# 04 exported_namespace：写 `namespace="tetris-test"` 永远查不到的那条指标

> **一句话**：一条指标里可能有两个 `namespace`——对象自己带着的，和抓取目标所在的。默认 `honorLabels: false` 时后者赢，前者被改名成 `exported_namespace`。不知道这件事，你会以为"指标没采到"。

**领域**：Prometheus / PodMonitor · **难度**：★★★☆☆ · **状态**：已修复

## 现象

给 Argo Rollouts 控制器单独加了一个 PodMonitor（控制器 Pod 在 `default`，它管的 Rollout 在 `tetris-test`）。Grafana 里只零星看到几个 `rollout_*` 序列，写告警时：

```promql
rollout_events_total{namespace="tetris-test"}
```

**永远是空**。但 `rollout_events_total` 这个指标名是存在的。

## 排查

先确认指标存不存在、真实标签长什么样——不要靠猜：

```bash
kubectl -n monitoring port-forward --address 127.0.0.1 svc/kube-prometheus-stack-prometheus 19090:9090 &
curl -sG 'http://127.0.0.1:19090/api/v1/labels' \
     --data-urlencode 'match[]=rollout_events_total' | python3 -m json.tool
```

返回的标签里有 `namespace="default"`、`exported_namespace="tetris-test"`、`name="tetris-game"`、`reason=...`。

## 根因

一条指标在进入 TSDB 前会经过"标签合并"，两边的 `namespace` 撞名：

| 来源 | `namespace` 的值 |
|---|---|
| 指标自身（控制器在 `tetris-test` 里发布，业务标签自带） | `tetris-test` |
| 抓取目标（PodMonitor 选中的 Pod 在 `default`） | `default` |

`honorLabels: false`（默认）的处理规则是：**目标标签优先**——保留 `namespace="default"`，把指标自带的那个改名为 `exported_namespace`。这个改名的目的是不丢信息，但对查询的人是纯陷阱。

## 修复

告警规则里一律用 `exported_namespace`：

```yaml
expr: increase(rollout_events_total{exported_namespace="tetris-test", reason="RolloutAborted"}[10m]) > 0
```

应用自己的 PodMonitor（目标是 `tetris-test` 里的 Pod）不受影响，那条链路两边 namespace 都是 `tetris-test`，仍然写 `namespace=`。

另一种写法是 `honorLabels: true` 强制保留指标自带标签，但那样会把"目标在哪个 namespace"这个维度整个抹掉——不同 namespace 的同名目标会混成一条序列，反而更危险。

## 验证

```bash
kubectl -n tetris-test port-forward svc/<prometheus> 19090:9090 &
curl -sG 'http://127.0.0.1:19090/api/v1/query' \
     --data-urlencode 'query=count(rollout_events_total) by (exported_namespace)'
```

## 教训

1. **"指标名存在但查询为空"** 的第一反应应该是打 `/api/v1/labels` 看真实标签，而不是改查询条件试运气。
2. **`namespace` 不是唯一有这种冲突的标签**——`pod`、`instance`、`job`、`container` 都会在目标与指标之间撞名，冲突时的规则都是"目标赢，指标那一份加 `exported_` 前缀"。
3. 采集声明（PodMonitor）的 `namespaceSelector` 决定的是**去哪找 Pod**，不是**指标属于谁**。这两个概念在本项目里分居两个 namespace，是这个坑的温床。
