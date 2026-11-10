# Tetris on Kubernetes — 最终交付快照

> **说明 / Disclaimer**：本项目为个人 k8s 实验环境项目，仓库内的内网 IP（192.168.164.x）、主机名（harbor.local / tetris.local）与域名均属实验拓扑，不含生产凭据；数据库口令与邮件账号已改为环境变量 / 占位符。

> 生成时间: 2026-10-13 (UTC)
> 生成方式: **k8s 对象一律从集群实时导出**，不是磁盘上遗留的清单文件
> 集群: k8s v1.28.15 / 4 节点 (k8s-master .133, jenkins .131, harbor .132, gitlab .130)
> 源码版本: git `test-branch` @ **e9142b7**
> 线上镜像: `harbor.local/tetris-game/tetris-game:test-branch-36`
> Rollout 状态: `Healthy`  stable = current = `6fdfc574f6`

---

## ⚠️ 先读这一条

`/prometheus_Grafana/` 下原有的清单**已经过期，不要再用**：

| 旧文件 | 问题 |
|---|---|
| `monitor/anaylsis-template.yaml` | **文件名拼写错误的重复文件**，应与 `analysis-template.yaml` 二选一删除 |
| `monitor/analysis-template.yaml` | 09-21 的 6 指标版本；**线上已是 7 指标**（新增 `gameplay-smoke`、修好 `app-up`） |
| `monitor/prometheusrule-tetris-alerts.yaml` | 只有 7 条告警；**线上已 11 条** |
| `monitor/podmonitor-argo-rollouts.yaml` | `namespaceSelector` 仍指向 `default`；**线上已改为 `argo-rollouts`** |
| `argo_rollouts/rollout.yaml` | 10-09 版本，镜像/字段均落后于线上 |
| 各种 `*.bak-*` / `*.new` / `backup/` | 历史过程文件 |

**以本目录 `02-k8s/` 为准。**

---

## 目录结构

```
/root/tetris-delivery/
├── README.md                本文件: 总索引 + 复原步骤 + 变更记录
├── MANIFEST.txt             全部文件的清单 (大小 / md5)
├── 01-app/                  应用源码 (与 git test-branch@e9142b7 一致)
├── 02-k8s/                  集群生效对象的权威快照 (可直接 apply)
├── 03-test/                 测试与验证脚本
├── 04-evidence/             验证证据 (金丝雀护栏修复前后对照)
└── 05-backups/              变更前的备份 (可回滚)
```

---

## 01-app/ — 应用源码

| 文件 | 作用 |
|---|---|
| `app.py` | Flask + Flask-SocketIO 主程序；含业务指标埋点 (`GAME_START_FAILED.inc()`、`@socketio.on_error_default`) |
| `player_manager.py` | 玩家/游戏实例管理；`active_players` 计数与 `remove_player` |
| `data_storage.py` | SQLAlchemy + MySQL 持久化层 |
| `game_logic.py` | 俄罗斯方块核心逻辑 |
| `static/game.js` | 真实前端：`transports:['websocket','polling']`，只发 `auth`/`start_game`/`restart`/`move_*`，**从不发 `get_statistics`** |
| `Dockerfile` / `pip.conf` / `requirements.txt` | 镜像构建 |
| `Jenkinsfile` | CI/CD 流水线（构建→推 Harbor→**json patch 换镜像触发金丝雀**→等待金丝雀结果） |
| `docker-compose.yml` | 旧的本机 docker 部署路径（已停用） |

## 02-k8s/ — 集群生效对象

