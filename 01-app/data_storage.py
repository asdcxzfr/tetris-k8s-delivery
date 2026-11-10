import json
import os
from datetime import datetime
from typing import Dict, List, Optional
from sqlalchemy import create_engine, Column, String, Integer, DateTime, Text, inspect, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

Base = declarative_base()

class PlayerStats(Base):
    """玩家统计数据表"""
    __tablename__ = 'player_stats'
    
    player_id = Column(String(36), primary_key=True)
    nickname = Column(String(50), default='Anonymous')
    high_score = Column(Integer, default=0)
    total_games = Column(Integer, default=0)
    total_lines = Column(Integer, default=0)
    total_score = Column(Integer, default=0)
    best_level = Column(Integer, default=0)
    win_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.now)
    last_played = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    extra_data = Column(Text, nullable=True)

class GameState(Base):
    """游戏状态表"""
    __tablename__ = 'game_states'
    
    player_id = Column(String(36), primary_key=True)
    game_state = Column(Text)
    last_save = Column(DateTime, default=datetime.now, onupdate=datetime.now)

class GameRecords(Base):
    """游戏对局记录表"""
    __tablename__ = 'game_records'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    player_id = Column(String(36))
    score = Column(Integer)
    lines = Column(Integer)
    level = Column(Integer)
    duration = Column(Integer)
    played_at = Column(DateTime, default=datetime.now)

class User(Base):
    """用户表"""
    __tablename__ = 'users'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(50), unique=True, nullable=False)
    password = Column(String(255), nullable=False)
    nickname = Column(String(50), default='')
    email = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    last_login = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    total_score = Column(Integer, default=0)
    total_games = Column(Integer, default=0)
    high_score = Column(Integer, default=0)

