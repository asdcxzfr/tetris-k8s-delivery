#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tetris 并发压测客户端  (100 并发 / 10 分钟)

严格模仿真实前端 (static/game.js) 的行为:
    connect -> 等 'connected' -> emit('start_game') -> 等 'game_started'
            -> 持续 emit(move_left/move_right/rotate/move_down/hard_drop)
            -> 收到 game_state.game_over 时 emit('restart') + emit('start_game')

  * start_game 是必须的: 服务端 handle_move_down 有 `game.game_started` 守卫。
  * 依赖: 不装任何包。engineio 客户端需要 requests, 用 pip 自带的 vendored 副本注入
    sys.modules 解决 (见 _bootstrap_deps)。因此传输通常是 polling (除非镜像里有
    websocket-client)。polling 每个事件一次 HTTP 请求, 对服务端压力更大。

延迟测量有两条, 分开统计:
  [A] 主指标 : get_statistics -> 'statistics'   完全无污染
                (fall timer emit 的是 game_state / statistics_update, 不会串扰)
  [B] 次指标 : move_* -> 下一个 'game_state'    更贴近"操作手感"
                注意: 掉落定时器也会 emit game_state, 所以 B 会被"提前到达"的 tick
                干扰 -> 实测值相对真值偏乐观(偏低)。引用 B 时需知悉此偏差。

错误率口径 (修正过): errors / attempts
    errors   = 连接失败 + 探针超时 + 探针发送失败 + 动作发送失败
    attempts = 连接尝试 + 探针发送 + 动作发送
  早期版本只算"探针超时/探针发送", 连接全失败时仍显示 0%, 属严重误导, 已修正。

进程模型: 父进程 fork N 个 worker, 每个 worker 跑 M 个客户端线程。
          总并发 = N x M, 默认 10 x 10 = 100。