| 文件 | 对象 | 说明 |
|---|---|---|
| `rollout.yaml` | Rollout `tetris-test/tetris-game` | 金丝雀 steps: 10%→2m→30%→2m→60%→2m→100%；`analysis.startingStep=1`；resources `limits.memory=256Mi` / `requests.cpu=100m,memory=128Mi` |
| `services.yaml` | Service ×3 | `tetris-game-stable` / `tetris-game-canary`（选择器带 `rollouts-pod-template-hash`）、`tetris-game-service`（NodePort 8080:32459，只选 `app=tetris-game`） |
| `ingress.yaml` | Ingress `tetris-ingress` | host `tetris.local`（canary ingress 由 Argo 自动生成，未导出） |
| `hpa.yaml` | HPA `tetris-game-hpa` | min1/max3，CPU 50%，scaleUp +2/30s，scaleDown −1/60s stab 120s |
| `podmonitor-tetris.yaml` | PodMonitor | 采集应用 `/metrics` |
| `podmonitor-argo-rollouts.yaml` | PodMonitor | 采集 argo-rollouts；**`namespaceSelector` 必须是 `argo-rollouts`**（曾因指向 `default` 导致 argo 指标全断、4 条告警失效） |
| `analysistemplate-tetris-canary-analysis.yaml` | AnalysisTemplate | **7 条护栏**：`gameplay-smoke`(job provider) + `app-up` + `business-error-rate` + `game-start-failure-ratio` + `http-5xx-rate` + `p95-latency` + `exceptions` |
| `prometheusrule-tetris-app-alerts.yaml` | PrometheusRule | **11 条告警**（7 条原有 + 4 条内存/OOM/重启） |
| `alertmanagerconfig-tetris-alerts.yaml` | AlertmanagerConfig | 邮件通知路由 |
| `configmap-canary-smoke-probe.yaml` | ConfigMap | `smoke.py`；**`gameplay-smoke` 依赖它，不可删** |
| `configmap-grafana-tetris-dashboard.yaml` | ConfigMap | Grafana 看板 |

### 未导出的 Secret（需手工重建）

```bash
# 1) Harbor 拉取凭证（Jenkins 每次发布也会重建它）
kubectl -n tetris-test create secret docker-registry harbor-pull-secret \
  --docker-server=harbor.local \
  --docker-username='robot$k8s-pull' \
  --docker-password='<Harbor robot secret>'

# 2) SMTP（Alertmanager 邮件用）
kubectl -n tetris-test create secret generic smtp-secret \
  --from-literal=password='<QQ 邮箱授权码>'
```
> Harbor robot 账号 `robot$k8s-pull` 当前 `duration=-1`（永不过期）。
> 同项目的 `robot$test01` 仍是 30 天有效期且**已过期**，如不使用建议删除。

### 复原顺序

```bash
kubectl apply -f 02-k8s/services.yaml
kubectl apply -f 02-k8s/hpa.yaml
kubectl apply -f 02-k8s/podmonitor-tetris.yaml
kubectl apply -f 02-k8s/podmonitor-argo-rollouts.yaml
kubectl apply -f 02-k8s/configmap-canary-smoke-probe.yaml   # 必须在 AnalysisTemplate 之前
kubectl apply -f 02-k8s/analysistemplate-tetris-canary-analysis.yaml
kubectl apply -f 02-k8s/prometheusrule-tetris-app-alerts.yaml
kubectl apply -f 02-k8s/alertmanagerconfig-tetris-alerts.yaml
kubectl apply -f 02-k8s/ingress.yaml
kubectl apply -f 02-k8s/rollout.yaml
```

## 03-test/ — 测试脚本

| 文件 | 作用 |
|---|---|
| `loadtest.py` | 并发压测（websocket 优先）；输出 [A] `get_statistics` RTT、[B] `move_*→game_state` RTT、错误率；支持 `--connect-ramp` 错峰、`--probe-interval 0` 关探针 |
| `smoke.py` | 金丝雀业务冒烟探针（`gameplay-smoke` Job 使用）：对金丝雀发 N 个客户端，开局成功率不达标即退出非 0 |
| `probe.py` | 对照探针（同时打 canary 和 stable） |
| `selfheal-test.sh` | 自愈测试（`pod` / `int` 两种故障模式） |

## 04-evidence/ — 验证证据

| 文件 | 说明 |
|---|---|
| `guardrail-fix-evidence/analysisrun-FAILED.yaml` | 修复后：坏版本被 `gameplay-smoke` 拦下（AnalysisRun Failed，97 秒） |
| `guardrail-fix-evidence/analysisrun-BEFORE-Successful.yaml` | 修复前：同一个坏版本 6 条护栏全绿并 promote 到 100% |
| `guardrail-fix-evidence/analysistemplate-v2.yaml` | 修复后的 AnalysisTemplate |
| `guardrail-fix-evidence/events.txt` | `MetricFailed` / `AnalysisRunFailed` / `RolloutAborted` 事件流 |

## 05-backups/ — 变更前备份

