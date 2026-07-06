"""
M2: 板块状态机 — 根据信号时间线自动判定每个板块所处的生命周期阶段
"""
from datetime import date as dt_date, timedelta
from temporal_engine.db import get_conn

# 状态枚举
STAGES = ['休眠', '萌芽', '共识', '确认', '过热', '退潮']

# 状态转换: 按优先级从高到低匹配
STAGE_RULES = [
    # (阶段名, lambda fields -> bool, 操作建议)
    # 注意: 按优先级排序, 确认 > 过热 > 共识 > 萌芽
    ('退潮', lambda f: f['bearish_dominant'] and f['total_mentioners'] >= 1,
     '清仓/不做'),
    ('确认', lambda f: f['total_mentioners'] >= 3 and f['bullish_pct'] >= 0.5 and f.get('cloud_confirm', False),
     '正常仓位'),
    ('过热', lambda f: f['total_mentioners'] >= 4 and f['bullish_pct'] >= 0.8 and not f.get('cloud_confirm', False),
     '轻仓/设止盈'),
    ('共识', lambda f: f['total_mentioners'] >= 3 and f['bullish_pct'] >= 0.5,
     '可轻仓试探'),
    ('萌芽', lambda f: 1 <= f['total_mentioners'] <= 2,
     '加入观察, 不进场'),
    ('休眠', lambda f: f['total_mentioners'] == 0,
     '不关注'),
]

# 概念 → 申万二级行业映射 (用于跨源对齐)
CONCEPT_SECTOR_MAP = {
    '证券': '证券',
    '半导体': '半导体',
    '化学制药': '化学制药',
    '通信设备': '通信设备',
    'PCB': '元件',
    '锂电池': '电池',
    '光通信': '通信设备',
    'AI算力': 'IT服务',
    '新能源': '电力设备',
    '消费电子': '电子',
}


def analyze_all(reference_date=None, cloud_signals=None):
    """
    对 signal_timeline 中所有概念做状态判定。
    
    Args:
        reference_date: 参考日期 'YYYY-MM-DD', 默认今天
        cloud_signals: dict, 云起确认的概念列表 {'证券': True, '半导体': False}
    
    Returns:
        List[Dict]: 每个概念的状态卡片
    """
    if reference_date is None:
        reference_date = dt_date.today().isoformat()
    if cloud_signals is None:
        cloud_signals = {}

    # 查询近7天数据
    past = (dt_date.fromisoformat(reference_date) - timedelta(days=6)).isoformat()

    conn = get_conn()
    rows = conn.execute(
        "SELECT concept, author_id, sentiment, date "
        "FROM signal_timeline "
        "WHERE date >= ? AND date <= ? "
        "ORDER BY concept, date",
        (past, reference_date)
    ).fetchall()

    # 按概念分组
    from collections import defaultdict
    concept_data = defaultdict(list)
    for r in rows:
        concept_data[r['concept']].append(dict(r))

    results = []
    # 确保至少返回已知概念
    all_concepts = set(concept_data.keys())

    for concept in sorted(all_concepts):
        signals = concept_data[concept]
        # 计算指标
        authors = set()
        sentiment_counts = {'bullish': 0, 'bearish': 0, 'neutral': 0}
        dates_seen = set()

        for s in signals:
            authors.add(s['author_id'])
            sentiment_counts[s['sentiment']] = sentiment_counts.get(s['sentiment'], 0) + 1
            dates_seen.add(s['date'])

        total = len(authors)
        bullish_pct = sentiment_counts['bullish'] / max(len(signals), 1)
        bearish_dominant = sentiment_counts['bearish'] > sentiment_counts['bullish']

        fields = {
            'concept': concept,
            'total_mentioners': total,
            'bullish_pct': round(bullish_pct, 2),
            'bearish_dominant': bearish_dominant,
            'signals_count': len(signals),
            'dates_active': len(dates_seen),
            'cloud_confirm': cloud_signals.get(concept, False),
        }

        # 匹配阶段
        stage = '休眠'
        advice = '不关注'
        for stage_name, rule, adv in STAGE_RULES:
            if rule(fields):
                stage = stage_name
                advice = adv
                break

        fields['stage'] = stage
        fields['advice'] = advice
        results.append(fields)

    conn.close()
    return results


def get_concept_stage(concept, reference_date=None, cloud_signals=None):
    """获取单个概念的当前阶段"""
    all_states = analyze_all(reference_date, cloud_signals)
    for s in all_states:
        if s['concept'] == concept:
            return s
    return {
        'concept': concept,
        'stage': '休眠',
        'total_mentioners': 0,
        'advice': '不关注',
    }