class MySQLStorage:
    """MySQL存储实现"""
    
    def __init__(self, host='localhost', port=3306, user='root', 
                 password='', database='tetris_game', charset='utf8mb4'):
        
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.database = database
        self.charset = charset
        
        # 先连接到MySQL服务器（不指定数据库）
        self._create_database_if_not_exists()
        
        # 创建数据库连接
        self._init_engine()
        
        # 创建所有表
        self._create_tables()
        
        logger.info(f"[MySQLStorage] 初始化完成: {host}:{port}/{database}")
    
    def _create_database_if_not_exists(self):
        """自动创建数据库（如果不存在）"""
        try:
            temp_engine = create_engine(
                f'mysql+pymysql://{self.user}:{self.password}@{self.host}:{self.port}',
                pool_pre_ping=True
            )
            
            with temp_engine.connect() as conn:
                result = conn.execute(
                    text("SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA WHERE SCHEMA_NAME = :db_name"),
                    {"db_name": self.database}
                )
                exists = result.fetchone()
                
                if not exists:
                    conn.execute(text(f"CREATE DATABASE {self.database} CHARACTER SET {self.charset}"))
                    conn.commit()
                    logger.info(f"[MySQLStorage] 数据库 '{self.database}' 创建成功")
                else:
                    logger.info(f"[MySQLStorage] 数据库 '{self.database}' 已存在")
            
            temp_engine.dispose()
            
        except Exception as e:
            logger.error(f"[MySQLStorage] 创建数据库失败: {e}")
            raise
    
    def _init_engine(self):
        """初始化数据库引擎"""
        db_url = (
            f'mysql+pymysql://{self.user}:{self.password}'
            f'@{self.host}:{self.port}/{self.database}'
            f'?charset={self.charset}'
        )
        
        self.engine = create_engine(
            db_url,
            pool_pre_ping=True,
            pool_recycle=3600,
            echo=False
        )
        
        self.Session = sessionmaker(bind=self.engine)
        logger.info("[MySQLStorage] 数据库引擎初始化成功")
    
    def _create_tables(self):
        """自动创建所有表"""
        try:
            Base.metadata.create_all(self.engine)
            inspector = inspect(self.engine)
            tables = inspector.get_table_names()
            logger.info(f"[MySQLStorage] 已创建表: {', '.join(tables)}")
        except Exception as e:
            logger.error(f"[MySQLStorage] 创建表失败: {e}")
            raise
    
    def save_game_state(self, player_id: str, game_state: Dict) -> bool:
        """保存游戏状态"""
        session = self.Session()
        try:
            game_state_record = session.query(GameState).filter_by(player_id=player_id).first()
            
            if game_state_record:
                game_state_record.game_state = json.dumps(game_state)
                game_state_record.last_save = datetime.now()
            else:
                game_state_record = GameState(
                    player_id=player_id,
                    game_state=json.dumps(game_state)
                )
                session.add(game_state_record)
            
            session.commit()
            logger.debug(f"[MySQLStorage] 游戏状态已保存: {player_id}")
            return True
        except Exception as e:
            session.rollback()
            logger.error(f"[MySQLStorage] 保存游戏状态失败: {e}")
            return False
        finally:
            session.close()
    
    def load_game_state(self, player_id: str) -> Optional[Dict]:
        """加载游戏状态"""
        session = self.Session()
        try:
            game_state_record = session.query(GameState).filter_by(player_id=player_id).first()
            if game_state_record and game_state_record.game_state:
                return json.loads(game_state_record.game_state)
            return None
        except Exception as e:
            logger.error(f"[MySQLStorage] 加载游戏状态失败: {e}")
            return None
        finally:
            session.close()
    
    def save_player_stats(self, player_id: str, stats: Dict) -> bool:
        """保存玩家统计"""
        session = self.Session()
        try:
            player_stats = session.query(PlayerStats).filter_by(player_id=player_id).first()
            
            if player_stats:
                for key, value in stats.items():
                    if hasattr(player_stats, key) and key != 'player_id':
                        if key in ['total_games', 'total_score', 'total_lines', 'win_count']:
                            current_value = getattr(player_stats, key, 0)
                            setattr(player_stats, key, current_value + value)
                        elif key in ['high_score', 'best_level']:
                            current_value = getattr(player_stats, key, 0)
                            setattr(player_stats, key, max(current_value, value))
                        else:
                            setattr(player_stats, key, value)
                player_stats.last_played = datetime.now()
            else:
                stats_copy = {k: v for k, v in stats.items() if k != 'player_id'}
                player_stats = PlayerStats(player_id=player_id, **stats_copy)
                session.add(player_stats)
            
            session.commit()
            logger.info(f"[MySQLStorage] 玩家统计已保存: {player_id}")
            return True
        except Exception as e:
            session.rollback()
            logger.error(f"[MySQLStorage] 保存玩家统计失败: {e}")
            return False
        finally:
            session.close()
    
    def load_player_stats(self, player_id: str) -> Optional[Dict]:
        """加载玩家统计"""
        session = self.Session()
        try:
            player_stats = session.query(PlayerStats).filter_by(player_id=player_id).first()
            
            if player_stats:
                return {
                    'player_id': player_stats.player_id,
                    'nickname': player_stats.nickname,
                    'high_score': player_stats.high_score,
                    'total_games': player_stats.total_games,
                    'total_lines': player_stats.total_lines,
                    'total_score': player_stats.total_score,
                    'best_level': player_stats.best_level,
                    'win_count': player_stats.win_count,
                    'created_at': player_stats.created_at.isoformat() if player_stats.created_at else None,
                    'last_played': player_stats.last_played.isoformat() if player_stats.last_played else None
                }
            return None
        except Exception as e:
            logger.error(f"[MySQLStorage] 加载玩家统计失败: {e}")
            return None
        finally:
            session.close()
    
    def save_game_record(self, player_id: str, score: int, lines: int, level: int, duration: int) -> bool:
        """保存游戏对局记录"""
        session = self.Session()
        try:
            record = GameRecords(
                player_id=player_id,
                score=score,
                lines=lines,
                level=level,
                duration=duration
            )
            session.add(record)
            session.commit()
            logger.info(f"[MySQLStorage] 游戏记录已保存: {player_id} - {score}分")
            return True
        except Exception as e:
            session.rollback()
            logger.error(f"[MySQLStorage] 保存游戏记录失败: {e}")
            return False
        finally:
            session.close()
    
    def get_leaderboard(self, limit: int = 10) -> List[Dict]:
        """获取排行榜"""
        session = self.Session()
        try:
            players = session.query(PlayerStats).order_by(
                PlayerStats.high_score.desc()
            ).limit(limit).all()
            
            return [
                {
                    'player_id': p.player_id,
                    'nickname': p.nickname or p.player_id[:8],
                    'high_score': p.high_score,
                    'total_games': p.total_games,
                    'total_lines': p.total_lines,
                    'best_level': p.best_level,
                    'last_played': p.last_played.isoformat() if p.last_played else None
                }
                for p in players
            ]
        except Exception as e:
            logger.error(f"[MySQLStorage] 获取排行榜失败: {e}")
            return []
        finally:
            session.close()
    
    def get_player_recent_records(self, player_id: str, limit: int = 10) -> List[Dict]:
        """获取玩家最近的对局记录"""
        session = self.Session()
        try:
            records = session.query(GameRecords).filter_by(
                player_id=player_id
            ).order_by(
                GameRecords.played_at.desc()
            ).limit(limit).all()
            
            return [
                {
                    'score': r.score,
                    'lines': r.lines,
                    'level': r.level,
                    'duration': r.duration,
                    'played_at': r.played_at.isoformat() if r.played_at else None
                }
                for r in records
            ]
        except Exception as e:
            logger.error(f"[MySQLStorage] 获取玩家记录失败: {e}")
            return []
        finally:
            session.close()
    
    def get_statistics(self) -> Dict:
        """获取全局统计信息"""
        session = self.Session()
        try:
            total_players = session.query(PlayerStats).count()
            total_games_result = session.query(PlayerStats.total_games).all()
            total_games_sum = sum([g[0] for g in total_games_result]) if total_games_result else 0
            
            highest_score_player = session.query(PlayerStats).order_by(
                PlayerStats.high_score.desc()
            ).first()
            
            total_lines_result = session.query(PlayerStats.total_lines).all()
            total_lines_sum = sum([l[0] for l in total_lines_result]) if total_lines_result else 0
            
            return {
                'total_players': total_players,
                'total_games': total_games_sum,
                'total_lines': total_lines_sum,
                'highest_score': highest_score_player.high_score if highest_score_player else 0,
                'highest_score_player': highest_score_player.nickname if highest_score_player else None
            }
        except Exception as e:
            logger.error(f"[MySQLStorage] 获取统计信息失败: {e}")
            return {}
        finally:
            session.close()
    
    def delete_player_data(self, player_id: str) -> bool:
        """删除玩家所有数据"""
        session = self.Session()
        try:
            session.query(GameState).filter_by(player_id=player_id).delete()
            session.query(PlayerStats).filter_by(player_id=player_id).delete()
            session.query(GameRecords).filter_by(player_id=player_id).delete()
            session.commit()
            logger.info(f"[MySQLStorage] 玩家数据已删除: {player_id}")
            return True
        except Exception as e:
            session.rollback()
            logger.error(f"[MySQLStorage] 删除玩家数据失败: {e}")
            return False
        finally:
            session.close()
    
    def test_connection(self) -> bool:
        """测试数据库连接"""
        try:
            session = self.Session()
            session.execute(text("SELECT 1"))
            session.close()
            logger.info("[MySQLStorage] 数据库连接测试成功")
            return True
        except Exception as e:
            logger.error(f"[MySQLStorage] 数据库连接测试失败: {e}")
            return False
    
    # ========== 用户管理方法 ==========
    
    def create_user(self, username: str, password: str, nickname: str = None) -> Optional[Dict]:
        """创建新用户"""
        import hashlib
        
        session = self.Session()
        try:
            # 检查用户名是否已存在
            existing = session.query(User).filter_by(username=username).first()
            if existing:
                logger.warning(f"[MySQLStorage] 用户名已存在: {username}")
                return None
            
            # 密码加密（使用SHA256）
            hashed_password = hashlib.sha256(password.encode()).hexdigest()
            
            user = User(
                username=username,
                password=hashed_password,
                nickname=nickname or username
            )
            session.add(user)
            session.commit()
            
            logger.info(f"[MySQLStorage] 用户创建成功: {username}")
            return {
                'id': user.id,
                'username': user.username,
                'nickname': user.nickname,
                'created_at': user.created_at.isoformat()
            }
        except Exception as e:
            session.rollback()
            logger.error(f"[MySQLStorage] 创建用户失败: {e}")
            return None
        finally:
            session.close()
    
    def verify_user(self, username: str, password: str) -> Optional[Dict]:
        """验证用户登录"""
        import hashlib
        
        session = self.Session()
        try:
            hashed_password = hashlib.sha256(password.encode()).hexdigest()
            user = session.query(User).filter_by(
                username=username, 
                password=hashed_password
            ).first()
            
            if user:
                # 更新最后登录时间
                user.last_login = datetime.now()
                session.commit()
                
                logger.info(f"[MySQLStorage] 用户登录成功: {username}")
                return {
                    'id': user.id,
                    'username': user.username,
                    'nickname': user.nickname,
                    'high_score': user.high_score,
                    'total_games': user.total_games,
                    'total_score': user.total_score
                }
            else:
                logger.warning(f"[MySQLStorage] 用户登录失败: {username}")
                return None
        except Exception as e:
            logger.error(f"[MySQLStorage] 验证用户失败: {e}")
            return None
        finally:
            session.close()
    
    def get_user_by_id(self, user_id: int) -> Optional[Dict]:
        """通过ID获取用户信息"""
        session = self.Session()
        try:
            user = session.query(User).filter_by(id=user_id).first()
            if user:
                return {
                    'id': user.id,
                    'username': user.username,
                    'nickname': user.nickname,
                    'high_score': user.high_score,
                    'total_games': user.total_games,
                    'total_score': user.total_score,
                    'created_at': user.created_at.isoformat() if user.created_at else None,
                    'last_login': user.last_login.isoformat() if user.last_login else None
                }
            return None
        except Exception as e:
            logger.error(f"[MySQLStorage] 获取用户失败: {e}")
            return None
        finally:
            session.close()
    
    def get_user_by_username(self, username: str) -> Optional[Dict]:
        """通过用户名获取用户信息"""
        session = self.Session()
        try:
            user = session.query(User).filter_by(username=username).first()
            if user:
                return {
                    'id': user.id,
                    'username': user.username,
                    'nickname': user.nickname,
                    'high_score': user.high_score,
                    'total_games': user.total_games,
                    'total_score': user.total_score
                }
            return None
        except Exception as e:
            logger.error(f"[MySQLStorage] 获取用户失败: {e}")
            return None
        finally:
            session.close()
    
    def update_user_stats(self, user_id: int, score: int, lines: int, level: int) -> bool:
        """更新用户统计"""
        session = self.Session()
        try:
            user = session.query(User).filter_by(id=user_id).first()
            if user:
                user.total_games += 1
                user.total_score += score
                if score > user.high_score:
                    user.high_score = score
                session.commit()
                logger.info(f"[MySQLStorage] 用户统计更新: user_id={user_id}, score={score}")
                return True
            return False
        except Exception as e:
            session.rollback()
            logger.error(f"[MySQLStorage] 更新用户统计失败: {e}")
            return False
        finally:
            session.close()