| 文件 | 说明 |
|---|---|
| `at-backup-*.yaml` | 修改前的 AnalysisTemplate（6 指标版） |
| `tetris-app-alerts-backup-20261013-095945.yaml` | 加 4 条内存/OOM 告警前的 PrometheusRule（7 条） |
| `tetris-app-alerts-backup-before-appdown-*.yaml` | 修 `TetrisAppDown` 前的 PrometheusRule |

---

## 本轮改动记录

### 已完成

| # | 改动 | 证据 |
|---|---|---|
| 1 | 应用埋点：`GAME_START_FAILED.inc()` + `@socketio.on_error_default`（socket 错误不再全盲） | 容器内源码确认；commit `4aae01c` |
| 2 | 护栏新增 `gameplay-smoke`（job provider 黑盒冒烟），分析期间真的对金丝雀打一局游戏 | 坏版本 97 秒被拦；好版本 10/10 PASS |
| 3 | 修复 `app-up` 误报：去掉 `or vector(0)`，`initialDelay` 60s→120s | 历史两次误判不再复现 |
| 4 | 修复 `TetrisRolloutDegraded`（critical）：`phase=~"Degraded\|Aborted"` → **`phase="Abort"`** | 回溯：该条件在 abort 期间为真 8.5 分钟 > `for:2m` |
| 5 | 修复 `TetrisAppDown` 误报：`up{...}==0` → **`sum(up{...})==0`** | 3 小时窗口误报 3 次 → 0 次 |
| 6 | 新增 4 条告警（内存接近 limit / OOMKilled / 频繁重启 / cgroup OOM） | 全部 `health=ok`，告警总数 7→11 |
| 7 | Jenkinsfile 打开"等待金丝雀结果"：金丝雀失败即构建标红 | build 36 实测 `FAILURE`，日志 `PHASE=Degraded → exit 1` |
| 8 | CI/CD 修复：Harbor robot 过期（30 天到期）→ 重建为 `duration=-1` | build 35 全绿，金丝雀自动触发 |
| 9 | 修复 PodMonitor 采集回归（`namespaceSelector` 指向已删除的 `default` 控制器） | argo 指标恢复：`rollout_*` 34 系列 |
| 10 | 清理：双 argo-rollouts 控制器（split-brain）、悬空 ClusterRoleBinding、残留测试 Pod/Job | 见会话记录 |

### 已知未修（有意保留）

**代码层**（需出新镜像）
- `get_statistics()` 4 条查询含 2 次全表 `.all()`（空闲时 P50 已 112ms）
- `create_player` 的全局锁跨 DB 查询
- `remove_player` 的 `active_players -= 1` 位于 3 段无保护 DB 写之后
- 每个 tick 新建 `threading.Timer`（线程峰值 492）
- `GAME_OVER.inc()` 从不自增（死指标）
- `app.py` 里 5 个 handler 重复注册（`move_down`/`move_left`/`rotate` 各 2 次）

**资源配额**（按用户决定不改动）
- `limits.memory=256Mi`（实测峰值 251–253Mi → 曾 OOMKilled）
- `requests.cpu=100m`（实测峰值 1380m）→ HPA `averageUtilization:50` 形同虚设
- HPA 只按 CPU、不看内存；`maxReplicas=3`（容量上限约 60 玩家）

**告警**
- `TetrisRolloutAborted` / `TetrisAnalysisFailed` 基于 `increase(rollout_events_total[...])`，而该 counter 的 series **首次出现即值 1**（无 0 基准）→ **会漏掉控制器重启后的第一次事件**。已决定保留现状，abort 检测主力交给 `TetrisRolloutDegraded`
- `http-5xx-rate` / `p95-latency` / `exceptions` 三条护栏只能看到就绪探针（业务流量走 socket.io，`flask_http_request_*` 看不见）

**运维杂项**
- Harbor robot `robot$test01` 已过期未处理；Harbor robot 到期无提醒机制
- Harbor 有 untagged artifact 待 GC
- Harbor 的 "harbor-log 启动顺序" 问题未根治（挂了需手工 `cd /opt/harbor && docker compose up -d`）
- `gitlab` 节点内存 88%（GitLab 自身占用，已确认无需处理）

---

## 关键设计要点（复用时的注意事项）

