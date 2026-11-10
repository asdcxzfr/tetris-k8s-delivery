class TetrisClient {
    constructor() {
        this.canvas = document.getElementById('gameCanvas');
        this.ctx = this.canvas.getContext('2d');
        this.nextCanvas = document.getElementById('nextCanvas');
        this.nextCtx = this.nextCanvas.getContext('2d');
        
        this.blockSize = 30;
        this.nextBlockSize = 24;
        this.socket = null;
        this.playerId = null;
        this.gameState = null;
        this.gameStarted = false;  // 新增：游戏是否已开始
        
        // 检查登录状态
        this.userId = sessionStorage.getItem('user_id');
        this.username = sessionStorage.getItem('username');
        this.nickname = sessionStorage.getItem('nickname');
        
        if (!this.userId) {
            window.location.href = '/';
            return;
        }
        
        document.getElementById('playerName').textContent = this.nickname || this.username;
        
        this.initSocket();
        this.initEventListeners();
        this.initStartButton();
    }
    
    initSocket() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const host = window.location.host;
        this.socket = io(`${protocol}//${host}`, {
            path: '/socket.io',
            transports: ['websocket', 'polling'],
            reconnection: true
        });
        
        this.socket.on('connect', () => {
            console.log('Connected to server');
            // 发送用户ID给服务器
            this.socket.emit('auth', { user_id: this.userId });
        });
        
        this.socket.on('connected', (data) => {
            this.playerId = data.player_id;
            this.gameState = data.game_state;
            document.getElementById('playerId').textContent = '游戏ID: ' + this.playerId.substring(0, 8);
            this.draw();
            this.updateUI();
            this.showMessage('游戏已准备就绪，点击"开始游戏"开始！', 'info');
        });
        
        this.socket.on('game_state', (gameState) => {
            this.gameState = gameState;
            this.draw();
            this.updateUI();
            
            // 游戏结束后禁用开始按钮
            if (this.gameState.game_over) {
                document.getElementById('startBtn').disabled = false;
                document.getElementById('startBtn').textContent = '▶ 重新开始';
                this.gameStarted = false;
            }
        });
        
        this.socket.on('game_started', () => {
            this.gameStarted = true;
            document.getElementById('startBtn').disabled = true;
            document.getElementById('startBtn').textContent = '游戏中...';
            this.showMessage('游戏开始！', 'success');
        });
        
        this.socket.on('disconnect', () => {
            this.showMessage('与服务器断开连接', 'error');
        });
    }
    
    initStartButton() {
        const startBtn = document.getElementById('startBtn');
        startBtn.addEventListener('click', () => {
            if (!this.gameStarted && this.gameState && !this.gameState.game_over) {
                this.socket.emit('start_game');
            } else if (this.gameState && this.gameState.game_over) {
                this.socket.emit('restart');
                this.socket.emit('start_game');
            }
        });
    }
    
    sendAction(action) {
        // 只有游戏开始后才能发送操作
        if (!this.gameStarted && action !== 'restart') {
            this.showMessage('请先点击"开始游戏"！', 'info');
            return;
        }
        if (this.socket && this.gameState && !this.gameState.game_over) {
            this.socket.emit(action);
        }
    }
    
    draw() {
        if (!this.gameState) return;
        
        this.ctx.fillStyle = '#1a1a2e';
        this.ctx.fillRect(0, 0, this.canvas.width, this.canvas.height);
        
        // 绘制网格线
        this.ctx.strokeStyle = '#16213e';
        this.ctx.lineWidth = 0.5;
        for (let x = 0; x <= 10; x++) {
            this.ctx.beginPath();
            this.ctx.moveTo(x * this.blockSize, 0);
            this.ctx.lineTo(x * this.blockSize, this.canvas.height);
            this.ctx.stroke();
        }
        for (let y = 0; y <= 20; y++) {
            this.ctx.beginPath();
            this.ctx.moveTo(0, y * this.blockSize);
            this.ctx.lineTo(this.canvas.width, y * this.blockSize);
            this.ctx.stroke();
        }
        
        // 绘制已固定的方块
        const grid = this.gameState.grid;
        for (let y = 0; y < grid.length; y++) {
            for (let x = 0; x < grid[y].length; x++) {
                if (grid[y][x]) {
                    this.ctx.fillStyle = grid[y][x];
                    this.ctx.fillRect(x * this.blockSize, y * this.blockSize, 
                                     this.blockSize - 1, this.blockSize - 1);
                    this.ctx.fillStyle = 'rgba(255, 255, 255, 0.3)';
                    this.ctx.fillRect(x * this.blockSize, y * this.blockSize, 
                                     this.blockSize - 1, 3);
                }
            }
        }
        
        // 绘制当前方块（只有游戏开始后才显示）
        if (this.gameStarted && this.gameState.current_piece) {
            const piece = this.gameState.current_piece;
            for (let y = 0; y < piece.shape.length; y++) {
                for (let x = 0; x < piece.shape[y].length; x++) {
                    if (piece.shape[y][x]) {
                        this.ctx.fillStyle = piece.color;
                        const drawX = (piece.x + x) * this.blockSize;
                        const drawY = (piece.y + y) * this.blockSize;
                        this.ctx.fillRect(drawX, drawY, this.blockSize - 1, this.blockSize - 1);
                        this.ctx.fillStyle = 'rgba(255, 255, 255, 0.3)';
                        this.ctx.fillRect(drawX, drawY, this.blockSize - 1, 3);
                    }
                }
            }
        }
        
        // 游戏未开始时显示提示
        if (!this.gameStarted && !this.gameState.game_over) {
            this.ctx.fillStyle = 'rgba(0, 0, 0, 0.7)';
            this.ctx.fillRect(0, 0, this.canvas.width, this.canvas.height);
            this.ctx.fillStyle = '#ffd700';
            this.ctx.font = 'bold 20px Arial';
            this.ctx.textAlign = 'center';
            this.ctx.fillText('点击"开始游戏"', this.canvas.width / 2, this.canvas.height / 2);
            this.ctx.font = '14px Arial';
            this.ctx.fillText('开始挑战', this.canvas.width / 2, this.canvas.height / 2 + 30);
        }
        
        // 游戏结束遮罩
        if (this.gameState.game_over) {
            this.ctx.fillStyle = 'rgba(0, 0, 0, 0.7)';
            this.ctx.fillRect(0, 0, this.canvas.width, this.canvas.height);
            this.ctx.fillStyle = '#fff';
            this.ctx.font = 'bold 24px Arial';
            this.ctx.textAlign = 'center';
            this.ctx.fillText('GAME OVER', this.canvas.width / 2, this.canvas.height / 2);
        }
        
        // 暂停遮罩
        if (this.gameState.paused && !this.gameState.game_over && this.gameStarted) {
            this.ctx.fillStyle = 'rgba(0, 0, 0, 0.5)';
            this.ctx.fillRect(0, 0, this.canvas.width, this.canvas.height);
            this.ctx.fillStyle = '#ffd700';
            this.ctx.font = 'bold 24px Arial';
            this.ctx.textAlign = 'center';
            this.ctx.fillText('PAUSED', this.canvas.width / 2, this.canvas.height / 2);
        }
        
        this.drawNextPiece();
    }
    
    drawNextPiece() {
        this.nextCtx.fillStyle = '#1a1a2e';
        this.nextCtx.fillRect(0, 0, this.nextCanvas.width, this.nextCanvas.height);
        
        if (this.gameState && this.gameState.next_piece) {
            const shape = this.gameState.next_piece.shape;
            const color = this.gameState.next_piece.color;
            const offsetX = (this.nextCanvas.width - shape[0].length * this.nextBlockSize) / 2;
            const offsetY = (this.nextCanvas.height - shape.length * this.nextBlockSize) / 2;
            
            for (let y = 0; y < shape.length; y++) {
                for (let x = 0; x < shape[y].length; x++) {
                    if (shape[y][x]) {
                        this.nextCtx.fillStyle = color;
                        this.nextCtx.fillRect(offsetX + x * this.nextBlockSize,
                                             offsetY + y * this.nextBlockSize,
                                             this.nextBlockSize - 1, this.nextBlockSize - 1);
                    }
                }
            }
        }
    }
    
    updateUI() {
        if (!this.gameState) return;
        document.getElementById('score').textContent = this.gameState.score;
        document.getElementById('lines').textContent = this.gameState.lines;
        document.getElementById('level').textContent = this.gameState.level;
    }
    
    showMessage(msg, type) {
        const msgDiv = document.getElementById('statusMessage');
        msgDiv.textContent = msg;
        msgDiv.style.color = type === 'error' ? '#ff6b6b' : '#4ade80';
        setTimeout(() => {
            if (msgDiv.textContent === msg) {
                msgDiv.textContent = '';
            }
        }, 3000);
    }
    
    initEventListeners() {
        document.addEventListener('keydown', (e) => {
            if (!this.socket || !this.gameState) return;
            
            if (this.gameState.game_over && e.key !== 'r' && e.key !== 'R') {
                return;
            }
            
            switch(e.key) {
                case 'ArrowLeft':
                    e.preventDefault();
                    this.sendAction('move_left');
                    break;
                case 'ArrowRight':
                    e.preventDefault();
                    this.sendAction('move_right');
                    break;
                case 'ArrowDown':
                    e.preventDefault();
                    this.sendAction('move_down');
                    break;
                case 'ArrowUp':
                    e.preventDefault();
                    this.sendAction('rotate');
                    break;
                case ' ':
                case 'Space':
                    e.preventDefault();
                    this.sendAction('hard_drop');
                    break;
                case 'p':
                case 'P':
                    e.preventDefault();
                    this.sendAction('toggle_pause');
                    break;
                case 'r':
                case 'R':
                    e.preventDefault();
                    this.sendAction('restart');
                    break;
            }
        });
        
        // 退出登录
        document.getElementById('logoutBtn').addEventListener('click', () => {
            sessionStorage.clear();
            window.location.href = '/';
        });
    }
}

window.addEventListener('DOMContentLoaded', () => {
    if (typeof io !== 'undefined') {
        new TetrisClient();
    } else {
        console.error('Socket.IO library not loaded');
    }
});
