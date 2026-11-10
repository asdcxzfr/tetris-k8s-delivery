import os
import threading
from typing import Dict, Optional
from game_logic import TetrisGame
from data_storage import MySQLStorage
import uuid
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class PlayerManager:
    """玩家管理器"""
    
    def __init__(self, storage=None):
        self.games: Dict[str, TetrisGame] = {}
        self.sid_to_player: Dict[str, str] = {}
        self.player_to_sid: Dict[str, str] = {}
        self.lock = threading.Lock()
        self.total_players = 0
        self.active_players = 0
        
        # 使用MySQL存储
        if storage is None:
            self.storage = MySQLStorage(
                host=os.environ.get('DB_HOST', '127.0.0.1'),
                port=int(os.environ.get('DB_PORT', '3306')),
                user=os.environ.get('DB_USER', 'root'),
                password=os.environ.get('DB_PASSWORD', ''),
                database=os.environ.get('DB_NAME', 'tetris_game')
            )
        else:
            self.storage = storage
        
        # 测试连接
        if not self.storage.test_connection():
            logger.error("数据库连接失败！")
        else:
            logger.info("数据库连接成功！")
        
        logger.info("[PlayerManager] 初始化完成")
    
    def create_player(self, socket_id: str, nickname: str = None) -> TetrisGame:
        """创建新玩家"""
        with self.lock:
            player_id = str(uuid.uuid4())
            
            # 加载已有统计（如果有）
            saved_stats = self.storage.load_player_stats(player_id)
            
            # 创建游戏实例
            game = TetrisGame(player_id)
            
            if not nickname:
                nickname = f"Player_{player_id[:8]}"
            
            # 存储映射
            self.games[player_id] = game
            self.sid_to_player[socket_id] = player_id
            self.player_to_sid[player_id] = socket_id
            
            # 更新统计
            self.total_players += 1
            self.active_players += 1
            
            # 保存玩家信息
            player_stats = {
                'player_id': player_id,
                'nickname': nickname,
                'total_games': (saved_stats.get('total_games', 0) if saved_stats else 0) + 1
            }
            self.storage.save_player_stats(player_id, player_stats)
            
            logger.info(f"[PlayerManager] 玩家创建: {player_id} ({nickname})")
            return game
    
    def get_player_by_sid(self, socket_id: str) -> Optional[TetrisGame]:
        """通过Socket ID获取玩家"""
        with self.lock:
            player_id = self.sid_to_player.get(socket_id)
            if player_id:
                return self.games.get(player_id)
            return None
    
    def get_player_id(self, socket_id: str) -> Optional[str]:
        """通过Socket ID获取玩家ID"""
        with self.lock:
            return self.sid_to_player.get(socket_id)
    
    def remove_player(self, socket_id: str, save_state: bool = True) -> bool:
        """移除玩家"""
        with self.lock:
            player_id = self.sid_to_player.get(socket_id)
            if player_id:
                game = self.games.get(player_id)
                
                if save_state and game and not game.game_over:
                    # 保存最终统计
                    stats = self.storage.load_player_stats(player_id) or {}
                    stats['high_score'] = max(stats.get('high_score', 0), game.score)
                    stats['total_score'] = stats.get('total_score', 0) + game.score
                    stats['total_lines'] = stats.get('total_lines', 0) + game.lines
                    stats['best_level'] = max(stats.get('best_level', 0), game.level)
                    
                    self.storage.save_player_stats(player_id, stats)
                    
                    # 保存游戏记录
                    self.storage.save_game_record(
                        player_id=player_id,
                        score=game.score,
                        lines=game.lines,
                        level=game.level,
                        duration=0  # 可以记录游戏时长
                    )
                
                # 删除映射
                if player_id in self.games:
                    del self.games[player_id]
                if socket_id in self.sid_to_player:
                    del self.sid_to_player[socket_id]
                if player_id in self.player_to_sid:
                    del self.player_to_sid[player_id]
                
                self.active_players -= 1
                logger.info(f"[PlayerManager] 玩家移除: {player_id}")
                return True
            return False
    
    def get_top_players(self, limit: int = 10) -> list:
        """获取排行榜"""
        return self.storage.get_leaderboard(limit)
    
    def get_statistics(self) -> Dict:
        """获取统计信息"""
        db_stats = self.storage.get_statistics()
        return {
            'active_players': self.active_players,
            'total_players': self.total_players,
            **db_stats
        }
    # 在 PlayerManager 类中添加

    def create_player_with_user(self, socket_id: str, user_id: int) -> TetrisGame:
        """为已登录用户创建游戏实例"""
        with self.lock:
            # 使用 user_id 作为 player_id 的一部分
            player_id = f"user_{user_id}_{int(time.time())}"
            
            # 获取用户信息
            user_info = self.storage.get_user_by_id(user_id)
            
            # 创建游戏实例
            game = TetrisGame(player_id)
            game.user_id = user_id
            game.nickname = user_info['nickname'] if user_info else f"Player_{user_id}"
            
            # 存储映射
            self.games[player_id] = game
            self.sid_to_player[socket_id] = player_id
            self.player_to_sid[player_id] = socket_id
            
            self.total_players += 1
            self.active_players += 1
            
            logger.info(f"[PlayerManager] 用户 {user_id} 开始游戏，游戏ID: {player_id}")
            return game
    
    def get_user_id_by_sid(self, socket_id: str) -> Optional[int]:
        """通过Socket ID获取用户ID"""
        with self.lock:
            player_id = self.sid_to_player.get(socket_id)
            if player_id:
                game = self.games.get(player_id)
                if game and hasattr(game, 'user_id'):
                    return game.user_id
            return None
  
# 创建全局实例
player_manager = PlayerManager()
