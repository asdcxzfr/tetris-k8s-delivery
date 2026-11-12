# 07 `rollout_info` 的 phase 词表 ≠ `Rollout.status.phase`：一条永远不触发的告警

> **一句话**：`rollout_info` 的 `phase` 标签值是控制器自己拼的字符串（本项目实测为 `Abort`），而 `Rollout` 对象的 `status.phase` 是另一套枚举（abort 后是 `Degraded`）。把两者当成同一个词表，写出来的告警永远不会响。

**领域**：Argo Rollouts / 指标语义 · **难度**：★★☆☆☆ · **状态**：已修复

## 现象

参考写的规则：

```promql
rollout_info{phase=~"Degraded|Aborted"} == 1
```

跑了一周，**一次都没触发过**。期间明明发生过 abort。

## 排查

`rollout_info` 是个 **gauge**，值恒为 1，阶段信息全在标签里。真实存在的标签值如下：

```bash
kubectl -n monitoring port-forward svc/kube-prometheus-stack-prometheus 19090:9090 &
curl -sG 'http://127.0.0.1:19090/api/v1/labels' \
     --data-urlencode 'match[]=rollout_info' | python3 -m json.tool
```

再对照 Rollout 对象本身的状态：

```bash
kubectl -n tetris-test get rollout tetris-game -o jsonpath='{.status.phase}{"\n"}{.status.message}{"\n"}'
# Degraded
# RolloutAborted
```

两边词表对不上：指标侧是 `Abort`（单数、无 ed），对象侧是 `Degraded`；`Aborted` 这个词在两侧**都不存在**。

另一个被忽略的点：`phase="Degraded"` 这条序列在 Rollout 健康时**根本不存在**（gauge 只暴露当前状态），左边为空 = 结果为空。

## 根因

`status.phase` 是 `RolloutPhase` 类型枚举（`Healthy` / `Degraded` / `Progressing` / `Paused` …），表示"这次发布整体处于什么阶段"；`abort` 是一个**动作**，落库时把 phase 推到 `Degraded`，同时把 `status.message` 写成 `RolloutAborted`。

而 `rollout_info` 的 `phase` 标签是控制器在打点时自己拼的字符串，与上一套枚举**不是同一份常量**（ `Abort`、`Paused`）。两套词表之间没有编译期或运行期的约束，写错了不会有任何报错，报警只会静默不触发。

`== 1` 在这里只是惯例性防御：标签选择器已经在序列层筛过一遍，值比较只在被选中的序列上生效，写不写结果都一样。

## 修复

测试真值：

```yaml
- alert: TetrisRolloutDegraded            # abort 后的主检测信号
  expr: rollout_info{exported_namespace="tetris-test", phase="Abort"} == 1
  for: 0m

- alert: TetrisRolloutStuck               # 卡在人工 gate
  expr: rollout_info{exported_namespace="tetris-test", phase="Paused"} == 1
  for: 15m
```

并且把"abort 检测"的职责从事件型 counter（见 06）迁到这里——gauge 不受重启归零影响，可靠性更高。

## 验证

```bash
# 造一次 abort，然后确认查询有结果
kubectl argo rollouts abort tetris-game -n tetris-test
curl -sG localhost:19090/api/v1/query \
     --data-urlencode 'query=rollout_info{phase=~".+"}' | python3 -m json.tool
```

## 教训

1. **指标里的枚举值必须测试确认真值**，任何来自文档、示例、记忆的字符串都可能是错的；`/api/v1/labels` 与 `?match[]=` 是成本最低的事实来源。
2. **同一份"状态"在 API 对象与指标里可能有两套词表**，跨边界引用时要显式核对，而不是靠命名相似。
3. gauge 只暴露**当前**状态：不存在的序列表示"没处于该状态"，不是"缺数据"，这与 counter 的判断逻辑正好相反。
