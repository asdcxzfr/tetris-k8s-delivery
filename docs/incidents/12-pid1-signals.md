# 12 容器里的 PID 1 收不到 SIGTERM：一次"以为重启了其实没有"的自愈假死

> **一句话**：容器里 `python app.py` 就是 PID 1，而内核**不给 PID 1 任何默认信号处置**，没有注册 handler 的信号会被直接丢弃。于是 `kill -15 1` 和 `kill -9 1` 的 syscall 都返回成功，进程却纹丝不动。凡是靠"杀掉/重启"来验证自愈的测试，都会在这里假死。

**领域**：Linux 信号 / 容器进程模型 · **难度**：★★★★☆ · **状态**：已修复（测试脚本改造）

## 现象

自愈测试脚本的逻辑很朴素：注入异常 → 每隔几秒查 `restartCount` → 增加就算自愈成功。但跑起来后 **restartCount 一直是 0**，脚本等到超时，报告未自愈。

手动验证更迷惑：

```bash
kubectl -n tetris-test exec deploy/tetris-game -- sh -c 'kill -15 1; echo sent'
# sent            # 命令成功返回
kubectl -n tetris-test get pod -l app=tetris-game -o jsonpath='{.items[0].status.containerStatuses[0].restartCount}'
# 0               # 完全没重启
```

`kill` 明明返回成功，容器也没退出。

## 排查

先确认 PID 1 是谁、装了哪些信号处理器：

```bash
kubectl -n tetris-test exec deploy/tetris-game -- cat /proc/1/status | grep -E 'Name|SigCgt|SigBlk|SigIgn'
# Name:	 python
# SigCgt: 0000000000000002      ← 只捕获了 SIGINT(2)
# SigBlk: 0000000000000000
# SigIgn: 0000000000000000
```

`SigCgt` 是位掩码，位 1（值 2）代表信号 2 = SIGINT。也就是说：**SIGINT 有 handler，SIGTERM 没有，SIGKILL 无 handler 可言**。

再看进程树，确认没有 init 类进程挡在前面：

```bash
kubectl -n tetris-test exec deploy/tetris-game -- ps -o pid,ppid,comm
#  PID  PPID COMMAND
#    1     0 python app.py        # 直接就是业务进程，没有 tini / dumb-init
```

## 根因

Linux 内核对 **PID 1 有特殊规则**：普通进程收到没有 handler 的信号时，默认动作是终止；但 PID 1 的默认动作被改为"忽略"（避免 init 被误杀导致内核 panic）。所以：

- `kill -15 1`（SIGTERM，Python 默认没装 handler）→ 内核丢弃 → 进程继续跑
- `kill -9 1`（SIGKILL）→ **同为 PID 1 的例外**，syscall 返回 0 但信号被丢弃
- `sh -c 'kill ...'` 的退出码只反映"信号是否成功发出"，不反映"是否产生了效果"——这是假象的来源

于是测试脚本陷入经典的轮询一个永远不会变的计数器：它看到 restartCount 不涨，就以为容器没干掉；实际上容器**真的**没被干掉。

顺带暴露的第二层问题（同为 Flask 项目常见坑）：`debug=True` 时 werkzeug 会启动 reloader，真正跑服务的是带 `WERKZEUG_RUN_MAIN=1` 的子进程。prometheus exporter 若在 import 阶段判断 `is_running_from_reloader()` 为真就不注册 metrics，`/metrics` 就会**空着**，看上去像"指标没采集到"，其实是"根本没暴露"。

## 修复

业务侧（治本）：让 app 自己处理 SIGTERM，并在容器里用 init 转发信号。

```python
# app.py
import signal, sys

def _on_sigterm(*_):
    # 给 socketio / db 连接一点收尾时间，然后主动退出
    sys.exit(0)

signal.signal(signal.SIGTERM, _on_sigterm)
```

```yaml
# rollout.yaml
spec:
  template:
    spec:
      containers:
        - name: tetris-game
          # 选项 A：加 init 代理（tini 会把信号转发给 PID 1 之后的进程，并回收僵尸）
          # 选项 B：不用 init 时，至少确保进程注册了 SIGTERM handler
          lifecycle:
            preStop:
              exec:
                command: ["sh", "-c", "sleep 5"]
      terminationGracePeriodSeconds: 20
```

测试侧（治标但必要）：**只发 PID 1 真正注册过的信号**。

```bash
kubectl -n tetris-test exec deploy/tetris-game -- sh -c 'kill -INT 1'
# 然后轮询 restartCount；若要验证"被 SIGKILL 也活不了"，就整 Pod 删：
kubectl -n tetris-test delete pod -l rollouts-pod-template-hash=<hash>
```

注意最后一条：**对于金丝雀发布场景删 Pod 必须带 `rollouts-pod-template-hash`**，否则 `-l app=tetris-game` 会把 stable 与 canary 的 Pod 一起删掉，单副本场景直接造成零可用。

## 验证

```bash
# 1) handler 是否真的注册上了（重启后位掩码应包含 SIGTERM=15，即 0x4000）
kubectl -n tetris-test exec deploy/tetris-game -- sh -c \
  'kill -INT 1' && kubectl -n tetris-test get pod -l app=tetris-game \
  -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.status.containerStatuses[0].restartCount}{"\n"}{end}'

# 2) exporter 是否暴露（非空）
kubectl -n tetris-test exec deploy/tetris-game -- sh -c 'wget -qO- localhost:8000/metrics | wc -l'
```

## 教训

1. **PID 1 的默认语义与普通进程不同**，这是容器里最容易看起来成功了的静默失败：退出码 0 不代表有副作用。
2. **验证"能不能被杀"之前，先查 `SigCgt`**。只发注册过的信号，或者从外部删 Pod，PID 1上的默认行为不会生效。
3. **测试脚本的失败模式要和被测对象解耦**：轮询 restartCount 这类看结果的方式，遇到动作根本没生效时会给出误导性结论，应同时断言"动作是否产生了预期副作用"。
