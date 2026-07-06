"""
测试种子数据生成 — 10天 × 5概念 × 5博主 的模拟信号
覆盖: 慢共识/快共识/先宏后微/仅有宏观/退潮
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from temporal_engine.db import init_db, get_conn

# 博主名单 (模拟5个博主ID)
BLOGGERS = [
    ('xueqiu:7251377368', '白河愁'),
    ('xueqiu:1034624503', '雪球大V'),
    ('zhihu:xiao-peng', '派大星'),
    ('zsxq:511158', '波king'),
    ('zsxq:288882', '奥特之父'),
]

# 5个概念, 每个有不同的时序特征
SCENARIOS = {
    '证券': {
        'type': '慢共识',
        'events': {
            '2026-06-25': [0],               # 白河愁1人看多
            '2026-06-26': [0],               # 继续
            '2026-06-27': [0, 2],            # 派大星加入
            '2026-06-28': [0, 2, 3],         # 波king加入 → 3人共识
            '2026-06-29': [0, 2, 3],         # 维持
            '2026-06-30': [0, 2, 3, 1],      # 雪球大V加入 → 4人
            '2026-07-01': [0, 1, 2, 3],      # 维持4人
            '2026-07-02': [0, 1, 2, 3, 4],   # 全员看多
            '2026-07-03': [0, 1, 2, 3, 4],   # 持续
            '2026-07-04': [0, 1, 2, 3],      # 1人转neutral
        },
    },
    'AI应用': {
        'type': '爆款共识',
        'events': {
            '2026-07-01': [0],                # 白河愁1人
            '2026-07-02': [0, 1, 2, 3, 4],   # 突然全员看多
            '2026-07-03': [0, 1, 2, 3, 4],   # 维持
            '2026-07-04': [0, 1, 2, 3, 4],   # 维持
        },
    },
    '半导体': {
        'type': '先宏后微',
        'events': {
            '2026-06-27': [0],               # 1人
            '2026-06-28': [0, 2],            # 2人
            '2026-06-29': [0, 2, 3],         # 3人共识
            '2026-06-30': [0, 2, 3],         # 维持
            '2026-07-01': [0, 2, 3, 1],      # 4人
            '2026-07-02': [0, 1, 2, 3],      # 4人
            '2026-07-03': [0, 1, 2, 3, 4],   # 5人
            '2026-07-04': [0, 1, 2, 3, 4],   # 5人, 云起确认(B类信号)
        },
    },
    '新能源': {
        'type': '仅有宏观',
        'events': {
            '2026-06-28': [0, 2],            # 2人
            '2026-06-29': [0, 2],            # 维持2人
            '2026-06-30': [0, 2, 3],         # 3人共识
            '2026-07-01': [0, 2, 3],         # 维持
            '2026-07-02': [0, 2, 3],         # 维持
            '2026-07-03': [0, 2],            # 1人退出
            '2026-07-04': [0, 2],            # 2人, 无云起信号
        },
    },
    '通信设备': {
        'type': '退潮',
        'events': {
            '2026-06-25': [0, 1, 2],         # 3人看多
            '2026-06-26': [0, 1, 2],         # 维持
            '2026-06-27': [0, 1],            # 1人退出
            '2026-06-28': [0],               # 只剩1人
            '2026-06-29': [],                 # 无人提及
            '2026-06-30': [],                 # 无人
            '2026-07-01': [],                 # 无人
            '2026-07-02': [],                 # 无人
            '2026-07-03': [],                 # 无人
            '2026-07-04': [],                 # 无人
        },
    },
}


def generate():
    """生成模拟信号并写入 temporal.db"""
    init_db()
    conn = get_conn()

    # 清空旧数据
    conn.execute("DELETE FROM signal_timeline")

    count = 0
    for concept, scenario in SCENARIOS.items():
        for date_str, blogger_indices in scenario['events'].items():
            for idx in blogger_indices:
                author_id, author_name = BLOGGERS[idx]
                source = author_id.split(':')[0]

                # 通信设备后期转 bearish, 模拟退潮
                if concept == '通信设备':
                    if date_str >= '2026-06-28':
                        sentiment = 'bearish'
                        direction = 'short'
                    elif date_str >= '2026-06-27':
                        sentiment = 'neutral'
                        direction = 'watch'
                    else:
                        sentiment = 'bullish'
                        direction = 'long'
                else:
                    sentiment = 'bullish'
                    direction = 'long'

                conn.execute(
                    "INSERT OR IGNORE INTO signal_timeline "
                    "(date, concept, source, author, author_id, sentiment, direction, confidence, article_count) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)",
                    (date_str, concept, source, author_name, author_id, sentiment, direction, 0.75)
                )
                count += 1

    conn.commit()

    # 验证
    rows = conn.execute("SELECT COUNT(*) FROM signal_timeline").fetchone()
    conn.close()

    print(f"[seed] Generated {count} signals ({rows[0]} rows in DB)")
    print(f"  Concepts: {list(SCENARIOS.keys())}")
    print(f"  Date range: 2026-06-25 ~ 2026-07-04")
    types = ', '.join(f'{k}({v["type"]})' for k, v in SCENARIOS.items())
    print(f'  Scenarios: {types}')
    return count


if __name__ == '__main__':
    generate()
