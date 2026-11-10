from flask import Flask, send_from_directory, request, jsonify
from flask_socketio import SocketIO, emit
from player_manager import player_manager
import threading
import hashlib
import time
from datetime import datetime
from prometheus_flask_exporter import PrometheusMetrics

app = Flask(__name__, static_folder='static')
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-only-insecure-secret')
metrics = PrometheusMetrics(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# ===== 业务指标（启动即存在: 无标签 Counter/Gauge 在 import 时即产生样本）=====
import os
from prometheus_client import Counter, Gauge

GAME_START         = Counter('tetris_game_start',         '累计开局次数')
GAME_START_FAILED  = Counter('tetris_game_start_failed',  '累计开局失败次数')
GAME_OVER          = Counter('tetris_game_over',          '累计结束局数')
GAME_RECORD_SAVED  = Counter('tetris_game_record_saved',  '累计游戏记录落库成功次数')
DB_ERRORS          = Counter('tetris_db_errors',          '累计数据库操作错误次数')
ERRORS             = Counter('tetris_errors',             '累计业务错误数（含 HTTP 5xx）')
AUTH_TOTAL         = Counter('tetris_auth',               '累计认证请求数（注册+登录）')
AUTH_FAILED        = Counter('tetris_auth_failed',        '累计认证失败次数')

WS_CONNECTIONS     = Gauge('tetris_ws_connections',       '当前 WebSocket 连接数')
ACTIVE_PLAYERS     = Gauge('tetris_players_active',       '当前活跃玩家数')
TOTAL_PLAYERS      = Gauge('tetris_players_total',        '累计创建玩家数')

UP                 = Gauge('tetris_up',                   '应用存活（启动即 1）')
UP.set(1)
APP_START_TS       = Gauge('tetris_app_start_time_seconds', '应用启动时间（Unix 时间戳）')
APP_START_TS.set(time.time())

@app.after_request
def _count_http_5xx(response):
    if response.status_code >= 500:
        ERRORS.inc()
    return response

# 存储定时器
fall_timers = {}

# 游戏速度配置
FALL_SPEEDS = {
    1: 500, 2: 450, 3: 400, 4: 350, 5: 300,
    6: 250, 7: 200, 8: 150, 9: 100, 10: 80
}

def get_fall_speed(level: int) -> int:
    """根据等级获取下落速度"""
    return FALL_SPEEDS.get(min(level, 10), 100)

def start_fall_timer(sid: str):
    """开始下落计时器"""
    global fall_timers
    
    # 清除现有计时器
    if sid in fall_timers:
        fall_timers[sid].cancel()
    
    # 获取玩家游戏实例
    game = player_manager.get_player_by_sid(sid)
    if not game or game.game_over or game.paused:
        return
    
    def fall():
        # 重新获取游戏实例（防止引用过期）
        game = player_manager.get_player_by_sid(sid)
        if game and not game.game_over and not game.paused:
            game.move_down()
            # 发送游戏状态更新
            socketio.emit('game_state', game.get_game_state(), room=sid)
            
            # 重新启动计时器
            if not game.game_over:
                start_fall_timer(sid)
    
    speed = get_fall_speed(game.level)
    timer = threading.Timer(speed / 1000.0, fall)
    timer.daemon = True
    timer.start()
    fall_timers[sid] = timer

@app.route('/')
def index():
    """提供前端页面"""
    return send_from_directory('static', 'index.html')

@app.route('/<path:path>')
def static_files(path):
    """提供静态文件"""
    return send_from_directory('static', path)

@app.route('/api/statistics')
def api_statistics():
    """获取服务器统计信息API"""
    stats = player_manager.get_statistics()
    return jsonify({
        'success': True,
        'statistics': stats
    })

@app.route('/api/leaderboard')
def api_leaderboard():
    """获取排行榜API"""
    limit = request.args.get('limit', 10, type=int)
    players = player_manager.get_top_players(limit)
    return jsonify({
        'success': True,
        'players': players,
        'total': len(players)
    })

@socketio.on('connect')
def handle_connect():
    """处理客户端连接"""
    sid = request.sid
    
    # 使用PlayerManager创建新玩家
    game = player_manager.create_player(sid)
    player_id = player_manager.get_player_id(sid)
    ACTIVE_PLAYERS.set(player_manager.active_players)
    WS_CONNECTIONS.set(player_manager.active_players)
    TOTAL_PLAYERS.set(player_manager.total_players)
    
    print(f"[WebSocket] 玩家连接: {player_id} (SID: {sid})")
    
    # 发送玩家ID和游戏状态
    emit('connected', {
        'player_id': player_id,
        'game_state': game.get_game_state(),
        'statistics': player_manager.get_statistics()
    })
    
    # 开始下落计时器
    start_fall_timer(sid)

@socketio.on('disconnect')
def handle_disconnect():
    """处理客户端断开连接"""
    sid = request.sid
    
    # 清除计时器
    if sid in fall_timers:
        fall_timers[sid].cancel()
        del fall_timers[sid]
    
    # 使用PlayerManager移除玩家
    player_id = player_manager.get_player_id(sid)
    if player_id:
        print(f"[WebSocket] 玩家断开: {player_id} (SID: {sid})")
        player_manager.remove_player(sid)
        ACTIVE_PLAYERS.set(max(player_manager.active_players, 0))
        WS_CONNECTIONS.set(max(player_manager.active_players, 0))
        
        # 广播更新后的统计信息给所有玩家
        socketio.emit('statistics_update', player_manager.get_statistics())

@socketio.on('move_left')
def handle_move_left():
    """处理左移"""
    sid = request.sid
    game = player_manager.get_player_by_sid(sid)
    
    if game:
        game.move_left()
        emit('game_state', game.get_game_state(), room=sid)

@socketio.on('move_right')
def handle_move_right():
    """处理右移"""
    sid = request.sid
    game = player_manager.get_player_by_sid(sid)
    
    if game:
        game.move_right()
        emit('game_state', game.get_game_state(), room=sid)

@socketio.on('move_down')
def handle_move_down():
    """处理下移"""
    sid = request.sid
    game = player_manager.get_player_by_sid(sid)
    
    if game:
        game.move_down()
        emit('game_state', game.get_game_state(), room=sid)
        
        # 重置计时器
        if not game.game_over:
            start_fall_timer(sid)

@socketio.on('rotate')
def handle_rotate():
    """处理旋转"""
    sid = request.sid
    game = player_manager.get_player_by_sid(sid)
    
    if game:
        game.rotate_piece()
        emit('game_state', game.get_game_state(), room=sid)

@socketio.on('hard_drop')
def handle_hard_drop():
    """处理硬到底"""
    sid = request.sid
    game = player_manager.get_player_by_sid(sid)
    
    if game:
        game.hard_drop()
        emit('game_state', game.get_game_state(), room=sid)
        
        # 重置计时器
        if not game.game_over:
            start_fall_timer(sid)

@socketio.on('toggle_pause')
def handle_toggle_pause():
    """处理暂停/继续"""
    sid = request.sid
    game = player_manager.get_player_by_sid(sid)
    
    if game:
        game.toggle_pause()
        emit('game_state', game.get_game_state(), room=sid)
        
        # 处理计时器
        if game.paused:
            if sid in fall_timers:
                fall_timers[sid].cancel()
        else:
            start_fall_timer(sid)

@socketio.on('restart')
def handle_restart():
    """处理重新开始"""
    sid = request.sid
    game = player_manager.get_player_by_sid(sid)
    
    if game:
        game.restart()
        emit('game_state', game.get_game_state(), room=sid)
        
        # 重置计时器
        if sid in fall_timers:
            fall_timers[sid].cancel()
        start_fall_timer(sid)

@socketio.on('get_leaderboard')
def handle_get_leaderboard():
    """获取排行榜（WebSocket方式）"""
    sid = request.sid
    players = player_manager.get_top_players(10)
    emit('leaderboard', {'players': players}, room=sid)

@socketio.on('get_statistics')
def handle_get_statistics():
    """获取统计信息（WebSocket方式）"""
    sid = request.sid
    stats = player_manager.get_statistics()
    emit('statistics', stats, room=sid)

@socketio.on('save_game_record')
def handle_save_game_record(data):
    """保存游戏记录"""
    sid = request.sid
    game = player_manager.get_player_by_sid(sid)
    player_id = player_manager.get_player_id(sid)
    
    if game and player_id:
        # 保存游戏记录到数据库
        try:
            player_manager.storage.save_game_record(
                player_id=player_id,
                score=game.score,
                lines=game.lines,
                level=game.level,
                duration=data.get('duration', 0)
            )
            GAME_RECORD_SAVED.inc()
            emit('record_saved', {'success': True}, room=sid)
        except Exception:
            DB_ERRORS.inc()
            ERRORS.inc()
            raise

# 添加登录注册路由
@app.route('/game')
def game_page():
    """游戏页面"""
    return send_from_directory('static', 'game.html')

@app.route('/api/register', methods=['POST'])
def api_register():
    """用户注册API"""
    data = request.json
    username = data.get('username', '').strip()
    password = data.get('password', '')
    nickname = data.get('nickname', '').strip()

    if not username or not password:
        return jsonify({'success': False, 'message': '用户名和密码不能为空'})

    if len(password) < 6:
        return jsonify({'success': False, 'message': '密码长度至少6位'})

    # 创建用户
    AUTH_TOTAL.inc()
    try:
        user = player_manager.storage.create_user(username, password, nickname)
    except Exception:
        DB_ERRORS.inc(); ERRORS.inc(); raise

    if user:
        return jsonify({'success': True, 'user': user})
    else:
        AUTH_FAILED.inc()
        return jsonify({'success': False, 'message': '用户名已存在'})

@app.route('/api/login', methods=['POST'])
def api_login():
    """用户登录API"""
    data = request.json
    username = data.get('username', '').strip()
    password = data.get('password', '')

    if not username or not password:
        return jsonify({'success': False, 'message': '用户名和密码不能为空'})

    # 验证用户
    AUTH_TOTAL.inc()
    try:
        user = player_manager.storage.verify_user(username, password)
    except Exception:
        DB_ERRORS.inc(); ERRORS.inc(); raise

    if user:
        return jsonify({
            'success': True,
            'user_id': user['id'],
            'username': user['username'],
            'nickname': user['nickname'],
            'high_score': user['high_score']
        })
    else:
        AUTH_FAILED.inc()
        return jsonify({'success': False, 'message': '用户名或密码错误'})

# 添加认证WebSocket事件
@socketio.on('auth')
def handle_auth(data):
    """用户认证"""
    sid = request.sid
    user_id = data.get('user_id')

    if user_id:
        # 为已登录用户创建游戏实例
        game = player_manager.create_player_with_user(sid, user_id)
        player_id = player_manager.get_player_id(sid)

        # 获取用户信息并更新统计
        user_info = player_manager.storage.get_user_by_id(user_id)

        emit('connected', {
            'player_id': player_id,
            'game_state': game.get_game_state(),
            'user_info': user_info
        })
        print(f"[WebSocket] 用户 {user_id} 已认证，游戏ID: {player_id}")

@socketio.on('start_game')
def handle_start_game():
    """开始游戏"""
    sid = request.sid
    game = player_manager.get_player_by_sid(sid)

    if game and not game.game_started:
        game.game_started = True
        # 开始下落计时器
        start_fall_timer(sid)
        emit('game_started', room=sid)
        GAME_START.inc()
        print(f"[WebSocket] 游戏开始: {sid}")
    else:
        # 【埋点修复】开局没成功: 这条原本永远不会被计数,
        # 导致 game-start-failure-ratio 的分子恒为 0。
        GAME_START_FAILED.inc()
        ERRORS.inc()
        print(f"[WebSocket] 开局失败: sid={sid} game_exists={game is not None}",
              flush=True)

# 修改 move_down 事件，添加游戏开始检查
@socketio.on('move_down')
def handle_move_down():
    """处理下移"""
    sid = request.sid
    game = player_manager.get_player_by_sid(sid)

    if game and game.game_started and not game.game_over and not game.paused:
        game.move_down()
        emit('game_state', game.get_game_state(), room=sid)

        if not game.game_over:
            start_fall_timer(sid)

# 修改其他移动事件，添加游戏开始检查
@socketio.on('move_left')
def handle_move_left():
    sid = request.sid
    game = player_manager.get_player_by_sid(sid)
    if game and game.game_started and not game.game_over and not game.paused:
        game.move_left()
        emit('game_state', game.get_game_state(), room=sid)

@socketio.on('move_right')
def handle_move_right():
    sid = request.sid
    game = player_manager.get_player_by_sid(sid)
    if game and game.game_started and not game.game_over and not game.paused:
        game.move_right()
        emit('game_state', game.get_game_state(), room=sid)

@socketio.on('rotate')
def handle_rotate():
    sid = request.sid
    game = player_manager.get_player_by_sid(sid)
    if game and game.game_started and not game.game_over and not game.paused:
        game.rotate_piece()
        emit('game_state', game.get_game_state(), room=sid)

@socketio.on('hard_drop')
def handle_hard_drop():
    sid = request.sid
    game = player_manager.get_player_by_sid(sid)
    if game and game.game_started and not game.game_over and not game.paused:
        game.hard_drop()
        emit('game_state', game.get_game_state(), room=sid)
        if not game.game_over:
            start_fall_timer(sid)

# ==============================================================================
#  【埋点修复】Socket.IO 异常兑底
#
#  原来只有 @app.after_request 统计 HTTP 5xx, 而业务流量 100% 走 websocket,
#  所以所有 socket 侧的异常对监控完全不可见。
#  加上这个之后, business-error-rate / exceptions 才能看到业务层错误。
# ==============================================================================
@socketio.on_error_default
def _default_socket_error(e):
    ERRORS.inc()
    print(f"[SocketIO] 事件处理异常: {type(e).__name__}: {e}", flush=True)


if __name__ == '__main__':
    print("=" * 50)
    print("俄罗斯方块游戏服务器启动")
    print(f"WebSocket服务: ws://localhost:5000")
    print(f"HTTP服务: http://localhost:5000")
    print("=" * 50)
    socketio.run(app, host='0.0.0.0', port=5000, debug=False, allow_unsafe_werkzeug=True)