1. **Argo Rollouts 是 CRD，不支持 strategic-merge patch**；换镜像必须用 `kubectl patch --type=json` 精确指定 `/spec/template/spec/containers/0/image`，否则 merge patch 会整体覆盖 containers 列表（丢掉 env/ports）。
2. **`rollout_info` 的 phase 标签值是 `Abort`（单数）**，而 Rollout 对象的 `status.phase` 是 `Degraded`——两者不是同一套词表，写告警时别混。
3. **自报型指标（应用自己上报）天然抓不住"绕过埋点"的实现缺陷**；拦能力来自外部黑盒探测（`gameplay-smoke` Job）。
4. **金丝雀分析期间若没有业务流量，任何"流量类"指标都测不到业务是否可用**——所以必须有主动探针。
5. **长连接（websocket）× HPA**：扩容只对新连接有效，已连接用户不受益。
6. **Harbor robot 有有效期**，静默过期会让流水线在 `docker login` 阶段莫名失败；建议统一 `duration=-1` 或加到运维备忘。


---

## 99-tools/ — 快照生成工具

| 文件 | 作用 |
|---|---|
| `export-clean.py` | 把 `kubectl get <obj> -o yaml` 的输出清洗成可复现的清单：去掉 `managedFields`/`resourceVersion`/`uid`/`creationTimestamp`/`generation`/`status`/`ownerReferences`，并剔除 `kubectl.kubernetes.io/last-applied-configuration` 等噪声注解 |

**⚠️ 这个脚本里有一个必须保留的关键点**：它对 YAML 1.1 有歧义的字符串强制加引号。

原因：PyYAML 刻意**不**把单字母 `y`/`Y`/`n`/`N` 解析为布尔值（为了避开 "Norway problem"），所以 `yaml.safe_dump` 不会给字符串 `N` 加引号；但 **Kubernetes 用的 go-yaml 是完整 YAML 1.1，会把裸写的 `N` 当成 `false`**：

```
error: spec...containers[0].env[1].name: Invalid value: "boolean":
       ...name in body must be of type string: "boolean"
```

这个坑在本项目里**踩了两次**（手写 AnalysisTemplate 时一次、用 PyYAML 导出快照时一次），所以脚本用自定义 `representer` 显式加引号堵住它。

重新生成快照：

```bash
for spec in "tetris-test/rollout/tetris-game" "tetris-test/analysistemplate/tetris-canary-analysis" ...; do
  IFS=/ read ns kind name <<< "$spec"
  kubectl -n "$ns" get "$kind" "$name" -o yaml > /tmp/raw.yaml
  python3 99-tools/export-clean.py /tmp/raw.yaml 02-k8s/<输出文件名>.yaml
done
```

---

## 快照校验结果（生成时实测）

**校验 A — 可应用性**：`02-k8s/` 下全部 11 个文件 `kubectl apply --dry-run=server` **全部通过，0 个失败**。

**校验 B — 忠实性**：把「服务端解析该文件后得到的 `spec`」与「集群实时对象的 `spec`」逐字段比对：

| 文件 | 结果 |
|---|---|
| `rollout.yaml` | MATCH |
| `analysistemplate-tetris-canary-analysis.yaml` | MATCH |
| `prometheusrule-tetris-app-alerts.yaml` | MATCH |
| `alertmanagerconfig-tetris-alerts.yaml` | MATCH |
| `services.yaml`（3 个 Service 逐文档） | MATCH |
| `hpa.yaml` | MATCH |
| `podmonitor-tetris.yaml` | MATCH |
| `podmonitor-argo-rollouts.yaml` | MATCH |
| `configmap-canary-smoke-probe.yaml` | MATCH |
| `ingress.yaml` | MATCH |

即：**本快照不是"磁盘上那份旧清单"，而是线上真实生效状态的可复现副本。**

**校验 C — 关键内容抽查**

- `rollout.yaml`：`image: ...:test-branch-36`、`limits.memory: 256Mi`、`requests.cpu: 100m,memory: 128Mi`
- `analysistemplate-*.yaml`：**7 条指标**（`gameplay-smoke` 为 job provider，其余 6 条 prometheus）
- `prometheusrule-*.yaml`：**11 条告警**（`TetrisAppDown` 已是 `sum(up{...}) == 0`；`TetrisRolloutDegraded` 已是 `phase="Abort"`）
- `podmonitor-argo-rollouts.yaml`：`namespaceSelector.matchNames: [argo-rollouts]`
