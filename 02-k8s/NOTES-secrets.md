# 未导出的 Secret 与需要手工提供的值

本目录的 k8s 快照**不含任何明文密钥**。以下对象需要手工重建：

## 1. tetris-test/harbor-pull-secret (kubernetes.io/dockerconfigjson)

```bash
kubectl -n tetris-test create secret docker-registry harbor-pull-secret \
  --docker-server=harbor.local \
  --docker-username='robot$k8s-pull' \
  --docker-password='<Harbor robot$k8s-pull 的 secret>'
```
- 该 robot 在 Harbor 里当前 `duration=-1`（永不过期）
- **Jenkins 每次发布也会重建这个 secret**（Jenkinsfile 的 Deploy 阶段），所以只要 Jenkins 凭据 `harbor-credentials` 正确，它会自动恢复

## 2. tetris-test/smtp-secret (Opaque, key: password)

```bash
kubectl -n tetris-test create secret generic smtp-secret \
  --from-literal=password='<QQ 邮箱 SMTP 授权码>'
```
- 被 `monitoring` 里 alertmanager 的配置引用
- 注意: 该授权码在 Alertmanager 生成出来的配置里也是明文，轮换时两处都要更新

## 3. 其他

- `tetris-test/loadtest-script` ConfigMap: 压测脚本，可从 `03-test/loadtest.py` 重建：
  ```bash
  kubectl -n tetris-test create configmap loadtest-script \
    --from-file=loadtest.py=03-test/loadtest.py
  ```
- `argo-rollouts/argo-rollouts-notification-secret`: 当前为空，未配置通知
- Jenkins 凭据（不在 k8s 里）：
  - `harbor-credentials`: Username/Password → `robot$k8s-pull` + robot secret
  - `gitlab-ssh-key`: SSH 私钥，用于拉取/推送 GitLab
