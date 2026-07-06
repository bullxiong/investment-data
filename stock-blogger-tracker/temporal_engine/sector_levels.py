"""
M6: 板块点位追踪 — 云起的大盘/板块级别关键点位管理

数据来源: 云起大盘分析 (如 0701大盘.docx, 0702大盘.docx)
追踪内容: 申万二级行业指数的 反弹支撑位/压力位/目标位
用途: LLM 日报的"明日关注"中，提示接近关键点位的板块
"""
import json, os
from datetime import date as dt_date, timedelta
from temporal_engine.db import get_conn

SCHEMA = """
CREATE TABLE IF NOT EXISTS sector_levels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sector_name TEXT NOT NULL,           -- 申万二级行业名, 如 '工程机械'
    signal_type TEXT NOT NULL,           -- 'support'(支撑/反弹) | 'resistance'(压力) | 'target'(目标)
    index_level REAL NOT NULL,           -- 关键点位, 如 1000.0
    signal_date TEXT NOT NULL,           -- 云起提出该信号的日期 'YYYY-MM-DD'
    original_text TEXT,                  -- 云起原文摘要
    current_level REAL,                  -- 最新指数点位 (手动/行情更新)
    current_date TEXT,                   -- 点位更新日期
    status TEXT DEFAULT 'pending',       -- 'pending'(待触发) | 'approaching'(接近中) | 'triggered'(已触发) | 'expired'(已失效)
    triggered_date TEXT,                 -- 触发日期
    proximity_pct REAL,                  -- 当前价距关键位的百分比
    notes TEXT,                          -- 备注
    UNIQUE(sector_name, signal_type, index_level, signal_date)
);

CREATE INDEX IF NOT EXISTS idx_sl_sector ON sector_levels(sector_name);
CREATE INDEX IF NOT EXISTS idx_sl_status ON sector_levels(status);
"""


def init():
    conn = get_conn()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


def add_level(sector_name, signal_type, index_level, signal_date, original_text=''):
    """添加一条板块关键点位"""
    conn = get_conn()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO sector_levels (sector_name, signal_type, index_level, signal_date, original_text, status) "
            "VALUES (?, ?, ?, ?, ?, 'pending')",
            (sector_name, signal_type, index_level, signal_date, original_text[:500])
        )
        conn.commit()
    finally:
        conn.close()


def update_current(sector_name, current_level, current_date=None):
    """更新板块最新指数点位，同时重新计算 proximity_pct"""
    if current_date is None:
        current_date = dt_date.today().isoformat()

    conn = get_conn()
    # 找到该板块所有 pending/approaching 的记录
    rows = conn.execute(
        "SELECT id, index_level, signal_type FROM sector_levels "
        "WHERE sector_name=? AND status IN ('pending', 'approaching')",
        (sector_name,)
    ).fetchall()

    for r in rows:
        proximity = (current_level - r['index_level']) / r['index_level'] * 100
        new_status = 'pending'
        if r['signal_type'] == 'support':
            # 支撑位: 接近下方时触发
            if proximity <= 5:
                new_status = 'approaching'
            if proximity <= 1:
                new_status = 'triggered'
        elif r['signal_type'] == 'resistance':
            # 压力位: 接近上方时触发
            if proximity >= -5:
                new_status = 'approaching'
            if proximity >= -1:
                new_status = 'triggered'

        conn.execute(
            "UPDATE sector_levels SET current_level=?, current_date=?, proximity_pct=?, status=? WHERE id=?",
            (current_level, current_date, round(proximity, 1), new_status, r['id'])
        )
    conn.commit()
    conn.close()


