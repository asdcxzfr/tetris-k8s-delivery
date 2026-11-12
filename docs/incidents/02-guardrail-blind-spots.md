# 02 护栏假绿：7 条自动回滚指标，6 条绿灯放行了坏版本

> **一句话**：自报型指标只能证明应用在正常推送指标，不能证明能用。一个在埋点之前就返回 500 的版本，会让所有自报指标保持沉默——而沉默在 `failureCondition` 里等于"没超标"，于是护栏全绿，流量推到 100%。

**领域**：金丝雀发布 / 观测设计 · **难度**：★★★★☆ · **状态**：已修复（新增外部黑盒探测）

## 现象

故意推一个坏镜像（开局接口必失败），预期是 AnalysisRun 失败、Rollout 自动 abort。实际：

- `AnalysisRun` 结果是 **Successful**
- 7 条指标里 **6 条绿**，只有 1 条擦边，快到临界值。
- 流量按 steps 一路推到 100%，坏版本吃满全部流量，最后靠人工发现。

证据：`04-evidence/guardrail-fix-evidence/analysisrun-BEFORE-Successful.yaml`

## 排查

先看 AnalysisRun 里每条 metric 的原始结果，而不是看它成功/失败的结论：

```
business-error-rate      → 查询结果为空
http-5xx-rate            → 查询结果为空
game-start-failure-ratio → 查询结果为空
app-up                   → 1（Pod 活着）
p95-latency              → 近似 0，无业务流量
```

四个业务指标同时为空，这就是全场唯一的线索：**不是没出问题，是根本就没有数据**。

## 根因

三层叠加，每一层都在放大下一层：

1. **护栏全部消费应用自报指标**。7 条里有 6 条查的是 Flask 埋点（`flask_http_request_*`、自定义 `tetris_*`），埋点写在请求处理路径里。坏版本在埋点执行之前就 return 500 或崩掉，于是指标根本不增长。
2. **查询为空 → 比较为 false**。`failureCondition: result[0] > 0.5` 在结果为空时判定为"未超标"，即通过。空集在护栏里被解释成了健康。
3. **没有业务流量时，流量类指标天然测不到可用性**。金丝雀阶段只有一个测试用流量，socket.io 长连接的业务流量不经过 HTTP 请求指标，于是 p95 也常年为 0。

`up`/就绪探针同样是自报视角的延伸：它只覆盖进程存活、端口在听，抓不到逻辑坏了但 `/metrics` 照常应答的情况。

## 修复

补一条**不依赖应用自报**的外部探测，作为第 7 条护栏（`gameplay-smoke`，job provider）：

```yaml
- name: gameplay-smoke
  provider:
    job:
      spec:
        backoffLimit: 0
        template:
          spec:
            restartPolicy: Never
            containers:
            - name: smoke
              image: curlimages/curl:8.10.1
              command: ["sh","-c"]
              args:
                - curl -sf -m 5 http://<canary-service>/healthz &&
                  curl -sf -m 5 -X POST http://<canary-service>/api/game/start
  failureCondition: status.failed > 0        # Job 失败即判异常
  failureLimit: 0
  consecutiveErrorLimit: 3
```

另外两处配套修正：

- **P95 用 `failureCondition` 而不是 `successCondition`**。`histogram_quantile` 在窗口内无观测时返回 `NaN`：`NaN > 0.5` 为 false（安全放过），而 `result[0] < 0.5` 会让它既不算成功也不算失败 → `Inconclusive` 卡在中间（见 10）。
- **数量型阈值要显式给零基准**，否则"没有新失败"和"没有数据"两种情况在 `increase(...) > 3` 里长得一模一样（见 06）。

## 验证

用同一个坏镜像重跑，整条链路在 **97 秒**内闭合：

```
BackoffLimitExceeded  →  MetricFailed  →  AnalysisRunFailed  →  RolloutAborted
```

`kubectl argo rollouts get rollout` 显示 `Status: ✖ Degraded`、`Message: RolloutAborted`，流量留在 stable。

## 教训

1. **护栏的可信度等于它最独立的那条指标**。只要全部指标都由被监控对象自己上报，坏版本导致的指标不上报就是一种合法的通过方式。
2. **空集必须显式处理**。每个 `failureCondition` 都要确认："查不到数据时算通过还是算失败？"，空集是特殊的处理方式。
3. **端到端探测不需要复杂**。一个 `curl` 制造实际的访问记录，比 6 条精心调参的 P95/5xx 指标加起来更能拦住坏版本。