"""
import argparse
import collections
import json
import multiprocessing as mp
import os
import random
import shutil
import sys
import tempfile
import threading
import time


def _bootstrap_deps():
    """零依赖引导 (踩坑总结):
    python-engineio 的客户端 `import requests`, 而 app 镜像里没装 requests。
    但镜像自带 pip, 而 pip 自己 vendor 了 requests/urllib3/certifi/idna/chardet
    (实测 pip 23.0.1: requests=2.28.2 urllib3=1.26.14 certifi=2022.12.07
     idna=3.4 chardet=5.1.0; charset_normalizer 未 vendor, requests 会退回 chardet)。
    把这些模块注入 sys.modules 即可, 无需任何网络下载。

    为什么不用 pip install: 本集群 Pod 的外部 DNS 解析间歇性失败
    ([Errno -3] Temporary failure in name resolution), 且镜像内 pip.conf 指向的
    mirrors.aliyun.com 因 trusted-host 未生效会被 pip 直接"忽略该仓库"。
    预下载 wheel 到 hostPath 也会被 DNS 问题卡住。故采用本方案。
    """
    try:
        import requests  # noqa: F401
        return 'system'
    except Exception:
        pass
    try:
        from pip._vendor import requests
    except Exception as ex:
        return 'FAILED:%s' % type(ex).__name__
    sys.modules.setdefault('requests', requests)
    for name in ('urllib3', 'certifi', 'idna', 'charset_normalizer', 'chardet'):
        try:
            mod = __import__('pip._vendor.' + name, fromlist=[name])
            sys.modules.setdefault(name, mod)
        except Exception:
            pass
    return 'pip-vendored(requests=%s)' % getattr(requests, '__version__', '?')


BOOT_MODE = _bootstrap_deps()

# 传输选择: 有 websocket-client 才优先 websocket, 否则直接 polling (避免无谓的失败重试)
try:
    import websocket  # noqa: F401
    TRANSPORTS = [['websocket', 'polling'], ['polling']]
    TRANSPORT_HINT = 'websocket-first'
except Exception:
    TRANSPORTS = [['polling']]
    TRANSPORT_HINT = 'polling-only (websocket-client 未安装)'

ACT_NAMES = ['move_left', 'move_right', 'rotate', 'move_down', 'hard_drop']
ACT_WEIGHTS = [40, 40, 15, 4, 1]


def pct(sorted_vals, p):
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


class Gate:
    """可容错的"就绪"同步点: 每个客户端连上或失败后都 arrive(),
    主线程等所有客户端都报告过即可继续 —— 不会因为部分失败而卡死。"""

    def __init__(self, n):
        self.n = n
        self.count = 0
        self.lock = threading.Lock()
        self.evt = threading.Event()

    def arrive(self):
        with self.lock:
            self.count += 1
            if self.count >= self.n:
                self.evt.set()

    def wait(self, timeout):
        return self.evt.wait(timeout)


class Client(threading.Thread):
    def __init__(self, idx, cfg, stats, gate, ordinal=0):
        super().__init__(daemon=True)
        self.idx = idx
        self.ordinal = ordinal
        self.cfg = cfg
        self.stats = stats
        self.gate = gate
        self.lock = threading.Lock()
        self.sio = None
        self.connected_evt = threading.Event()
        self.game_started_evt = threading.Event()
        # [A] FIFO: 已发出、还没收到回复的探针发出时刻。
        # 旧版用 stats_evt.wait(probe_timeout) 阻塞等待 -> 探针一超时就卡住整个
        # 客户端线程 5 秒, 动作循环被饿死, 吞吐和延迟双双失真。现改为非阻塞配对。
        self.probe_q = collections.deque()
        self.t_act_sent = 0.0
        self.last_start = 0.0
        self.pending_act = False
        self.game_over = False
        self.stop = False

    # ---------- server -> client ----------
    def on_connected(self, data=None):
        self.connected_evt.set()

    def on_game_started(self, data=None):
        self.game_started_evt.set()

    def on_statistics(self, data=None):
        now = time.time()
        with self.lock:
            self.stats['stats_replies'] += 1
            if self.probe_q:
                self.stats['rtt_a'].append(now - self.probe_q.popleft())

    def on_statistics_update(self, data=None):
        pass

    def on_game_state(self, data=None):
        if self.pending_act and self.t_act_sent:
            # 这是"我发动作的响应"
            rtt = time.time() - self.t_act_sent
            with self.lock:
                self.stats['rtt_b'].append(rtt)
                self.stats['states_resp'] += 1
            self.pending_act = False
            self.t_act_sent = 0.0
        else:
            # 这是掉落定时器主动推的 tick (不是我们的响应)
            with self.lock:
                self.stats['states_tick'] += 1
        with self.lock:
            self.stats['states'] += 1
        if isinstance(data, dict) and data.get('game_over'):
            self.game_over = True

    # ---------- 连接 ----------
    def connect(self):
        import socketio
        cfg = self.cfg
        attempts = TRANSPORTS
        for ti, tr in enumerate(attempts):
            try:
                sio = socketio.Client(reconnection=True, request_timeout=15,
                                      logger=False, engineio_logger=False)
                sio.on('connected', self.on_connected)
                sio.on('game_started', self.on_game_started)
                sio.on('statistics', self.on_statistics)
                sio.on('statistics_update', self.on_statistics_update)
                sio.on('game_state', self.on_game_state)
                t0 = time.time()
                sio.connect(cfg['url'], transports=tr, wait_timeout=cfg['connect_timeout'])
                used = 'unknown'
                try:
                    used = sio.transport()
                except Exception:
                    pass
                with self.lock:
                    self.stats['conn_ok'] += 1
                    # 单位: 秒 (和 rtt_a / rtt_b 保持一致; report() 统一 *1000 转 ms)
                    self.stats['conn_ms'].append(time.time() - t0)
                    self.stats['transport'][used] = self.stats['transport'].get(used, 0) + 1
                self.sio = sio
                return True
            except Exception as ex:
                with self.lock:
                    self.stats['err_kinds']['connect[%s]:%s' % (
                        ','.join(tr), type(ex).__name__)] = \
                        self.stats['err_kinds'].get('connect[%s]:%s' % (
                            ','.join(tr), type(ex).__name__), 0) + 1
                try:
                    sio.disconnect()
                except Exception:
                    pass
        with self.lock:
            self.stats['conn_err'] += 1
        return False

    def run(self):
        cfg = self.cfg
        # 连接错峰: 100 个客户端在同一毫秒内全部砸向 connect() 是不真实的"惊群"。
        # 真实场景是玩家陆续进入。ramp 把连接摊开在 connect_ramp 秒内。
        if cfg.get('connect_ramp', 0) > 0 and cfg.get('total_clients', 0) > 1:
            time.sleep(self.ordinal * cfg['connect_ramp'] / float(cfg['total_clients'] - 1))
        if not self.connect():
            self.gate.arrive()
            return
        self.gate.arrive()

        self.connected_evt.wait(cfg['connect_timeout'])
        start = time.time()
        last_probe = 0.0
        last_act = 0.0

        while not self.stop and (time.time() - start) < cfg['duration']:
            now = time.time()

            # 连接掉了就必须立刻退出。否则下面"游戏还没开始"的分支会 10ms 一次地
            # 重发 start_game —— 实测 42 个断线客户端刷出 80932 次 start_game emit,
            # 把服务器打死。那样测到的是压测端的 bug, 不是应用的能力。
            if self.sio is None or not getattr(self.sio, 'connected', False):
                with self.lock:
                    self.stats['lost_conn'] += 1
                break

            if self.game_over:
                try:
                    self.sio.emit('restart')
                    self.game_started_evt.clear()
                    self.sio.emit('start_game')
                    with self.lock:
                        self.stats['restarts'] += 1
                    self.game_over = False
                except Exception:
                    with self.lock:
                        self.stats['err_kinds']['restart_emit'] = \
                            self.stats['err_kinds'].get('restart_emit', 0) + 1
                time.sleep(0.5)

            if not self.game_started_evt.is_set() and (now - self.last_start) >= 1.0:
                try:
                    self.sio.emit('start_game')
                    self.last_start = now
                    with self.lock:
                        self.stats['start_game'] += 1
                except Exception:
                    with self.lock:
                        self.stats['err_kinds']['start_game_emit'] = \
                            self.stats['err_kinds'].get('start_game_emit', 0) + 1

            # ---- 超时清算: 迟迟不回的探针/在途动作, 在这里统一判超时 ----
            with self.lock:
                while self.probe_q and (now - self.probe_q[0]) > cfg['probe_timeout']:
                    self.probe_q.popleft()
                    self.stats['probe_timeout'] += 1
                if self.pending_act and (now - self.t_act_sent) > cfg['action_timeout']:
                    self.pending_act = False
                    self.stats['act_timeout'] += 1
                    self.stats['err_kinds']['act_timeout'] = \
                        self.stats['err_kinds'].get('act_timeout', 0) + 1

            # [A] 延迟探针 (无污染): 只记下发出时刻, 回复在 on_statistics 里配对, 不阻塞
            #     probe_interval=0 表示关闭。注意: 真实前端 static/game.js 从不发
            #     get_statistics, 而这个 handler 每次要打 4 条 SQL, 所以它属于"额外
            #     人为负载"; 测应用极限时应关掉, 否则测的是我们自己加的压力。
            if cfg['probe_interval'] > 0 and now - last_probe >= cfg['probe_interval']:
                try:
                    with self.lock:
                        self.probe_q.append(time.time())
                        self.stats['probes_sent'] += 1
                    self.sio.emit('get_statistics')
                except Exception:
                    with self.lock:
                        if self.probe_q:
                            self.probe_q.pop()
                        self.stats['probe_emit_err'] += 1
                last_probe = now

            # [B] 持续发动作 —— 一次只允许一个"在途"
            #     真实玩家不会在服务器还没响应时继续狂点; 不设这个闸门, 测到的其实是
            #     客户端自己堆积队列的速度, 而不是服务端能力。
            if (not self.pending_act) and (now - last_act >= cfg['action_interval']):
                act = random.choices(ACT_NAMES, weights=ACT_WEIGHTS, k=1)[0]
                try:
                    with self.lock:
                        self.t_act_sent = time.time()
                        self.pending_act = True
                        self.stats['actions'] += 1
                    self.sio.emit(act)
                except Exception:
                    with self.lock:
                        self.pending_act = False
                        self.stats['action_emit_err'] += 1
                        self.stats['err_kinds']['action_emit'] = \
                            self.stats['err_kinds'].get('action_emit', 0) + 1
                last_act = now

            time.sleep(0.01)

        try:
            self.sio.disconnect()
        except Exception:
            pass


def worker(proc_idx, cfg, out_path):
    stats = {
        'proc': proc_idx, 'actions': 0, 'states': 0, 'states_resp': 0, 'states_tick': 0,
        'start_game': 0, 'restarts': 0, 'stats_replies': 0,
        'probes_sent': 0, 'probe_timeout': 0, 'probe_emit_err': 0,
        'action_emit_err': 0, 'act_timeout': 0, 'lost_conn': 0,
        'conn_ok': 0, 'conn_err': 0, 'conn_ms': [], 'rtt_a': [], 'rtt_b': [],
        'err_kinds': {}, 'transport': {},
    }
    n = cfg['clients']
    gate = Gate(n)
    base = proc_idx * n
    threads = [Client(i, cfg, stats, gate, ordinal=base + i) for i in range(n)]
    for t in threads:
        t.start()

    got_all = gate.wait(cfg['connect_timeout'] + 30 + cfg.get('connect_ramp', 0))
    if not got_all:
        print('[proc %02d] WARN: only %d/%d clients reported ready'
              % (proc_idx, gate.count, n), flush=True)

    start = time.time()
    while time.time() - start < cfg['duration']:
        time.sleep(30)
        el = int(time.time() - start)
        print('[proc %02d] t=%3ds actions=%d states=%d rttA=%d rttB=%d conn_ok=%d conn_err=%d'
              % (proc_idx, el, stats['actions'], stats['states'],
                 len(stats['rtt_a']), len(stats['rtt_b']),
                 stats['conn_ok'], stats['conn_err']), flush=True)

    for t in threads:
        t.stop = True
    for t in threads:
        t.join(timeout=15)
    # 结果写文件, 不用 multiprocessing.Queue:
    # 父进程是先 join 再 get 的, 而子进程退出前要等 feeder 线程把数据 flush 进管道;
    # 管道 64KB 一满 + 父进程还在 join -> 双向死锁 (20并发/120s 时实测踩到,
    # 结果就是各 proc 进度都打完了却永远等不到 RESULT SUMMARY)。
    with open(out_path, 'w') as f:
        json.dump(stats, f)


def _pick_stats_dir():
    """挑一个真正可写的目录放各 worker 的结果文件。
    不能想当然用系统临时目录: 在受限沙箱里 mkdtemp() 能建目录但子进程写不进去,
    结果文件全丢, 而"没有数据"又会被误判成 0% 错误率 -> PASS。逐个探测并试写。"""
    for d in [os.environ.get('LT_STATS_DIR'), '/tmp', '/var/tmp', os.getcwd(), '.']:
        if not d:
            continue
        try:
            os.makedirs(d, exist_ok=True)
            probe = os.path.join(d, '.ltwrite-%d' % os.getpid())
            with open(probe, 'w') as f:
                f.write('x')
            os.remove(probe)
            return tempfile.mkdtemp(prefix='ltstats-', dir=d)
        except Exception:
            continue
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--url', default='http://tetris-game-service.tetris-test:8080')
    ap.add_argument('--procs', type=int, default=10)
    ap.add_argument('--clients', type=int, default=10)
    ap.add_argument('--duration', type=int, default=600)
    ap.add_argument('--action-interval', type=float, default=0.3)
    ap.add_argument('--probe-interval', type=float, default=10.0)
    ap.add_argument('--probe-timeout', type=float, default=5.0)
    ap.add_argument('--connect-timeout', type=float, default=20.0)
    ap.add_argument('--action-timeout', type=float, default=5.0)
    ap.add_argument('--connect-ramp', type=float, default=0.0,
                    help='把 N 个客户端的连接摊开在这么多秒内完成 (0 = 同时连接)')
    args = ap.parse_args()

    cfg = {'url': args.url, 'clients': args.clients, 'duration': args.duration,
           'action_interval': args.action_interval, 'probe_interval': args.probe_interval,
           'probe_timeout': args.probe_timeout, 'connect_timeout': args.connect_timeout,
           'action_timeout': args.action_timeout, 'connect_ramp': args.connect_ramp,
           'total_clients': args.procs * args.clients}
    total = args.procs * args.clients
    print('=' * 78, flush=True)
    print('Tetris load test', flush=True)
    print('  target      : %s' % cfg['url'], flush=True)
    print('  dep bootstrap: %s' % BOOT_MODE, flush=True)
    print('  transport   : %s' % TRANSPORT_HINT, flush=True)
    print('  concurrency : %d procs x %d clients = %d' % (args.procs, args.clients, total), flush=True)
    print('  duration    : %ds' % cfg['duration'], flush=True)
    print('  action rate : ~%.0f actions/s total' % (total / cfg['action_interval']), flush=True)
    print('  probe rate  : %s' % ('关闭 (--probe-interval 0)' if cfg['probe_interval'] <= 0
                                  else '~%.1f get_statistics/s total' % (total / cfg['probe_interval'])),
          flush=True)
    print('  connect ramp: %s' % ('%.0fs 内错峰接入' % args.connect_ramp if args.connect_ramp else '无 (全部同时连接)'), flush=True)
    print('=' * 78, flush=True)

    out_dir = _pick_stats_dir()
    if not out_dir:
        print('FATAL: 找不到可写的目录放结果文件; 拒绝在拿不到数据的情况下给出结论', flush=True)
        sys.exit(3)
    print('  stats dir   : %s' % out_dir, flush=True)
    procs = []
    for i in range(args.procs):
        out_path = os.path.join(out_dir, 'proc%02d.json' % i)
        procs.append(mp.Process(target=worker, args=(i, cfg, out_path)))
    t_wall = time.time()
    for p in procs:
        p.start()
    for p in procs:
        p.join()
    wall = time.time() - t_wall

    results = []
    for i in range(args.procs):
        out_path = os.path.join(out_dir, 'proc%02d.json' % i)
        try:
            with open(out_path) as f:
                results.append(json.load(f))
        except Exception:
            print('[warn] proc %02d produced no stats file (%s)' % (i, out_path), flush=True)
    shutil.rmtree(out_dir, ignore_errors=True)
    agg = {k: 0 for k in ['actions', 'states', 'states_resp', 'states_tick', 'start_game',
                          'restarts', 'probes_sent', 'probe_timeout', 'probe_emit_err',
                          'action_emit_err', 'act_timeout', 'stats_replies', 'lost_conn',
                          'conn_ok', 'conn_err']}
    agg.update({'rtt_a': [], 'rtt_b': [], 'conn_ms': [], 'err_kinds': {}, 'transport': {}})
    for r in results:
        for k in ['actions', 'states', 'states_resp', 'states_tick', 'start_game', 'restarts',
                  'probes_sent', 'probe_timeout', 'probe_emit_err', 'action_emit_err',
                  'act_timeout', 'stats_replies', 'lost_conn', 'conn_ok', 'conn_err']:
            agg[k] += r.get(k, 0)
        for k in ['rtt_a', 'rtt_b', 'conn_ms']:
            agg[k].extend(r.get(k, []))
        for kk, vv in (r.get('err_kinds') or {}).items():
            agg['err_kinds'][kk] = agg['err_kinds'].get(kk, 0) + vv
        for kk, vv in (r.get('transport') or {}).items():
            agg['transport'][kk] = agg['transport'].get(kk, 0) + vv

    a, b, cm = sorted(agg['rtt_a']), sorted(agg['rtt_b']), sorted(agg['conn_ms'])

    print()
    print('=' * 78, flush=True)
    print('RESULT SUMMARY', flush=True)
    print('=' * 78, flush=True)
    print('wall time             : %.1f s' % wall, flush=True)
    print('connections ok / err  : %d / %d' % (agg['conn_ok'], agg['conn_err']), flush=True)
    print('transport used        : %s' % json.dumps(agg['transport']), flush=True)
    print('start_game emitted    : %d' % agg['start_game'], flush=True)
    print('game restarts         : %d' % agg['restarts'], flush=True)
    print('actions emitted       : %d   (%.0f/s)' % (agg['actions'], agg['actions'] / max(wall, 1)), flush=True)
    print('game_state received   : %d   (%.0f/s)' % (agg['states'], agg['states'] / max(wall, 1)), flush=True)
    print('   - 作为动作响应     : %d' % agg['states_resp'], flush=True)
    print('   - 掉落 tick 主动推 : %d' % agg['states_tick'], flush=True)
    if agg['states']:
        tick_ratio = agg['states_tick'] / agg['states'] * 100.0
        print('   => tick 占比 %.1f%%  ([B] 指标的乐观偏差上限约 = tick占比 x P50)'
              % tick_ratio, flush=True)
    print('latency probes sent   : %d   (replies %d, timeouts %d, emit_err %d)'
          % (agg['probes_sent'], agg['stats_replies'], agg['probe_timeout'],
             agg['probe_emit_err']), flush=True)
    print()

    def report(name, vals, note=''):
        if not vals:
            print('%s: NO SAMPLES' % name, flush=True)
            return None
        o = {'n': len(vals), 'p50': pct(vals, 50) * 1000, 'p95': pct(vals, 95) * 1000,
             'p99': pct(vals, 99) * 1000, 'max': vals[-1] * 1000,
             'mean': sum(vals) / len(vals) * 1000}
        print('%s  (n=%d) %s' % (name, o['n'], note), flush=True)
        print('    P50=%8.2f ms   P95=%8.2f ms   P99=%8.2f ms   max=%9.2f ms   mean=%8.2f ms'
              % (o['p50'], o['p95'], o['p99'], o['max'], o['mean']), flush=True)
        return o

    ra = report('[A] PRIMARY   get_statistics -> statistics', a,
                '(clean, no fall-tick interference)')
    rb = report('[B] SECONDARY move_* -> game_state', b,
                '(NOTE: fall ticks can arrive first -> biased LOW)')
    rc = report('[C] connect() time', cm, '')
    print()

    attempts = agg['conn_ok'] + agg['conn_err'] + agg['probes_sent'] + agg['actions']
    errors = (agg['conn_err'] + agg['probe_timeout'] + agg['probe_emit_err']
              + agg['action_emit_err'] + agg['act_timeout'] + agg['lost_conn'])
    err_rate = (errors / attempts * 100.0) if attempts else 0.0
    total_clients = cfg['total_clients']
    print('客户端存活            : %d/%d 连上, %d 个中途掉线'
          % (agg['conn_ok'], total_clients, agg['lost_conn']), flush=True)
    print('error rate = errors/attempts = %d/%d = %.3f%%'
          % (errors, attempts, err_rate), flush=True)
    print('  breakdown: conn_err=%d lost_conn=%d probe_timeout=%d probe_emit_err=%d '
          'action_emit_err=%d act_timeout=%d'
          % (agg['conn_err'], agg['lost_conn'], agg['probe_timeout'], agg['probe_emit_err'],
             agg['action_emit_err'], agg['act_timeout']), flush=True)
    if agg['err_kinds']:
        print('  kinds: %s' % json.dumps(agg['err_kinds'], ensure_ascii=False), flush=True)
    print()
    print('VERDICT (target: P99 < 200ms, error rate < 1%)', flush=True)
    # 先判"本轮是否有效": 一个请求都没发出去时 errors=0 -> 错误率 0% -> 看起来 PASS。
    # 这是最危险的假阳性, 必须在这之前拦掉。
    valid = (agg['conn_ok'] > 0 and attempts > 0)
    if not valid:
        print('  !! 本轮数据无效: conn_ok=%d, attempts=%d -> 不予判定 (不是 PASS)'
              % (agg['conn_ok'], attempts), flush=True)
    if ra:
        print('  [A] P99 = %.2f ms  ->  %s' % (ra['p99'], 'PASS' if ra['p99'] < 200 else 'FAIL'), flush=True)
    if rb:
        print('  [B] P99 = %.2f ms  ->  %s' % (rb['p99'], 'PASS' if rb['p99'] < 200 else 'FAIL'), flush=True)
    if valid:
        print('  error rate = %.3f%%  ->  %s' % (err_rate, 'PASS' if err_rate < 1.0 else 'FAIL'), flush=True)
    else:
        print('  error rate = 无法评估 (本轮无效)', flush=True)
    print()
    print('RESULT_JSON ' + json.dumps({
        'boot_mode': BOOT_MODE, 'transport_hint': TRANSPORT_HINT,
        'wall_s': round(wall, 1), 'conn_ok': agg['conn_ok'], 'conn_err': agg['conn_err'],        'transport': agg['transport'], 'actions': agg['actions'], 'states': agg['states'],
        'states_resp': agg['states_resp'], 'states_tick': agg['states_tick'],
        'restarts': agg['restarts'], 'probes_sent': agg['probes_sent'],
        'stats_replies': agg['stats_replies'], 'act_timeout': agg['act_timeout'],
        'lost_conn': agg['lost_conn'], 'total_clients': cfg['total_clients'],
        'errors': errors, 'attempts': attempts, 'err_rate_pct': round(err_rate, 4),
        'valid': valid, 'connect_ramp': cfg.get('connect_ramp', 0),
        'rtt_a': ra, 'rtt_b': rb, 'connect_ms': rc, 'err_kinds': agg['err_kinds'],
    }), flush=True)


if __name__ == '__main__':
    main()
