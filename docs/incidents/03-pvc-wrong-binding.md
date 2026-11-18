# 03 一块 5Gi 的 PVC 抢走了 10Gi 的 logs 卷：storageClassName 为空时的抢位规则

> **一句话**：`storageClassName` 留空不等于不分配，等于把PVC扔进所有无 class PV 的公共池里，能抢到哪个由字母序决定。chart 里 value 路径写错一个层级，redis 的 5Gi 申请就会绑到 gitlab-logs-pv 上，然后整条链路崩坏。

**领域**：存储 / Helm chart · **难度**：★★★★☆ · **状态**：已修复（改用 dynamic provisioner）

## 现象

`helm` 装 GitLab 之后，两个症状同时出现：

1. `gitlab-system` 里**所有 PVC Pending**，Pod 全在 CreateContainerConfigError 之前卡住
2. 更隐蔽的一条：`redis-data-gitlab-redis-master-0` 居然 **Bound 成功**，但绑的是 `gitlab-logs-pv`（10Gi），而期望的 `redis-pv` 是 5Gi

第 2 条最容易被忽略——它不报错，`get pvc` 显示一片 Bound，只有对比 volume 名才看得出绑错了。

## 排查

对比PVC匹配和现有的PV，做四步对照：

```bash
# 1. PVC 到底有没有 class
kubectl describe pvc redis-data-gitlab-redis-master-0 -n gitlab-system
#   StorageClass: <空>          
#   Events: "no storage class is set"

# 2. 有没有 class 这一栏，直接用列视图横向比
kubectl get pvc -n gitlab-system -o custom-columns=NAME:.metadata.name,SC:.spec.storageClassName
kubectl get pv  -o custom-columns=NAME:.metadata.name,SIZE:.spec.capacity.storage,SC:.spec.storageClassName,STATUS:.status.phase
```

判读规则：

| PVC 状态 | 含义 |
|---|---|
| 有 SC，且没有匹配 PV | 手写 PV 不够/标签不匹配 |
| **无 SC** | chart 没把 class 传下去（**路径写错**） |
| 多个 PV 都无 SC | 字母序 fallback 相互抢夺 |

## 根因

三层叠加，缺一不会出事：

1. **value 路径漂移**。CR 里写的是顶层：

   ```yaml
   persistence:
     redis:
       storageClass: gitlab-redis     # ← 原以为 chart 读这里
   ```

   但 chart 9.10.8 的 redis 是 subchart，实际读的是：

   ```yaml
   redis:
     master:
       persistence:
         storageClassName: gitlab-redis   # ← chart 真正读这里
   ```

   umbrella chart 不把 subchart 的 values 暴露到顶层，传了等于没传，PVC 的 `storageClassName` 是**空**。

2. **空 class 触发 fallback**。k8s 的绑定器此时只根据字母序去匹配**所有 `storageClassName` 为空的 Available PV**。

3. **抢位规则是字母序**。候选池里有 `gitlab-logs-pv`(10Gi)、`gitlab-data-pv`(10Gi)、`gitlab-config-pv`(5Gi)、`gitlab-minio-pv`(10Gi)；redis 要 5Gi。字母序 `logs < data < minio`，于是 5Gi 申请绑走了 10Gi 的 logs 卷，size 不影响抢位，只要满足。

一个组件抢错只是开始：它占掉卷之后，下一个组件继续去抢下一个字母序的候选，**导致整个集群的 PV 分配集体错位**。

## 修复

不手写 PV，换成动态供给（local-path-provisioner，PVC 要什么现造什么）：

```bash
# 1. 装 provisioner
kubectl apply -f https://raw.githubusercontent.com/rancher/local-path-provisioner/v0.0.36/deploy/local-path-storage.yaml

# 2. 设为默认 class（PVC 不指定时自动用它）
kubectl patch storageclass local-path -p \
  '{"metadata":{"annotations":{"storageclass.kubernetes.io/is-default-class":"true"}}}'

# 3. 删掉所有手写 PV（释放"无 class 公共池"）
kubectl delete pv gitlab-gitaly-pv gitlab-postgresql-pv gitlab-redis-pv \
                gitlab-minio-pv gitlab-config-pv gitlab-logs-pv gitlab-data-pv

# 4. 让 operator 重建 PVC
kubectl delete pvc --all -n gitlab-system
kubectl delete gitlab gitlab -n gitlab-system && kubectl apply -f /mnt/gitlab/gitlab-cr.yaml
```

三条原则：

1. **默认 StorageClass 必设**（`is-default-class: "true"`），否则空 class 的 PVC 永远在待分配。
2. **`volumeBindingMode: WaitForFirstConsumer`**——PVC 与 Pod 都就绪后才绑，保证 PV 的本地目录和 Pod 在同一个节点；`Immediate` 模式下 local PV 会绑到任意节点导致 Pod 无处可调度。
3. **手写 PV 要清干净**，遗留的 Available PV 永远是抢绑的候选。

## 验证

```bash
kubectl get pvc -n gitlab-system          # 全部 Bound 且 SC 一致
kubectl get storageclass local-path       # 带 (default) 标记
kubectl get pods -n local-path-storage    # provisioner Running
```

## 教训

1. **`storageClassName` 为空不等于随机分配**。任何 chart 调参后都要回查 PVC 里 class 是否真的落地：`-o jsonpath='{.spec.storageClassName}'`。
2. **subchart 的 value 路径不可猜**：`helm show values` 或 `helm template` 出来的渲染结果才是唯一事实。
3. **"全部 Bound"不等于"绑对了"**。批量检查要看 `volumeName` 与期望的对应关系，不能只看 STATUS 列。
