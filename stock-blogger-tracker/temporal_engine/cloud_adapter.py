"""
CloudAdapter v2 — 使用 sw2_industry_map.json 做 股票→概念 映射
替代 v1 的手写 STOCK_SECTOR_MAP
"""
import json, os
from collections import defaultdict


# 缓存
_sw2_map = None
_concept_aliases = {
    # 博主系统概念 ↔ 申万二级行业 别名对齐
    '半导体': '半导体',
    '证券': '证券',
    '化学制药': '化学制药',
    '通信设备': '通信设备',
    '锂电池': '电池',
    '光通信': '通信设备',
    '消费电子': '消费电子',
    '有色金属': '工业金属',  # 合并到 工业金属
    'PCB': '元件',          # PCB 属于 元件
    '电子元件': '元件',
    '新能源': '电力设备',
}


def _load_sw2():
    """延迟加载 sw2_industry_map.json"""
    global _sw2_map
    if _sw2_map is not None:
        return _sw2_map

    paths = [
        os.path.join(os.path.expanduser('~'), '.openclaw-autoclaw', 'workspace',
                     '.openclaw-attachments', '20260706-154949-f2776e60-85b-sw2_industry_map.json'),
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     '..', 'data', 'stock_db', 'sw2_industry_map.json'),
    ]
    for p in paths:
        if os.path.exists(p):
            with open(p, 'r', encoding='utf-8') as f:
                _sw2_map = json.load(f)
            print('[sw2] Loaded %d stocks from %s' % (len(_sw2_map), os.path.basename(p)))
            return _sw2_map

    print('[sw2] WARNING: sw2_industry_map.json not found')
    _sw2_map = {}
    return _sw2_map


def stock_to_concept(stock_name):
    """通过 sw2_industry_map 把股票名映射到申万二级行业"""
    sw2 = _load_sw2()
    entry = sw2.get(stock_name)
    if entry:
        sw2_name = entry.get('sw2_name', '')
        # 通过别名对齐
        for alias, mapped in _concept_aliases.items():
            if alias == sw2_name or alias in sw2_name:
                return mapped
        return sw2_name
    return None


def load_cloud_signals(json_path=None):
    """
    v2: 使用 sw2_industry_map.json 做映射。
    
    Returns:
        dict: {concept: {has_signal, total_stocks, representative, stocks, ...}}
    """
    if json_path is None:
        json_path = os.path.join(
            os.path.expanduser('~'), '.openclaw-autoclaw', 'workspace',
            '.openclaw-attachments',
            '20260706-151902-7e750af2-5c9-cleaned_system_view_v4.json'
        )

    if not os.path.exists(json_path):
        print('[cloud v2] File not found: %s' % json_path)
        return {}

    with open(json_path, 'r', encoding='utf-8') as f:
        raw = json.load(f)

    concepts = defaultdict(list)
    unmapped = []
    used_fallback = 0

    for entry in raw:
        name = entry.get('name', '')
        sw2_concept = stock_to_concept(name)

        if sw2_concept is None:
            unmapped.append((name, entry.get('code', '')))
            continue

        concepts[sw2_concept].append({
            'name': name,
            'code': entry.get('code', ''),
            'type': entry.get('type', ''),
            'key_price': entry.get('key_price'),
            'date': entry.get('date', ''),
            'status': entry.get('status', ''),
        })

    cloud = {}
    for sector, stocks in concepts.items():
        b_stocks = [s for s in stocks if s['type'] in ('B', 'C')]
        a_stocks = [s for s in stocks if s['type'] == 'A']
        d_stocks = [s for s in stocks if s['type'] == 'D']

        representative = None
        if b_stocks:
            representative = sorted(b_stocks, key=lambda x: x['date'], reverse=True)[0]
        elif d_stocks:
            representative = sorted(d_stocks, key=lambda x: x['date'], reverse=True)[0]
        elif a_stocks:
            representative = sorted(a_stocks, key=lambda x: x['date'], reverse=True)[0]

        cloud[sector] = {
            'has_signal': True,
            'total_stocks': len(stocks),
            'b_stocks': len(b_stocks),
            'a_stocks': len(a_stocks),
            'd_stocks': len(d_stocks),
            'representative': representative,
            'stage': representative['type'] if representative else '',
            'entry_price': representative['key_price'] if representative else None,
            'stocks': stocks,
        }

    pct = 100 * (1 - len(unmapped) / max(len(raw), 1))
    print('[cloud v2] Loaded %d concepts from %d records (%.0f%% mapped, %d unmapped)' %
          (len(cloud), len(raw), pct, len(unmapped)))
    if unmapped:
        first10 = [u[0] for u in unmapped[:10]]
        print('[cloud v2] Unmapped: %s' % first10)

    return cloud


load = load_cloud_signals
