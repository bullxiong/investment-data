"""
M4: 时序交叉验证 — 检测六大交叉验证模式
"""
from datetime import date as dt_date, timedelta
from temporal_engine.db import get_conn


# 六大模式定义
CV_PATTERNS = {
    'CV-1': {
        'name': '先宏后微',
        'desc': '博主先达成共识 → 云起后续给出具体价位信号',
        'rating': 'S级', 'action': '正常仓位',
    },
    'CV-2': {
        'name': '先微后宏',
        'desc': '云起先给信号 → 博主关注度随后增加',
        'rating': 'A级', 'action': '半仓试探',
    },
    'CV-3': {
        'name': '仅有微观',
        'desc': '云起给信号但博主无人覆盖',
        'rating': 'B级', 'action': '轻仓',
    },
    'CV-4': {
        'name': '仅有宏观',
        'desc': '博主看多但云起无确认信号',
        'rating': '观察', 'action': '等待/观望',
    },
    'CV-5': {
        'name': '宏观转弱',
        'desc': '博主从共识转向分歧, 但云起信号仍存续',
        'rating': '警告', 'action': '减仓',
    },
    'CV-6': {
        'name': '双弱',
        'desc': '博主看空 + 云起回避',
        'rating': '回避', 'action': '不做',
    },
}


def verify(concept, reference_date=None, cloud_signals=None):
    """
    对单个概念做时序交叉验证。
    
    Args:
        concept: 概念名
        reference_date: 参考日期
        cloud_signals: {concept: {'stage': 'B', 'entry_price': 14.0, ...}}
                       云起老师对该概念的信号
    
    Returns:
        dict: {'concept': str, 'pattern': str, 'rating': str, 'action': str, ...}
    """
    if reference_date is None:
        reference_date = dt_date.today().isoformat()
    if cloud_signals is None:
        cloud_signals = {}

    conn = get_conn()
    # 博主数据: 近14天
    rows = conn.execute(
        "SELECT date, author_id, sentiment FROM signal_timeline "
        "WHERE concept=? AND date >= date(?, '-14 days') AND date <= ? "
        "ORDER BY date",
        (concept, reference_date, reference_date)
    ).fetchall()

    # 统计博主覆盖
    bullish_authors = set()
    bearish_authors = set()
    all_authors = set()

    for r in rows:
        all_authors.add(r['author_id'])
        if r['sentiment'] == 'bullish':
            bullish_authors.add(r['author_id'])
        elif r['sentiment'] == 'bearish':
            bearish_authors.add(r['author_id'])

    blogger_bullish = len(bullish_authors)
    blogger_total = len(all_authors)
    blogger_bearish = len(bearish_authors)

    # 云起数据
    cloud_data = cloud_signals.get(concept, {})
    has_cloud_signal = bool(cloud_data)  # 有具体价位信号
    cloud_stage = cloud_data.get('stage', '')

    # 判定模式
    if blogger_bullish >= 3 and has_cloud_signal:
        pattern = 'CV-1'
    elif has_cloud_signal and blogger_bullish >= 1 and blogger_bullish < 3:
        pattern = 'CV-2'
    elif has_cloud_signal and blogger_bullish == 0:
        pattern = 'CV-3'
    elif not has_cloud_signal and blogger_bullish >= 1:
        pattern = 'CV-4'
    elif blogger_bearish > blogger_bullish and has_cloud_signal:
        pattern = 'CV-5'
    elif blogger_bearish >= blogger_bullish and not has_cloud_signal:
        pattern = 'CV-6'
    else:
        # 默认: 按博主覆盖数给基础评级
        pattern = 'CV-4'

    info = CV_PATTERNS.get(pattern, CV_PATTERNS['CV-4'])
    conn.close()

    return {
        'concept': concept,
        'pattern': pattern,
        'pattern_name': info['name'],
        'rating': info['rating'],
        'action': info['action'],
        'blogger_bullish': blogger_bullish,
        'blogger_bearish': blogger_bearish,
        'blogger_total': blogger_total,
        'has_cloud_signal': has_cloud_signal,
        'cloud_stage': cloud_stage,
    }
