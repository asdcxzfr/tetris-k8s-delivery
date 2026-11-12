# 08 abort ≠ 回滚：暂停发布，镜像还是新的

> **一句话**：`kubectl argo rollouts abort` 停的是**流量与流程**，不是**版本**。`spec.template` 里的新镜像没有改变，所以"abort 之后就回到旧版本了"是一种错觉；真要退版本，只能把镜像指回去。

**领域**：渐进式交付语义 · **难度**：★★☆☆☆ · **状态**：认知修正（流程按此执行）

## 现象

金丝雀发布被护栏拦下、自动 abort 之后，`kubectl get pods` 里确实只剩 stable 的 Pod 在跑，看起来"已经回滚了"。但下一次CI碰一下这个 Rollout，坏镜像又会回来。

## 排查

把 abort 之后的对象原样打出来看：

```bash
kubectl -n tetris-test get rollout tetris-game -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'
# harbor.local/tetris-game/tetris-game:test-branch-36   #新标签pod
kubectl -n tetris-test get rollout tetris-game -o jsonpath='{.status.phase}{" "}{.status.message}{"\n"}'
# Degraded RolloutAborted
```

`status` 说"已中止"，`spec` 说"当前版本是新的"。真相：**abort 改的是 status 与流量路由，不改 spec**。

## 根因

Argo Rollouts 的模型里，"发布"是 `spec.template` 的每一次变化产生一个新 ReplicaSet。abort 只做三件事：

1. 停止为当前 revision 推进 steps（把 `status.stableRS` / `currentPodHash` 的流量关系锁在旧版本）
2. 把 phase 推到 `Degraded`、message 写成 `RolloutAborted`
3. **保留** canary 的 ReplicaSet（可能缩到 0 副本，但不删除）

它不修改 `spec.template`，所以"新版本"仍是声明态。这也是为什么 abort 之后 `promote` 没用；要继续发布，得再改一次镜像产生新 revision。

另外：`kubectl rollout undo` 是 Deployment 的语义，**Rollout 不支持**。

## 修复 / 正确姿势

想真回滚，就显式把镜像指回旧 tag（这是唯一可靠的"退版本"动作）：

```bash
# 方式一：argo 插件（会更新 spec.template，算一次新 revision）
kubectl argo rollouts set image tetris-game \
  tetris-game=harbor.local/tetris-game/tetris-game:test-branch-35 -n tetris-test

# 方式二：json patch（CI 里用的就是这条，因为 Rollout 是 CRD，没有 strategic-merge 元数据）
kubectl -n tetris-test patch rollout tetris-game --type='json' \
  -p='[{"op":"replace","path":"/spec/template/spec/containers/0/image","value":"harbor.local/tetris-game/tetris-game:test-branch-35"}]'
```

配套认知：

- `abort` 之后的 `promote` 无效；`promote --full` 只是把剩余 steps 一次走完，**会绕过人工 gate**
- abort 不会删 canary 的 RS，`get pods` 里可能还看到 0 副本的旧 RS，正常现象
- 单副本时删 Pod 就是零可用，回滚动作本身会造成短暂不可用

## 验证

```bash
kubectl argo rollouts get rollout tetris-game -n tetris-test --watch
# 期望看到Image 一栏 stable 与 current 相同；Status: ✔ Healthy
```

## 教训

1. **区分"控制意图"（spec）与"运行结果"（status/流量）**。abort 只动后者；只有改 spec 才是真正的版本迁移。
2. **回滚要写进流程**：谁来决定旧 tag 是哪个、由什么命令执行、要不要人确认。护栏拦下发布只是第一步，abort之后的第二步依旧需要人完成。
3. 别把 `kubectl rollout undo` 的习惯带过来，CRD 上它不存在。
