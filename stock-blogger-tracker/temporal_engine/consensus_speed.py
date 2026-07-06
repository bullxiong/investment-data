"""
M3: 共识速度检测 — 区分慢共识(健康)和爆款共识(羊群风险)
"""
from datetime import date as dt_date, timedelta
from temporal_engine.db import get_conn


def analyze(concept, reference_date=None):
    """
    分析一个概念的共识建立速度。
    
    Returns:
        dict: {'concept': str, 'type': str, 'days_to_consensus': int, 
               'acceleration': float, 'credibility': str, 'action': str}
    """
    if reference_date is None:
        reference_date = dt_date.today().isoformat()
    ref = dt_date.fromisoformat(reference_date)

    conn = get_conn()
    rows = conn.execute(
        "SELECT date, author_id, sentiment FROM signal_timeline "
        "WHERE concept=? AND date >= date(?, '-30 days') AND date <= ? "
        "ORDER BY date",
        (concept, reference_date, reference_date)
    ).fetchall()

    if not rows:
        return {
            'concept': concept, 'type': '无数据', 'days_to_consensus': None,
            'acceleration': 0, 'credibility': 'N/A', 'action': '不关注',
        }

    # 按日期统计每日新增的看多作者
    from collections import defaultdict
    daily_bullish = defaultdict(set)  # date -> set of author_ids
    first_date = None

    for r in rows:
        d = r['date']
        if first_date is None:
            first_date = d
        if r['sentiment'] == 'bullish':
            daily_bullish[d].add(r['author_id'])

    if not daily_bullish:
        return {
            'concept': concept, 'type': '无看多信号', 'days_to_consensus': None,
            'acceleration': 0, 'credibility': 'N/A', 'action': '观望',
        }

    # 累积看多作者
    all_bullish = set()
    days = sorted(daily_bullish.keys())
    consensus_date = None

    for d in days:
        all_bullish.update(daily_bullish[d])
        if len(all_bullish) >= 3 and consensus_date is None:
            consensus_date = d

    if consensus_date is None:
        # 尚未达到共识
        return {
            'concept': concept, 'type': '未达共识',
            'days_to_consensus': None,
            'acceleration': 0,
            'credibility': 'N/A',
            'action': '继续观察',
            'current_bullish_authors': len(all_bullish),
        }

    # 计算建立天数
    days_to_consensus = (dt_date.fromisoformat(consensus_date) - dt_date.fromisoformat(first_date)).days

    # 计算加速率: 近3天新增 / 前3天新增
    # 简化: 用后半段 vs 前半段的新增速度比
    mid = len(days) // 2
    first_half_dates = days[:mid]
    second_half_dates = days[mid:]

    first_half_added = set()
    for d in first_half_dates:
        first_half_added.update(daily_bullish[d])

    second_half_added = set()
    for d in second_half_dates:
        second_half_added.update(daily_bullish[d])

    # 归一化到每天
    fh_rate = len(first_half_added) / max(len(first_half_dates), 1)
    sh_rate = len(second_half_added) / max(len(second_half_dates), 1)

    acceleration = sh_rate / max(fh_rate, 0.01)

    # 分类
    if days_to_consensus >= 5 and acceleration < 2.0:
        ctype, cred, action = '慢共识', '高', '正常仓位'
    elif days_to_consensus <= 2 and acceleration >= 3.0:
        ctype, cred, action = '爆款', '低', '观望/回避'
    else:
        ctype, cred, action = '快共识', '中', '轻仓试探'

    conn.close()

    return {
        'concept': concept,
        'type': ctype,
        'days_to_consensus': days_to_consensus,
        'acceleration': round(acceleration, 1),
        'credibility': cred,
        'action': action,
        'first_mention': first_date,
        'consensus_date': consensus_date,
    }
