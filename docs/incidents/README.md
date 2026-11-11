# 踩坑复盘索引

本目录收录本项目（Tetris-on-k8s：Argo Rollouts 金丝雀 + 自动回滚 + 可观测性）在真实运行中踩到的 12 个坑。
每篇都是**现象 → 排查 → 根因 → 修复 → 验证 → 教训**的固定结构，命令均可直接复制。

这些不是教程式的"应该怎么做"，而是"我这么做了，然后错了，错在哪"。
所有篇目都经过了事后复验；涉及口令、邮箱、内网地址的部分已脱敏或占位。

## 篇目

| # | 篇名 | 一句话结论 | 领域 |
|---|---|---|---|
| 01 | [控制平面"自毁循环"](01-mirror-pod-config-seen.md) | kubelet 的 mirror pod 与 static manifest 的 `config.seen` 错位，对账每 ~5.5 分钟杀一次 apiserver | kubelet / 静态 Pod |
| 02 | [护栏假绿](02-guardrail-blind-spots.md) | 7 条自动回滚指标里 6 条对坏版本全绿，唯一生效的那条是"应用起来就 200"的冒烟探针 | Argo Rollouts Analysis |
| 03 | [5Gi 抢走 10Gi](03-pvc-wrong-binding.md) | `storageClassName` 留空 = 在全体无 class 的 PV 池里抽签，按名字字母序 | 存储 / PV 绑定 |
| 04 | [`exported_namespace`](04-exported-namespace.md) | 不是标签丢了，是 target 标签赢了并把它改名成 `exported_namespace` | Prometheus relabel |
| 05 | [`or vector(0)`](05-or-vector-zero.md) | 同一行惯用法：告警里防沉默，护栏里放水——它把"没有数据"变成了"数据是 0" | PromQL |
| 06 | [counter 的两个反向坑](06-counter-increase-window.md) | `counter > 0`：重启归零就漏报，abort 过一次就永久误报 | PromQL / 告警 |
| 07 | [`rollout_info` 的 phase 词表](07-rollout-info-phase.md) | 指标侧是 `Abort`，对象侧是 `Degraded`，写成 `Aborted` 的告警永远不触发 | 指标语义 |
| 08 | [abort ≠ 回滚](08-abort-is-not-rollback.md) | abort 只停流量与流程，`spec.template` 里的新镜像还在 | 渐进式交付 |
| 09 | [告警的三种静默失败](09-alertmanager-silent-failure.md) | 命名空间放错、severity 拼错、根路由被加条件——都不报错，只是安静丢弃 | Alertmanager |
| 10 | [直方图的三个陷阱](10-histogram-buckets.md) | 桶累积、`irate` 会漏、`NaN` 在 `>` 与 `<` 两侧含义相反 | PromQL / 延迟 |
| 11 | [`N` 变成 `false`](11-yaml-boolean-norway.md) | PyYAML 不给 `N` 加引号，go-yaml 把裸 `N` 读成布尔；同一个坑踩了两次 | YAML 1.1 |
| 12 | [PID 1 收不到 SIGTERM](12-pid1-signals.md) | 内核不给 PID 1 默认信号处置，`kill -9 1` 返回成功但毫无效果 | Linux / 容器 |

## 怎么读

- **只想看结论**：每篇开头的 `> 一句话` + 末尾 `## 教训` 三条。
- **要复用配置**：直接看 `## 修复` 里的 YAML / PromQL，均已在本仓库 `02-k8s/` 落地。
- **要排查同类问题**：`## 排查` 里的命令是按"先看事实、再看假设"排的，顺序即方法论。

## 关联材料

- 集群生效对象与告警规则：仓库 `02-k8s/`
- 失败与修复的原始 evidence：仓库 `04-evidence/`
- 自愈与护栏测试脚本：仓库 `03-test/`
- 原始踩坑记录（Word，17 MB，含 279 张截图）：未入库，说明见 [`../raw/README.md`](../raw/README.md)
