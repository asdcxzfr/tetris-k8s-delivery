# 原始素材说明（未入库）

## 为什么这里的 docx 没有提交进仓库

原始踩坑记录是本人在项目过程中随手写下的 Word 文档：

| 项目 | 数值 |
|---|---|
| 体积 | **17.32 MB** |
| 段落 | 4706 |
| 截图 | **279 张 PNG**（平均 61 KB，最大 563 KB） |
| 表格 | 0 |
| 结构 | 全部是 `Normal` 正文流，没有标题层级（无 H1/H2 样式） |

三个理由决定它不适合直接进 git：

1. **体积**：17 MB 二进制进 git 后每次修改都会新增一份完整副本，仓库会迅速膨胀；GitHub 单文件推荐上限 50 MB，虽未超，但已经远超"一个文档仓库该有的样子"。
2. **不可检索**：没有标题层级，无法生成锚点、目录或 diff；`git log -p` 对它完全失效。
3. **含未处理敏感信息**（见下），直接入库等于把实验环境的口令推到远端。

所以仓库里放的是**结构化重写版**：[`../incidents/`](../incidents/) 的 12 篇 Markdown——它们是从这份原始记录里提炼、复验、脱敏后的结果，也是唯一需要维护的版本。

## 原始记录里的敏感项（入库前必须处理）

以下是扫描原文档得到的真实命中，**写文档时一律不引用**，需要时改用占位符：

| 类别 | 位置（段落号） | 示例形态 |
|---|---|---|
| Harbor 管理员口令（base64） | p477 | `harborAdminPassword` 使用 base64 值 |
| GitLab 初次部署口令 | p604 / p607 | `--set auth.postgresPassword=<redacted>`、`auth.password=<redacted>` |
| 内存存储口令 | p631 | `postgres-password: "<redacted>"` |
| MinIO / S3 凭证 | p1189 / p1280 | `aws_secret_access_key: <redacted>` |
| 告警收件人邮箱 | p3875–3878 | 两个真实 QQ 邮箱（from / to） |
| 内网地址 | 全文多处 | `192.168.164.x`、`192.168.152.141` |
| URL 内嵌凭据 | p2256 | `http://root:<token>@...`（文档中是占位符模板，仍建议替换） |

另有一类**误报**：文中大量 `-p`、`--password` 字样的命令示例被正则捕获，实际是空值或占位符，无需处理。

统计口径：密码字面量 7 处、token/secret 21 处、邮箱 14 处、内网 IP 56 处。

## 如果要公开原始记录

按这个顺序做，每一步都可回退：

1. **复制一份**再动，原件保留在本地（本文档不入库，所以 Git 里不构成泄露）。
2. 用 Word 的"查找替换"处理上表每一类；邮箱统一替换为 `alert@example.com`。
3. 内网 IP 可保留（属实验拓扑，不构成风险），但需在文档开头加一句免责声明。
4. **压缩图片**：279 张截图占 17 MB 的绝大部分。Word 自带 `图片格式 → 压缩图片 → 删除裁剪区域 + 150 ppi`，通常能降到 3–5 MB。
5. 若仍要入库，考虑只提交**导出的 Markdown + 精选 10–20 张截图**（`docs/incidents/` 里已经在做这件事），原 docx 放在 Release 附件或对象存储。

## 与 `docs/incidents/` 的对应关系

| 原始记录里的线索 | 提炼后的篇目 |
|---|---|
| etcd/apiserver 反复重启、mirror pod、`config.seen` | [01](../incidents/01-mirror-pod-config-seen.md) |
| 金丝雀坏版本被放行、AnalysisRun 全绿 | [02](../incidents/02-guardrail-blind-spots.md) |
| PVC 绑错卷、`storageClassName` 为空 | [03](../incidents/03-pvc-wrong-binding.md) |
| `namespace` 标签查不到、`exported_namespace` | [04](../incidents/04-exported-namespace.md) |
| `or vector(0)`、护栏放水 | [05](../incidents/05-or-vector-zero.md) |
| `rollout_events_total` 告警复活/漏报 | [06](../incidents/06-counter-increase-window.md) |
| `rollout_info` phase 对不上 | [07](../incidents/07-rollout-info-phase.md) |
| abort 之后镜像还是新的 | [08](../incidents/08-abort-is-not-rollback.md) |
| 邮件不发、AlertmanagerConfig 不生效 | [09](../incidents/09-alertmanager-silent-failure.md) |
| P95 恒为 4.75ms、直方图桶 | [10](../incidents/10-histogram-buckets.md) |
| 导出的 YAML `name: N` 变成布尔 | [11](../incidents/11-yaml-boolean-norway.md) |
| 自愈测试 kill 完没重启 | [12](../incidents/12-pid1-signals.md) |