def get_approaching(days=14):
    """获取最近N天内接近关键点位的板块（用于 LLM '明日关注'）"""
    cutoff = (dt_date.today() - timedelta(days=days)).isoformat()
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM sector_levels WHERE status IN ('approaching', 'triggered') AND signal_date >= ? "
        "ORDER BY ABS(proximity_pct) ASC",
        (cutoff,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_by_sector(sector_name, days=30):
    """获取某板块的所有关键点位"""
    cutoff = (dt_date.today() - timedelta(days=days)).isoformat()
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM sector_levels WHERE sector_name=? AND signal_date >= ? ORDER BY signal_date DESC",
        (sector_name, cutoff)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─── 预填充: 从云起样本数据中提取的板块关键点位 ───

SEED_DATA = [
    # 来源: 0701大盘 / 0702大盘
    ('工程机械', 'support', 1000, '2026-07-01',
     '明确下跌结构中的反弹，不具备上行条件。待后期跌至1000点附近形成底部再考虑操作'),
    ('工程机械', 'target', 1000, '2026-07-01',
     '1000点关口将出现批量抢反弹机会'),

    ('半导体', 'support', 10000, '2026-07-01',
     '波段目标11573已兑现。预计进入大级别调整，回落至10000点附近'),
    ('半导体', 'support', 10620, '2026-07-01',
     '跌破周二低点意味着本轮上涨结束。10620附近守住后震荡下探10000点'),

    ('通信设备', 'support', 7000, '2026-07-01',
     '走势走坏，破位回踩。短期看跌，目标位下看至7000点'),
    ('通信设备', 'resistance', 7500, '2026-07-02',
     '跌至7000点附近反弹，反弹目标看至7500点'),

    ('证券', 'support', 1800, '2026-07-01',
     '双底突破后的洗盘结束。三天内回踩不破1800可看涨至1900'),
    ('证券', 'resistance', 1900, '2026-07-01',
     '1900点压力较重，触及后回落概率大'),

    ('光学光电子', 'resistance', 2000, '2026-07-01',
     '2000点为重要关口，行情在此阶段性触顶'),
    ('光学光电子', 'support', 1800, '2026-07-01',
     '调整关注1800点支撑'),

    ('光伏', 'support', 500, '2026-07-02',
     '跌破540关键位，进入新一波下跌。首先看500点支撑'),
    ('光伏', 'target', 453, '2026-07-02',
     '结构性目标可能至453点附近，需重视此风险'),

    ('消费电子', 'resistance', 6000, '2026-07-01',
     '6000点压力位导致回落。长期震荡构成上涨中继'),
    ('消费电子', 'support', 5400, '2026-07-02',
     '大幅跳水至5400点附近，上方密集区构成压力'),

    ('电池', 'support', 900, '2026-07-01',
     '卡在900-1000之间震荡，方向不明'),
    ('电池', 'resistance', 1000, '2026-07-01',
     '1000点关口压制'),

    ('机器人', 'support', 5000, '2026-07-02',
     '跌破5000点，按4800-5200区间跟踪横盘震荡'),
    ('机器人', 'resistance', 5200, '2026-07-02',
     '5200点震荡上轨'),

    ('计算机设备', 'resistance', 2000, '2026-07-01',
     '1960-2000区域遇阻回落。震荡后有望再次试探2000'),
    ('计算机设备', 'support', 1845, '2026-07-01',
     '下方支撑关注1845点'),

    ('化学制药', 'support', 2500, '2026-07-02',
     '反弹站上2500点后将在此震荡，处于大底部箱体内'),

    ('创新药', 'resistance', 1050, '2026-07-02',
     '通道上轨约1050点，1000点关口将拖累行情。短期空间有限，切忌追高'),

    ('医疗服务', 'support', 5000, '2026-07-01',
     '突破下降通道并站上5000点，进一步看涨至5300'),
    ('医疗服务', 'target', 5300, '2026-07-01',
     '后续观察5000-5300区间震荡情况'),
]


def seed():
    """预填充板块关键点位（从云起大盘分析中提取）"""
    init()
    for sector, stype, level, date_str, text in SEED_DATA:
        add_level(sector, stype, level, date_str, text)
    print('[sector_levels] Seeded %d records' % len(SEED_DATA))


# 手动更新当前指数点位（后续可接行情 API）
def update_all_current(levels_map):
    """
    批量更新板块最新指数点位。
    levels_map: {'工程机械': 1118, '半导体': 10500, ...}
    """
    for sector, level in levels_map.items():
        update_current(sector, level)
