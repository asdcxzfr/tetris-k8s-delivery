#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""测试MySQL连接和数据存储"""

import os

from data_storage import MySQLStorage

def test_mysql():
    print("=" * 50)
    print("测试MySQL存储功能")
    print("=" * 50)
    
    # 创建存储实例
    storage = MySQLStorage(
        host=os.environ.get('DB_HOST', '127.0.0.1'),
        port=int(os.environ.get('DB_PORT', '3306')),
        user=os.environ.get('DB_USER', 'root'),
        password=os.environ.get('DB_PASSWORD', ''),
        database=os.environ.get('DB_NAME', 'tetris_game')
    )
    
    # 测试连接
    if storage.test_connection():
        print("✓ 数据库连接成功")
    else:
        print("✗ 数据库连接失败")
        return
    
    # 测试保存玩家统计
    test_player_id = "test_player_001"
    stats = {
        'player_id': test_player_id,
        'nickname': 'TestPlayer',
        'high_score': 1000,
        'total_games': 1,
        'total_lines': 5,
        'total_score': 1000,
        'best_level': 3
    }
    
    if storage.save_player_stats(test_player_id, stats):
        print("✓ 保存玩家统计成功")
    else:
        print("✗ 保存玩家统计失败")
    
    # 测试加载玩家统计
    loaded_stats = storage.load_player_stats(test_player_id)
    if loaded_stats:
        print(f"✓ 加载玩家统计成功: {loaded_stats}")
    else:
        print("✗ 加载玩家统计失败")
    
    # 测试保存游戏记录
    if storage.save_game_record(test_player_id, 1000, 5, 3, 120):
        print("✓ 保存游戏记录成功")
    else:
        print("✗ 保存游戏记录失败")
    
    # 测试排行榜
    leaderboard = storage.get_leaderboard(5)
    print(f"✓ 排行榜获取成功，共 {len(leaderboard)} 条记录")
    for i, player in enumerate(leaderboard, 1):
        print(f"  {i}. {player['nickname']}: {player['high_score']}分")
    
    # 测试全局统计
    stats = storage.get_statistics()
    print(f"✓ 全局统计: {stats}")
    
    print("=" * 50)
    print("测试完成！")

if __name__ == '__main__':
    test_mysql()
