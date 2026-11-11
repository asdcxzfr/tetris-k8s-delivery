# 01 控制平面"自毁循环"：mirror pod 的 config.seen 与本地清单错位

> **一句话**：kubelet 杀的不是"坏 Pod"，而是"和它记忆不一致的 Pod"。静态 Pod 的身份有两条来源——磁盘上的 manifest 与 etcd 里的 mirror pod；`config.seen` 就是这两条来源对表用的时间戳。一旦错位，一个启动完全健康的 etcd 会被每 20 秒 SIGTERM 一次。

**领域**：控制平面 / kubelet · **难度**：★★★★★ · **状态**：原地未修复（换干净 VM 后消失）

## 现象

控制平面进入无休止的自杀循环，四个静态 Pod 轮回重启：

- etcd 每次启动都**完全正常**：恢复数据 → 通过损坏检查 → 当选 leader → 监听 2379/2380/2381 → `/health` 返回 `200 {"health":"true"}`（2ms）
- 然后 **7 秒到 20 秒**后被 SIGTERM（containerd 日志：`Stop container ... with signal terminated`）；有一次撑到 3 分 38 秒，说明**不是定时器，是事件驱动**
- apiserver 因 `127.0.0.1:2379 refused` + `PostStartHook "start-service-ip-repair-controllers" failed` 后 fatal 退出——这是连锁反应，不是病因
- scheduler 被 **liveness 探针**杀掉（`SyncLoop (probe) status="unhealthy"`），因为它选不出 leader（选举要靠 apiserver）
- 重启计数：scheduler 179 → 185 → 211，controller-manager 188 → 218 → 220，apiserver 14 → 66，etcd 12 → 47

## 排查

按"先证明谁在杀"的顺序做减法，每一步都要能排除一类假设：

| 假设 | 验证手段 | 结果 |
|---|---|---|
| 探针配错 | 直接 `curl /health` | 200，排除 |
| 容器 spec 抖动 | 对比 `io.kubernetes.container.hash` 注解 | 两次都是 `16e8ea2f`，排除 |
| 沙箱不健康 | 查 sandbox 状态 | `SANDBOX_READY`，排除 |
| 有进程在改 manifest | 看 `/etc/kubernetes/manifests/*` 的 mtime | 恒定，排除 |
| IPv6 回环坏 | apiserver 日志有 `Get https://[::1]:6443 connection refused` | 实测 `::1` 通、双栈监听，排除 |

决定性一步是把 kubelet 日志调到 v4。注意：写 kubelet 配置文件**没生效**，必须用 systemd drop-in 加 `--v=4` 再重启 kubelet。拿到的那一行是：

```
Pod sandbox changed, it will be killed and re-created.
```

触发者是 `SyncLoop (RECONCILE) source="api"`——**API 侧**的 Pod 对象和本地静态 Pod 不一致。

然后把 etcd 里的 mirror pod 挖出来对比。趁 etcd 短暂在线时直接读 etcd：

```
/registry/pods/kube-system/etcd-k8s-master
```

mirror pod 里 `192.168.164.133` 出现 **10 次**，而当前 manifest 只有 **6 次**。多出来的 4 次，正是更早的 `sed -i 's/host: 127.0.0.1/host: 192.168.164.133/g' /etc/kubernetes/manifests/etcd.yaml` 留下的旧版本（改探针 host）。mirror 上的注脚是 `config.seen: 11:01:31`。

## 根因

kubelet 对每个静态 Pod 做"对账"：把 API 里 mirror pod 的 spec 与磁盘 manifest 逐字段比，并把**它最后一次看到该配置的时间**记成 `config.seen`。两者不一致时，kubelet 判定"沙箱配置变了"，执行 `KillPod` 并重建。

于是形成闭环：

```
kubelet 起来 → 用当前 manifest 建 Pod → 短暂在线，apiserver 把 mirror 写进 etcd
   ↑                                              ↓
   └── 下一个 reconcile：mirror（旧 sed 版本）≠ manifest（新）→ KillPod
```

闭环之所以稳定，是因为它自己供给自己：apiserver 只在 etcd 活着时活着，etcd 只在没人杀它时活着，而杀它的判据来自 apiserver 写的 mirror。

**更隐蔽的一层**：`kubeadm reset` 没有真正清掉 `/var/lib/etcd`（etcd 日志里是 `restarting local member`，cluster 数据被复用）。所以重启计数跨 reset 延续（etcd 41 → 43、apiserver 48 → 50）——这是判断"不是陈旧状态、而是机器层面"的关键证据。

## 修复（以及我自己制造的第二重坑）

正确的动作是一个**原子序列**，顺序错了等于白做：

```bash
systemctl stop kubelet                      # 1. 先停，防止它重建 mirror
etcdctl del /registry/pods/kube-system/etcd-k8s-master \
             /registry/pods/kube-system/kube-apiserver-k8s-master \
             /registry/pods/kube-system/kube-controller-manager-k8s-master \
             /registry/pods/kube-system/kube-scheduler-k8s-master   # 2. 删过期 mirror，确认 0 残留
systemctl start kubelet                     # 3. 新 kubelet 以自己当前 config.seen 重建 mirror
systemctl disable --now etcd                # 4. 顺手关掉 apt 装的 etcd 3.3.25 冲突服务
```

为什么必须原子：**每次重启 kubelet，`config.seen` 都会变**。如果先重启 kubelet、再删 mirror，那些在重启前创建的 mirror 立刻过期，几分钟后又是一个新的错位。我在这上面绕了三轮——12:37 删完重启，12:37:58 旧 kubelet 又把 mirror 建了回去；13:19 才做清理，而 apiserver 已经卡进 5 分钟 back-off。

效果：kill 频率下降约 10 倍，etcd 一度稳定 8.5 分钟，节点曾 Ready。但**没有完全稳定**，因为叠了第二层故障。

## 第二层：CNI 死锁

calico 的 `install-cni` 卡在 5 分钟 back-off 时，CNI 插件要访问 API 才能建 sandbox，而 API 起不来 → coredns / calico-kube-controllers 无限循环：

```
No ready sandbox for pod can be found
```

kubelet 被 CNI 错误风暴淹没，连"谁杀了 apiserver"都看不清。破法是把坏插件从 containerd 的调用路径上摘掉，然后再重启**最后一次**：

```bash
mv /etc/cni/net.d/<calico 配置> /etc/cni/net.d.bak/   # 备份，不用删
systemctl restart kubelet                              # 干净环境下最后一次重启
```

## 结局

两小时后环境损伤太深（多次失败重装残留、旧 iptables/路由、apt etcd 冲突），原地恢复不可靠。新建一台干净 VM 后，`kubeadm init` **13 秒内控制平面全部健康**——反过来印证了根因不在配置本身，而在被反复折腾过的机器状态。

## 教训

1. **"自己健康"不等于"对账成功"**。etcd 每次都完美启动，杀它的判据在别处；看 `restartCount` 增加只能证明"被杀"，不能证明"为什么被杀"，必须拿 kubelet 的视角（v4 日志）。
2. **修复动作本身会改变被修复的状态**。kubelet 重启会重置 `config.seen`，所以"停-删-起"必须是原子序列，且之后不要再碰 kubelet。
3. **对账类系统的排查终点是"两份副本的差异"**，不是"哪一方坏了"。挖出 mirror pod 与 manifest 逐字节对比，比任何猜测都省时间。
4. `kubeadm reset` 不等于环境干净；能重建机器时，重建比修更快。
