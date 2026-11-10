
import os, sys, threading, time


def _boot():
    try:
        import requests  # noqa
        return 'system'
    except Exception:
        pass
    try:
        from pip._vendor import requests
    except Exception:
        return 'FAILED'
    sys.modules.setdefault('requests', requests)
    for n in ('urllib3', 'certifi', 'idna', 'charset_normalizer', 'chardet'):
        try:
            sys.modules.setdefault(n, __import__('pip._vendor.' + n, fromlist=[n]))
        except Exception:
            pass
    return 'pip-vendored'


BOOT = _boot()
try:
    import websocket  # noqa
    WSTRS = [['websocket'], ['polling']]
except Exception:
    WSTRS = [['polling']]

import socketio  # noqa

N = int(os.environ.get('N', '10'))
WAIT = float(os.environ.get('WAIT', '8'))
URLS = [u for u in os.environ.get('URLS', '').split(',') if u]


def one(url, out, lock):
    r = {'connected': False, 'ok': False, 'err': None, 'tr': None}
    sio = None
    for tr in WSTRS:
        started = threading.Event()
        c = socketio.Client(reconnection=False, request_timeout=15)
        c.on('game_started', lambda d=None: started.set())
        try:
            c.connect(url, transports=tr, wait_timeout=15)
            sio, r['connected'], r['tr'] = c, True, tr[0]
            break
        except Exception as ex:
            r['err'] = type(ex).__name__
            try:
                c.disconnect()
            except Exception:
                pass
    if r['connected']:
        try:
            sio.emit('start_game')
            r['ok'] = started.wait(WAIT)
        except Exception as ex:
            r['err'] = 'emit:' + type(ex).__name__
    try:
        sio.disconnect()
    except Exception:
        pass
    with lock:
        out.append(r)


print('  boot=%s  transports=%s' % (BOOT, WSTRS[0][0]), flush=True)
for url in URLS:
    out, lock = [], threading.Lock()
    ths = [threading.Thread(target=one, args=(url, out, lock)) for _ in range(N)]
    t0 = time.time()
    for t in ths:
        t.start()
    for t in ths:
        t.join(timeout=45)
    ok = sum(1 for r in out if r['ok'])
    conn = sum(1 for r in out if r['connected'])
    errs = {}
    for r in out:
        if r['err']:
            errs[r['err']] = errs.get(r['err'], 0) + 1
    print('  %-58s 连上 %d/%d   开局成功 %d/%d   (%.1fs)  errs=%s'
          % (url, conn, N, ok, N, time.time() - t0, errs), flush=True)
print('PROBE_DONE', flush=True)
