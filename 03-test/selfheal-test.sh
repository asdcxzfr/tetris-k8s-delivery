#!/bin/bash
# ==============================================================================
#  故障自愈计时  /root/selfheal-test.sh
#
#  用法:
#    ./selfheal-test.sh pod    场景A: 删除 Pod            (ReplicaSet 重建)   预期 ~8s
#    ./selfheal-test.sh int    场景B: 容器内 SIGINT 崩溃   (kubelet 重启容器)  预期 ~15s
#
#  重要背景 (踩过的坑):
#    Linux 规定: 只有 init(PID 1) 安装了 handler 的信号, 才能被同一 PID
#    namespace 内的其他进程送达给它 (man 7 pid_namespaces)。
#    本容器 PID 1 = python app.py, 实测 SigCgt=...0002 -> 只有 SIGINT 有 handler。
#    所以 `kill -9 1` / `kill -15 1` 都是无效的 (syscall 成功但信号被丢弃),
#    轮询 restartCount 会永远等不到 -> 脚本假死。改用 kill -INT 1。
#
#  本脚本所有等待都有超时保护, 不会挂死。
# ==============================================================================
set -u
NS=tetris-test
LABEL='app=tetris-game'
MODE="${1:-pod}"
MAXWAIT=90

pick(){  kubectl -n $NS get pod -l $LABEL -o jsonpath='{.items[0].metadata.name}' 2>/dev/null; }
ready(){ kubectl -n $NS get pod "$1" -o jsonpath='{.status.containerStatuses[0].ready}' 2>/dev/null; }
rc(){    kubectl -n $NS get pod "$1" -o jsonpath='{.status.containerStatuses[0].restartCount}' 2>/dev/null; }

report(){
  echo "----------------------------------------------------"
  echo "恢复耗时: $1 ms  ( 约 $(( $1/1000 )) 秒 )"
  if [ "$1" -le "$2" ]; then echo "判定: PASS (阈值 $(( $2/1000 )) 秒)"; else echo "判定: FAIL (阈值 $(( $2/1000 )) 秒)"; fi
}

case "$MODE" in

pod)
  OLD=$(pick); [ -z "$OLD" ] && { echo "找不到 Pod"; exit 1; }
  echo "旧 Pod : $OLD  (IP $(kubectl -n $NS get pod "$OLD" -o jsonpath='{.status.podIP}'))"
  echo "操作   : kubectl delete pod $OLD"
  T0=$(date +%s%3N)
  kubectl -n $NS delete pod "$OLD" --wait=false >/dev/null 2>&1
  DL=$(( $(date +%s) + MAXWAIT ))
  while :; do
    N=$(pick)
    if [ -n "$N" ] && [ "$N" != "$OLD" ] && [ "$(ready "$N")" = "true" ]; then break; fi
    if [ "$(date +%s)" -gt "$DL" ]; then echo "!! 超时 ${MAXWAIT}s, 放弃"; exit 2; fi
    sleep 0.3
  done
  T1=$(date +%s%3N)
  echo "新 Pod : $N  (IP $(kubectl -n $NS get pod "$N" -o jsonpath='{.status.podIP}'))"
  report $(( T1-T0 )) 10000
  ;;

int)
  POD=$(pick); [ -z "$POD" ] && { echo "找不到 Pod"; exit 1; }
  RC0=$(rc "$POD")
  echo "Pod    : $POD   当前 restartCount = $RC0"
  echo "预检   : PID1 信号掩码 (SigCgt, 只有设了 handler 的信号能送达)"
  kubectl -n $NS exec "$POD" -- sh -c 'grep -E "^(SigCgt|SigIgn)" /proc/1/status' 2>/dev/null | sed 's/^/         /'
  echo "操作   : kubectl exec $POD -- kill -INT 1   (SIGINT -> Python KeyboardInterrupt -> 进程退出)"
  T0=$(date +%s%3N)
  timeout 10 kubectl -n $NS exec "$POD" -- sh -c 'kill -INT 1' >/dev/null 2>&1 || true
  DL=$(( $(date +%s) + MAXWAIT ))
  while :; do
    RN=$(rc "$POD")
    if [ -n "$RN" ] && [ "$RN" -gt "$RC0" ] && [ "$(ready "$POD")" = "true" ]; then break; fi
    if [ "$(date +%s)" -gt "$DL" ]; then echo "!! 超时 ${MAXWAIT}s (restartCount = ${RN:-?})"; exit 2; fi
    sleep 0.3
  done
  T1=$(date +%s%3N)
  echo "restartCount : $RC0 -> $RN"
  echo "lastState    : $(kubectl -n $NS get pod "$POD" -o jsonpath='{.status.containerStatuses[0].lastState.terminated.reason}' 2>/dev/null) (exit $(kubectl -n $NS get pod "$POD" -o jsonpath='{.status.containerStatuses[0].lastState.terminated.exitCode}' 2>/dev/null))"
  report $(( T1-T0 )) 10000
  echo
  echo "参考: kubelet 容器重启退避 = 10s / 20s / 40s ... (上限 5min),"
  echo "      容器连续正常运行 10 分钟后计数重置。首次重启的等待就是 10s。"
  ;;

*)
  echo "用法: $0 {pod|int}"
  echo
  echo "  场景C (真·异常崩溃, 需在 Pod 所在节点上执行) —— 从祖先 namespace 发 SIGKILL 不受 PID 1 限制:"
  echo "    NODE=\$(kubectl -n tetris-test get pod -l app=tetris-game -o jsonpath='{.items[0].spec.nodeName}')"
  echo "    CID=\$(kubectl -n tetris-test get pod -l app=tetris-game -o jsonpath='{.status.containerStatuses[0].containerID}' | sed 's|containerd://||')"
  echo "    ssh root@\$NODE \"crictl inspect \$CID | python3 -c 'import sys,json;print(json.load(sys.stdin)[\\\"info\\\"][\\\"pid\\\"])'\"   # 得到 host PID"
  echo "    ssh root@\$NODE 'kill -9 <host PID>'"
  exit 1
  ;;
esac
