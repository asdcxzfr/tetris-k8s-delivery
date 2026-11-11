# 09 告警的三种静默失败：不报错、没日志、没邮件

> **一句话**：Alertmanager 的整条链路里，"配置合法"和"能送达"是两件独立的事。命名空间放错、severity 拼错、根路由被 operator 加了条件——三种错法都不会报错，告警只是**安静地掉进黑洞接收器**。

**领域**：Alertmanager / 告警路由 · **难度**：★★★★☆ · **状态**：已修复

## 现象

`kubectl apply` 成功、`check-config` SUCCESS、Prometheus 里 `ALERTS` 有数据，但**邮件一封不发**。翻 Alertmanager 日志，什么都没有。

## 排查

不要等邮件，直接看 operator 生成的**最终配置**（这是唯一事实）：

```bash
kubectl -n monitoring get secret alertmanager-kube-prometheus-stack-alertmanager-generated \
  -o go-template='{{index .data "alertmanager.yaml.gz"}}' | base64 -d | gunzip | head -40
```

期望在里面看到自己写的 matcher；如果没有、或者被包了额外条件，就是路由没对上。

再看告警此刻的路由归属：

```bash
kubectl -n monitoring port-forward svc/kube-prometheus-stack-alertmanager 9093:9093 &
curl -s localhost:9093/api/v2/alerts | python3 -m json.tool
```

`status.inhibitedBy` / 落到的 receiver 一目了然。

## 根因（三种静默失败的共同特征：合法但不生效）

### ① 命名空间放错 → 落进 `null` 黑洞

`AlertmanagerConfig` 放在哪个 namespace，operator 就会给它生成的子路由**自动**加上一条 `namespace="<那个 ns>"` 的 matcher。而 `PrometheusRule` 里每条规则都显式打了 `namespace: tetris-test`。

两边的 namespace 必须一致，否则：告警被 Prometheus 正常推来 → 匹配不上任何子路由 → 落到全局根路由的默认 receiver → 那是 operator 造的黑洞 `"null"` → 丢弃。

### ② severity 拼错或漏写 → 永远匹配不上

`severity=critical` 在编译时变成**完全锚定的正则** `^(?:critical)$`：

- 大小写敏感：`Critical` ≠ `critical`
- `severity=~"warning|info"` 不会意外匹配 `warning2`（这是好事）
- 但**漏写 `severity` 标签**的告警，其 `severity` 是空字符串，任何 `severity=...` 的 matcher 都不匹配 → 又是黑洞

### ③ 根路由被"想当然"

Alertmanager 真实路由此刻是一棵树，全局只有一个根 `route`，而 `AlertmanagerConfig.spec.route` **不是**那棵树的根——operator 会把它挂到全局根下面，作为第一级子路由。全局根有三条硬校验（`amtool check-config`）：

1. 不能有 `matchers`（它必须收下所有告警，不能筛）
2. 必须有 `receiver`
3. 不能有 `continue`

而它的 receiver 是 operator 固定的 `"null"`，不可控。`null` 也不是 Alertmanager 的内置关键字，只是一个"什么都不做"的黑洞接收器的名字——而且**必须加引号**：`- name: null` 会被 YAML 解析成空值，直接 `FAILED: missing name in receiver`。

## 修复

```yaml
apiVersion: monitoring.coreos.com/v1alpha1
kind: AlertmanagerConfig
metadata:
  name: tetris-alerts
  namespace: tetris-test          # ★ 必须与 PrometheusRule 里的 namespace 一致
spec:
  route:
    receiver: 'tetris-email'
    groupBy: ['alertname', 'namespace']
    matchers:
      - name: app
        value: tetris-game
    routes:
      - matchers:
          - name: severity
            value: critical
        receiver: 'tetris-email'
        repeatInterval: 1h        # 覆盖父级的 4h
```

三条经验：

1. **`app` / `namespace` / `severity` 三个标签要"三处一致"**：规则里的 labels、AMC 的 matchers、接收器名字。
2. **自定义等级要"三处配行为"**：`severity: urgent` 只是给标签换了个字符串，Alertmanager 不认识它——必须同时在**路由**（子路由 matcher）和**抑制**（`inhibitRules`，注意字段是单数 `sourceMatch`/`targetMatch`）两处补规则，否则它只是一个装饰性标签，既不催更、也不抑制。
3. **默认 4 条抑制规则只认 critical/warning/info**（critical 压同名 warning/info；warning 压同名 info；`InfoInhibitor` 压同 ns 的 info；任何告警压 `InfoInhibitor` 自身）。想要 `urgent > critical` 这种顺序，必须自己写 `inhibitRules`——**Alertmanager 没有"比大小"的能力**，它只做字符串匹配。

## 验证

```bash
# 1) operator 有没有合进去
kubectl -n monitoring exec alertmanager-kube-prometheus-stack-alertmanager-0 -c alertmanager -- \
  sh -c 'grep -n -A 6 "urgent" /etc/alertmanager/config_out/alertmanager.env.yaml'

# 2) 用本地 sink 验证投递链路（不依赖真实邮箱）
#    起一个把 POST 打到日志的 http server，临时规则 expr: vector(1) 立刻触发

# 3) 抑制是否生效
curl -s localhost:9093/api/v2/alerts | python3 -m json.tool | grep -A3 inhibitedBy
```

## 教训

1. **告警系统最常见的故障模式是"静默丢弃"**，不是发送失败——所以验证必须落在"最终生成的配置"和"接收端收到了什么"两个端点上。
2. **每次改配置后先 `check-config`，再看 `config_out`，最后才等邮件**。等邮件是在拿时间赌一条看不见的链路。
3. 标签是 Prometheus 与 Alertmanager 之间唯一的契约：任何一边拼写、命名空间、大小写不一致，结果都是"什么都没发生"。
