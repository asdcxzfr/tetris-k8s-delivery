# 10 直方图的三个陷阱：桶是累积的、irate 会漏、NaN 既不是通过也不是失败

> **一句话**：`histogram_quantile` 是"插值估计"而不是"实测分位数"。它默认的 15 个桶决定了分辨率，`irate` 会漏掉抓取间隙的全部流量，而窗口内无观测时它返回 `NaN`——这个 `NaN` 在 `failureCondition` 与 `successCondition` 里的含义完全相反。

**领域**：PromQL / 延迟测量 · **难度**：★★★☆☆ · **状态**：认知校准（规则已按此写）

## 现象

三个互不相关、但根子都在"不变量"上的困惑：

1. P95 稳定显示 4.75ms，但端到端压测里明显有几百毫秒的慢请求
2. 同一个业务异常，`rate(...[30s])` 报 0，`increase(...[10m])` 报 6.857
3. 金丝雀分析偶尔卡住不动，不通过也不失败，最后超时 `Inconclusive`

## 排查

先看应用到底暴露了多少桶：

```bash
kubectl -n tetris-test exec deploy/tetris-game -- \
  sh -c 'wget -qO- localhost:8000/metrics | grep "^flask_http_request_duration_seconds_bucket"'
```

应用没传 `buckets=` 参数，用的是 Flask exporter 默认的 15 个桶（5ms 到 10s）。再对同一个请求序列分别数一下：

```promql
# 只有 14 个 —— 说明没有请求超过 5ms
flask_http_request_duration_seconds_bucket{le="0.005"}
# 15 个 —— 说明有一个请求落在 (5ms, 10ms]
flask_http_request_duration_seconds_bucket{le="0.01"}
```

## 根因

### ① 累积语义：`le` 是"小于等于"，不是"落在这一档"

每个 `_bucket{le=x}` 的值是**从最慢到 x 为止的累计计数**，所以对桶做 `rate` 得到的是"耗时 ≤ x 的请求速率"，而不是"落在这一档的速率"。真正落在某一档的数量要靠相邻两项相减。

`le="+Inf"` 的桶恒等于 `_count`——这既是校验点（`+Inf == _count` 不成立说明序列被错误聚合过），也是 `histogram_quantile` 能工作的基础：它需要完整的累积分布。

### ② 必须 `by (le)`，否则算的不是分位数

```promql
histogram_quantile(0.95, sum by (le) (rate(flask_http_request_duration_seconds_bucket[5m])))
```

`histogram_quantile` 靠 `le` 这个标签把一组序列认成"一个直方图"，然后在这组累积计数上做**线性插值**。丢了 `le`，它只会把一对数字当成一个只有两档的分布——不报错，结果错得离谱。

插值的直接后果：全部请求都落在首个桶（≤5ms）时，它只能在 0 到 5ms 之间线性估值，于是 P95 恒为 **4.75ms**。这个数不是测量值，是插值产物——分辨率被桶的粒度锁死。要看清长尾，必须自定义桶（例如 `buckets=[.005,.01,.025,.05,.1,.25,.5,1,2.5,5]`）。

### ③ `_bucket` / `_sum` / `_count` 全都是 counter

它们只因进程重启而归零，所以原始值毫无意义，必须配 `rate()` / `increase()`。而 `rate()` 会**外推到窗口边界**：

- 抓取间隔 15s，`[120s]` 窗口内只有约 105s 的实际样本跨度 → 6 个请求被外推成 6.857
- 这类非整数值是外推产物，不是"半个请求"

`irate()` 更危险：它只取窗口内最后两个样本。如果 6 个请求全部落在两次抓取之间，`irate` 直接返回 **0**——不是漏一点，是整段漏掉。所以 `irate` 只适合人眼看趋势，**不能做阈值判断**（护栏/告警里禁用）。

### ④ `NaN`：`failureCondition` 与 `successCondition` 的语义相反

窗口内完全无观测时，`histogram_quantile` 返回 `NaN`：

- `failureCondition: result[0] > 0.5` → `NaN > 0.5` **为 false** → 判定"没失败" → 安全放过（这是护栏该有的行为）
- `successCondition: result[0] < 0.5` → `NaN < 0.5` 也为 false → 既不算成功也不算失败 → 分析一直等到超时 → `Inconclusive`

结论：**护栏侧用 `failureCondition` 表达"坏"，不要用 `successCondition` 表达"好"**。前者在没数据时沉默，后者在没数据时卡死。

## 修复

```yaml
# 1) 护栏：只写 failureCondition，让它对缺失数据免疫
failureCondition: len(result) == 0 || result[0] > 0.5

# 2) 用量一律走 rate + increase 窗口
sum by (le) (rate(flask_http_request_duration_seconds_bucket{...}[5m]))

# 3) 校验聚合没被搞坏
flask_http_request_duration_seconds_bucket{le="+Inf"} == flask_http_request_duration_seconds_count
```

## 验证

```bash
# 对同一时间点，用直方图算的 P95 与"直接用 sum/count 的均值"对比，量级不应差一个数量级
curl -sG localhost:19090/api/v1/query --data-urlencode \
  'query=histogram_quantile(0.95, sum by (le) (rate(flask_http_request_duration_seconds_bucket[5m])))' \
  | python3 -m json.tool
```

## 教训

1. **分位数是估计出来的**：桶的边界就是分辨率上限，4.75ms 这种"太整齐"的数字往往是插值信号。
2. **counter 语义要一路贯彻到直方图**：`_bucket`/`_sum`/`_count` 都不能裸读，`irate` 不能做阈值。
3. **缺失数据的默认行为要显式选**：`NaN` 在 `>` 与 `<` 两侧的表现相反，护栏要么 fail-open（用 failureCondition），要么显式判空，别指望它自己"安全"。
