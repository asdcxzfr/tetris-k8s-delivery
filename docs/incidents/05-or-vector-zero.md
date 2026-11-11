# 05 `or vector(0)`：同一行惯用法，在告警里救命，在护栏里放水

> **一句话**：`or vector(0)` 干的事只有一件——把"查不到数据"折叠成确定值 0。在告警里这个 0 会触发，是它的本意；在 AnalysisRun 的 `failureCondition` 里这个 0 不超标，等于给坏版本发通行证。

**领域**：PromQL / 告警与护栏语义 · **难度**：★★★☆☆ · **状态**：已按场景分别处理

## 现象

同一套 PromQL 片段（`... or vector(0)`）出现在两个地方，表现完全相反：

- `TetrisAppDown` 需要它才算可靠——没有它，整个 namespace 的 Pod 全没了的时候告警**不响**
- `analysis-template` 的 `p95-latency` 带上它之后，坏版本上线时护栏**全绿**

## 排查

先看 `or` 的语义：它是**集合并集**，`A or B` 的顺序是"左边有序列就用左边的，左边空集才补右边的"。所以 `or vector(0)` 的唯一作用就是：**把空结果变成一条值为 0 的序列**。

于是问题变成一句话：**这条查询为空时，代表什么？**

| 消费者 | 为空时的真实含义 | `or vector(0)` 的效果 |
|---|---|---|
| `sum(up{namespace="tetris-test"}) == 0` | 该 namespace 一个目标都没有 = 整体消失 | 0 == 0 → **firing**（正确，救回漏报） |
| `histogram_quantile(...) > 0.5`（failureCondition） | 窗口内没有观测 = 没有业务流量 | 0 > 0.5 为假 → **判定通过**（危险，放走坏版本） |

## 根因

`up`/`sum()` 这类聚合在**输入为空集时返回空集**，而不是 0：

```promql
sum(up{namespace="tetris-test"})        # 目标全没 → 空，== 0 也是空 → 告警永远不触发
sum(up{namespace="tetris-test"}) or vector(0)   # 空 → 0 → 0 == 0 → 触发
```

这是"指标消失"比"指标变差"更难发现的原因：聚合把空集吃掉了，规则连一行日志都不留。

而在护栏侧，空集被 `or vector(0)` 变成 0 之后，`failureCondition: result[0] > 0.5` 得到一个"没超标"的确定结论——**数据缺失被当成了健康**（与 02 是同一个坑的两面）。

## 修复

按"空集应当触发还是放行"分别处理，不要统一加 `or vector(0)`：

```yaml
# ① 告警：空集必须触发 —— 用 or vector(0) 兜住"整体消失"
- alert: TetrisAppDown
  expr: sum(up{namespace="tetris-test"}) == 0
  # 若不加 or vector(0)，namespace 全空时这条永远不响

# ② 护栏：空集不能放行 —— 用 absent() 或失败条件反转
- name: p95-latency
  failureCondition: result[0] > 0.5        # NaN/空 → 放过
  # 更严格的做法：
  # failureCondition: "isnan(result[0]) or result[0] > 0.5"
- name: business-error-rate
  failureCondition: "len(result) == 0 or result[0] > 0.05"
```

另一条更省心的原则：护栏里凡是"必须存在"的指标，就用 `absent()` 单独判一条，而不是指望每条 `failureCondition` 都记得处理空集。

## 验证

```bash
# 制造空集：把 namespace 的 Pod 全删，观察 TetrisAppDown 是否 firing
kubectl -n monitoring port-forward svc/kube-prometheus-stack-prometheus 19090:9090 &
curl -sG localhost:19090/api/v1/query --data-urlencode 'query=sum(up{namespace="tetris-test"})' 
curl -sG localhost:19090/api/v1/query --data-urlencode 'query=sum(up{namespace="tetris-test"}) or vector(0)'
```

第一条返回 `data.result: []`，第二条返回 `value: [.., "0"]`。

## 教训

1. **`or vector(0)` 不是"让查询更稳"的通用修饰符**，它是关于"空集代表什么"的一次语义决策，必须逐条判断。
2. **聚合函数会吞掉空集**（`sum`/`avg`/`max` 都是），饿死式故障因此天生难告警。
3. 护栏与告警对"缺数据"的期望方向相反：告警宁可误报，护栏宁可漏报——所以同一个手法不能复制粘贴。
