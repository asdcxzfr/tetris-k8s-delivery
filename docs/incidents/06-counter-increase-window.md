# 06 counter 的两个反向坑：重启归零导致漏报，abort 一次导致永久误报

> **一句话**：counter 的绝对值永远没有语义。`> 0` 这种写法在 counter 上同时踩两个坑——进程重启把它抹成 0（漏报），发生过一次它就再也不会回到 0（永久误报）。唯一正确的写法是"最近 N 分钟有没有新增"。

**领域**：PromQL / 告警正确性 · **难度**：★★★☆☆ · **状态**：已修复（残余一处已记录）

## 现象

第一版回滚告警这么写：

```promql
rollout_events_total{reason="RolloutAborted"} > 0
```

两种相反的错法先后出现：

1. **abort 过一次之后，这条告警永远 firing**：即使后来又发布了十次、全部健康，依旧会发送报警
2. 控制器 Pod 重启（发版频繁，控制器跟着滚动）后计数归零，**那一次的 abort 反而没报**

## 排查

先确认这个指标到底有哪些标签、哪些 reason：

```bash
kubectl -n monitoring port-forward svc/kube-prometheus-stack-prometheus 19090:9090 &
curl -sG 'http://127.0.0.1:19090/api/v1/labels' \
     --data-urlencode 'match[]=rollout_events_total' | python3 -m json.tool
```

标签：`name="tetris-game"`、`reason=...`、`type=...`、`exported_namespace="tetris-test"`。

关键发现：正常状态下，这个指标**只有** `reason="RolloutAddedToInformer"`（控制器启动事件），查询 `reason="RolloutAborted"` 返回 **EMPTY**，即绝大多数时间它根本不存在，而"不存在"和"值为 0"在 `> 0` 里表现一致。

## 根因

counter 的语义是"自进程启动以来的累计增量"：

- **单调递增**：一旦 abort 过一次，值 ≥ 1，之后新版本发布成功也不会让它回到 0，导致永久误报
- **随进程重启归零**：控制器滚动重启后从 0 重新计数 → 如果只看"当前值 > 0"，重启后的第一次事件可能刚好落在两次抓取之间导致漏报
- 更细的一层（已记录）：新 series **首次出现即值 1**，没有 0 基准，`increase()` 对"从无到有"的第一段增长计算会打折

## 修复

把"是否发生过"改写成"在某段时间内有没有新增"：

```yaml
- alert: TetrisRolloutAborted
  expr: increase(rollout_events_total{exported_namespace="tetris-test", reason="RolloutAborted"}[10m]) > 0
  for: 0m            # 事件型告警不需要 for，立刻发
```

`increase()` 一次解决两个问题：它只看窗口内的增量（永久 >0 的历史被窗口切掉），并且对计数器重置有专门处理（重置点不会算出负数或爆炸值）。

配套：`TetrisAnalysisFailed` 同理，用正则匹配两个 reason：

```yaml
expr: increase(rollout_events_total{exported_namespace="tetris-test", reason=~"AnalysisRunFailed|AnalysisRunError"}[10m]) > 0
```

注意 `=~` 是**完全锚定**的正则（`^(?:AnalysisRunFailed|AnalysisRunError)$`），不会误报 `AnalysisRunFailed2` 之类的相近值。

## 残余问题（已记录）

`rollout_events_total` 的 series 首次出现即 1，无 0 基准，所以控制器重启后的第一次事件仍可能漏掉。决策是**保留现状**，把 abort 检测的主力交给 `TetrisRolloutDegraded`（查 `rollout_info` 的 phase，是 gauge），事件型告警只作为补充。

## 验证

```bash
# 1. 持续观察：abort 一次后，10 分钟窗口过后告警应自行恢复
# 2. 直接看指标值随时间是否单调，以及重启后是否归零
curl -sG localhost:19090/api/v1/query_range \
     --data-urlencode 'query=rollout_events_total{reason="RolloutAborted"}' \
     --data-urlencode "start=$(date -d '-1 hour' +%s)" --data-urlencode "end=$(date +%s)" \
     --data-urlencode 'step=60'
```

## 教训

1. **counter 只回答"速率"，不回答"状态"**：任何 `counter > N` 的写法都值得怀疑，改用 `increase()`/`rate()` + 窗口。
2. **"值 > 0" 与 "序列存在" 是两件事**。`reason="RolloutAborted"` 这种按事件类型分出的 series，在没发生时不存于 TSDB。
3. 事件型告警设 `for: 0m`；状态型告警（phase、可用副本数）才需要 `for` 来抗抖动。混用会让两者都不可靠。
