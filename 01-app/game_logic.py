import random
import json
from typing import Dict, List, Optional

class TetrisGame:
    """俄罗斯方块游戏核心逻辑"""
    
    def __init__(self, player_id: str):
        self.player_id = player_id
        self.game_started = False
        self.grid_width = 10
        self.grid_height = 20
        self.grid = [[0 for _ in range(self.grid_width)] for _ in range(self.grid_height)]
        self.current_piece = None
        self.next_piece = None
        self.score = 0
        self.lines = 0
        self.level = 1
        self.game_over = False
        self.paused = False
        
        # 方块形状定义
        self.shapes = {
            'I': {'shape': [[1, 1, 1, 1]], 'color': '#00e5f0'},
            'O': {'shape': [[1, 1], [1, 1]], 'color': '#f0e000'},
            'T': {'shape': [[0, 1, 0], [1, 1, 1]], 'color': '#c084fc'},
            'S': {'shape': [[0, 1, 1], [1, 1, 0]], 'color': '#4ade80'},
            'Z': {'shape': [[1, 1, 0], [0, 1, 1]], 'color': '#f87171'},
            'J': {'shape': [[1, 0, 0], [1, 1, 1]], 'color': '#60a5fa'},
            'L': {'shape': [[0, 0, 1], [1, 1, 1]], 'color': '#fb923c'}
        }
        
        self.shape_list = list(self.shapes.keys())
        self.spawn_new_piece()
    
    def get_random_piece(self) -> Dict:
        """获取随机方块"""
        piece_type = random.choice(self.shape_list)
        piece = self.shapes[piece_type]
        return {
            'shape': [row[:] for row in piece['shape']],
            'color': piece['color'],
            'type': piece_type
        }
    
    def spawn_new_piece(self):
        """生成新方块"""
        if not self.next_piece:
            self.next_piece = self.get_random_piece()
        
        self.current_piece = {
            'shape': [row[:] for row in self.next_piece['shape']],
            'color': self.next_piece['color'],
            'type': self.next_piece['type'],
            'x': self.grid_width // 2 - len(self.next_piece['shape'][0]) // 2,
            'y': 0
        }
        
        self.next_piece = self.get_random_piece()
        
        # 检查游戏是否结束
        if self.check_collision():
            self.game_over = True
    
    def check_collision(self, shape: Optional[List] = None, offset_x: Optional[int] = None, 
                       offset_y: Optional[int] = None) -> bool:
        """检查碰撞"""
        shape = shape if shape is not None else self.current_piece['shape']
        offset_x = offset_x if offset_x is not None else self.current_piece['x']
        offset_y = offset_y if offset_y is not None else self.current_piece['y']
        
        for y, row in enumerate(shape):
            for x, cell in enumerate(row):
                if cell:
                    grid_x = offset_x + x
                    grid_y = offset_y + y
                    if (grid_x < 0 or grid_x >= self.grid_width or
                        grid_y >= self.grid_height or
                        (grid_y >= 0 and self.grid[grid_y][grid_x])):
                        return True
        return False
    
    def merge_piece(self):
        """固定当前方块"""
        for y, row in enumerate(self.current_piece['shape']):
            for x, cell in enumerate(row):
                if cell:
                    grid_x = self.current_piece['x'] + x
                    grid_y = self.current_piece['y'] + y
                    if 0 <= grid_y < self.grid_height and 0 <= grid_x < self.grid_width:
                        self.grid[grid_y][grid_x] = self.current_piece['color']
        
        self.clear_lines()
        self.spawn_new_piece()
    
    def start_fall_timer(sid: str):
        """开始下落计时器"""
        global fall_timers
        
        if sid in fall_timers:
            fall_timers[sid].cancel()
        
        game = player_manager.get_player_by_sid(sid)
        if not game or not game.game_started or game.game_over or game.paused:
            return
        
        def fall():
            game = player_manager.get_player_by_sid(sid)
            if game and game.game_started and not game.game_over and not game.paused:
                game.move_down()
                socketio.emit('game_state', game.get_game_state(), room=sid)
                if not game.game_over:
                    start_fall_timer(sid)
        
        speed = get_fall_speed(game.level)
        timer = threading.Timer(speed / 1000.0, fall)
        timer.daemon = True
        timer.start()
        fall_timers[sid] = timer


    def clear_lines(self):
        """消除完整的行"""
        lines_cleared = 0
        y = self.grid_height - 1
        
        while y >= 0:
            if all(self.grid[y]):
                del self.grid[y]
                self.grid.insert(0, [0 for _ in range(self.grid_width)])
                lines_cleared += 1
            else:
                y -= 1
        
        if lines_cleared > 0:
            # 更新分数
            points = {1: 100, 2: 300, 3: 500, 4: 800}
            self.score += points.get(lines_cleared, 0) * self.level
            self.lines += lines_cleared
            
            # 更新等级
            new_level = self.lines // 10 + 1
            if new_level > self.level:
                self.level = new_level
    
    def move_left(self) -> bool:
        """向左移动"""
        if not self.game_over and not self.paused:
            new_x = self.current_piece['x'] - 1
            if not self.check_collision(offset_x=new_x):
                self.current_piece['x'] = new_x
                return True
        return False
    
    def move_right(self) -> bool:
        """向右移动"""
        if not self.game_over and not self.paused:
            new_x = self.current_piece['x'] + 1
            if not self.check_collision(offset_x=new_x):
                self.current_piece['x'] = new_x
                return True
        return False
    
    def move_down(self) -> bool:
        """向下移动"""
        if not self.game_over and not self.paused:
            new_y = self.current_piece['y'] + 1
            if not self.check_collision(offset_y=new_y):
                self.current_piece['y'] = new_y
                return True
            else:
                self.merge_piece()
                return False
        return False
    
    def rotate_piece(self) -> bool:
        """旋转方块"""
        if not self.game_over and not self.paused:
            # 旋转90度
            shape = self.current_piece['shape']
            rotated = [[shape[y][x] for y in range(len(shape))] 
                      for x in range(len(shape[0]) - 1, -1, -1)]
            
            if not self.check_collision(shape=rotated):
                self.current_piece['shape'] = rotated
                return True
        return False
    
    def hard_drop(self):
        """硬到底"""
        if not self.game_over and not self.paused:
            while not self.check_collision(offset_y=self.current_piece['y'] + 1):
                self.current_piece['y'] += 1
            self.merge_piece()
    
    def toggle_pause(self):
        """暂停/继续"""
        if not self.game_over:
            self.paused = not self.paused
    
    def restart(self):
        """重新开始"""
        self.grid = [[0 for _ in range(self.grid_width)] for _ in range(self.grid_height)]
        self.score = 0
        self.lines = 0
        self.level = 1
        self.game_over = False
        self.paused = False
        self.spawn_new_piece()
    
    def get_game_state(self) -> Dict:
        """获取当前游戏状态"""
        return {
            'grid': self.grid,
            'current_piece': self.current_piece,
            'next_piece': self.next_piece,
            'score': self.score,
            'lines': self.lines,
            'level': self.level,
            'game_over': self.game_over,
            'paused': self.paused
        }
