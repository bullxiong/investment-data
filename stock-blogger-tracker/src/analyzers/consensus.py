# -*- coding: utf-8 -*-
"""
共识引擎 — 跨博主共振检测 + 概念股池 + 观点时间线
输入: 所有博主的 posts 和 views 数据
输出: data/cross_blogger/{resonance,concept_stocks,view_timeline}.json
"""

import json, os, sys, io, re
from datetime import datetime, timezone, timedelta
from collections import defaultdict

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.analyzers.concept_sentiment import get_sentiment_manager, extract_concept_context
TZ = timezone(timedelta(hours=8))

# ---- 行业-概念映射表 ----
CONCEPT_INDUSTRY_MAP = {
    "PCB产业链": ["PCB", "印制电路板", "电子元器件", "元器件"],
    "光通信": ["半导体", "光电子", "通信设备", "电子元器件", "元器件"],
    "半导体制造": ["半导体", "集成电路", "封测", "半导体设备", "元器件"],
    "AI基础设施": ["电源设备", "制冷设备", "服务器", "通信设备", "电气设备", "通用设备"],
    "AI算力": ["半导体", "计算机", "软件", "IT设备"],
    "被动元件": ["电子元器件", "元器件", "被动元件"],
    "存储": ["半导体", "存储芯片", "元器件"],
    "能源": ["电池", "光伏", "风电", "电力", "新能源"],
    "新质生产力": ["航天", "军工", "航空"],
    "机器人": ["机器人", "自动化", "机械设备"],
}


def proximity_score(concept_keyword, stock_name, post_text):
    """概念关键词与股票名在帖子中的邻近得分。

    不做硬过滤，只降置信度：
    - 1.0: 概念和股票在同一句话（50字内）
    - 0.5: 在同一段落（200字内）
    - 0.2: 距离 >200 字
    - 0.5: 找不到位置（中性）
    """
    cpos = post_text.find(concept_keyword)
    spos = post_text.find(stock_name)
    if cpos < 0 or spos < 0:
        return 0.5  # 找不到位置，中性
    dist = abs(cpos - spos)
    if dist <= 50:
        return 1.0
    elif dist <= 200:
        return 0.5
    else:
        return 0.2


def extract_date_from_title(title):
    """从知乎回答标题中提取日期。"""
    if not title:
        return None
    # 匹配 "2026年6月3日" / "2026 年 6 月 18 日"
    m = re.search(r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日', title)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    # 匹配 "6月3日"（无年份，默认2026）
    m = re.search(r'(\d{1,2})\s*月\s*(\d{1,2})\s*日', title)
    if m:
        return f"2026-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    return None
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'data')
OUT_DIR = os.path.join(DATA_DIR, 'cross_blogger')
os.makedirs(OUT_DIR, exist_ok=True)


def load_all_posts():
    """加载所有博主的帖子数据。"""
    posts_dir = os.path.join(DATA_DIR, 'posts')
    all_data = {}
    if not os.path.isdir(posts_dir):
        return all_data
    for uid in os.listdir(posts_dir):
        uid_dir = os.path.join(posts_dir, uid)
        if not os.path.isdir(uid_dir):
            continue
        uid_posts = []
        for fname in sorted(os.listdir(uid_dir)):
            if not fname.endswith('.json'):
                continue
            with open(os.path.join(uid_dir, fname), encoding='utf-8') as f:
                uid_posts.extend(json.load(f))
        if uid_posts:
            all_data[uid] = uid_posts
    return all_data


def get_blogger_name(uid):
    """从 bloggers.json 获取博主名称。"""
    config_file = os.path.join(os.path.dirname(DATA_DIR), 'bloggers.json')
    if os.path.exists(config_file):
        with open(config_file, encoding='utf-8') as f:
            config = json.load(f)
        if uid in config:
            return config[uid].get('name', uid)
    return uid


def build_timeline(all_posts):
    """构建观点变迁时间线（甘特图数据）。"""
    from src.analyzers.sector_extractor import SectorExtractor
    from src.preprocess.text_cleaner import TextCleaner
    extractor = SectorExtractor(data_dir=os.path.join(DATA_DIR, 'stock_db'))

    blog_timelines = {}
    for uid, posts in all_posts.items():
        name = get_blogger_name(uid)
        concept_periods = defaultdict(lambda: {'start': None, 'end': None, 'sentiments': []})

        last_valid_date = None  # 相邻对齐：记住最近一个有日期的帖子
        for p in posts:
            if not isinstance(p, dict):
                continue
            content = TextCleaner.clean(p.get('content', ''))
            if not content:
                continue
            date = (p.get('created_at', '') or '')[:10]
            if not date or date == '2026-06-24':
                # 占位日期，尝试从标题提取
                title = p.get('title', '')
                extracted = extract_date_from_title(title)
                if extracted:
                    date = extracted
                    last_valid_date = extracted
                elif last_valid_date:
                    # 相邻对齐：用前一个有日期的帖子的日期
                    date = last_valid_date
            if not date:
                continue

            result = extractor.extract(content)
            for s in result.get('sectors', []):
                sname = s['name']
                # 🆕 从 DeepSeek 缓存获取情感（替代 sector_extractor 的 neutral）
                manager = get_sentiment_manager()
                ctx = extract_concept_context(content, sname)
                result = manager.get_or_compute(uid, sname, date, [ctx])
                sent = result["sentiment"]
                # conviction/time_horizon/risk_acknowledged 暂存
                cp = concept_periods[sname]
                if cp['start'] is None or date < cp['start']:
                    cp['start'] = date
                if cp['end'] is None or date > cp['end']:
                    cp['end'] = date
                cp['sentiments'].append(sent)

        items = []
        for cname, cp in concept_periods.items():
            # 多数情感决定方向
            bullish = cp['sentiments'].count('bullish')
            bearish = cp['sentiments'].count('bearish')
            if bullish > bearish:
                sentiment = 'bullish'
            elif bearish > bullish:
                sentiment = 'bearish'
            else:
                sentiment = 'neutral'
            items.append({
                'concept': cname,
                'start': cp['start'],
                'end': cp['end'],
                'sentiment': sentiment,
                'mentions': len(cp['sentiments']),
            })

        blog_timelines[name] = items

    timeline = {
        'bloggers': list(blog_timelines.keys()),
        'timeline': [{'blogger': k, 'items': sorted(v, key=lambda x: x['start'])} for k, v in blog_timelines.items()],
    }

    with open(os.path.join(OUT_DIR, 'view_timeline.json'), 'w', encoding='utf-8') as f:
        json.dump(timeline, f, ensure_ascii=False, indent=2)
    print(f'view_timeline.json: {sum(len(v) for v in blog_timelines.values())} concepts across {len(blog_timelines)} bloggers')
    return timeline


def build_concept_stocks(all_posts):
    """构建概念股池：每个概念下各博主推荐的股票。"""
    from src.analyzers.sector_extractor import SectorExtractor
    from src.analyzers.stock_decoder import StockDecoder
    from src.preprocess.text_cleaner import TextCleaner
    db_dir = os.path.join(DATA_DIR, 'stock_db')
    extractor = SectorExtractor(data_dir=db_dir)
    decoder = StockDecoder(data_dir=db_dir)

    # 加载 stocks.json 建立 code→industry 映射
    stock_db_industries = {}
    stocks_path = os.path.join(db_dir, 'stocks.json')
    if os.path.exists(stocks_path):
        with open(stocks_path, encoding='utf-8') as f:
            for s in json.load(f):
                stock_db_industries[s['code']] = s.get('industry', '')

    # 加载概念→parent 映射
    concept_parent = extractor.community_parents

    # 🆕 加载白名单用于股票验证
    try:
        whitelist_path = os.path.join(DATA_DIR, 'concept_whitelist_v2.json')
        with open(whitelist_path, encoding='utf-8') as f:
            whitelist_data = json.load(f)
        wl_stocks = {}
        for cname_wl, info in whitelist_data['whitelist'].items():
            stocks_wl = info.get('stocks', {})
            wl_stocks[cname_wl] = set(stocks_wl.keys())
    except:
        wl_stocks = {}

    concept_pool = {}

    for uid, posts in all_posts.items():
        name = get_blogger_name(uid)
        last_valid_date = None
        for p in posts:
            if not isinstance(p, dict):
                continue
            content = TextCleaner.clean(p.get('content', ''))
            if not content:
                continue
            date = (p.get('created_at', '') or '')[:10]
            if not date or date == '2026-06-24':
                title = p.get('title', '')
                extracted = extract_date_from_title(title)
                if extracted:
                    date = extracted
                    last_valid_date = extracted
                elif last_valid_date:
                    date = last_valid_date
            if not date:
                continue

            # Extract sectors + stocks
            sr = extractor.extract(content)
            sectors = sr.get('sectors', [])
            dr = decoder.decode(content, sectors=[s['name'] for s in sectors])
            stocks = dr.get('matched', [])

            if not sectors or not stocks:
                continue

            for s in sectors:
                cname = s['name']
                if cname not in concept_pool:
                    concept_pool[cname] = {'bloggers': {}}
                if name not in concept_pool[cname]['bloggers']:
                    concept_pool[cname]['bloggers'][name] = {'stocks': {}, 'posts': []}

                blogger_data = concept_pool[cname]['bloggers'][name]
                for stock in stocks:
                    code = stock['code']
                    sname_stock = stock['name']

                    # 邻近得分：概念关键词与股票名的距离
                    cname_keyword = s.get('matched_text', cname)
                    prox = proximity_score(cname_keyword, sname_stock, content)

                    # 行业过滤：只有股票行业与概念parent的映射匹配才关联
                    stock_industry = stock_db_industries.get(code, '')
                    parent = concept_parent.get(cname, '')
                    valid_inds = CONCEPT_INDUSTRY_MAP.get(parent, [])
                    valid = False
                    if not valid_inds:
                        # 🆕 没有行业映射时，只关联白名单里有的股票
                        wl_codes = wl_stocks.get(cname, set())
                        valid = code in wl_codes
                    elif any(vi in stock_industry for vi in valid_inds):
                        valid = True
                    elif not stock_industry:
                        # 🆕 股票无行业信息时，也只关联白名单里的
                        wl_codes = wl_stocks.get(cname, set())
                        valid = code in wl_codes
                    if not valid:
                        continue

                    if code not in blogger_data['stocks']:
                        blogger_data['stocks'][code] = {
                            'name': sname_stock, 'first': date, 'last': date,
                            'proximity_scores': [prox],
                        }
                    else:
                        if date < blogger_data['stocks'][code]['first']:
                            blogger_data['stocks'][code]['first'] = date
                        if date > blogger_data['stocks'][code]['last']:
                            blogger_data['stocks'][code]['last'] = date
                        blogger_data['stocks'][code].setdefault('proximity_scores', []).append(prox)

    # Compute overlaps and summary
    for cname, cdata in concept_pool.items():
        all_codes = set()
        overlap_codes = None
        for bname, bdata in cdata['bloggers'].items():
            codes = set(bdata['stocks'].keys())
            all_codes.update(codes)
            # 🆕 只保留白名单确认的股票：博主提及 ∩ 白名单
            verified = codes & wl_stocks.get(cname, set())
            if overlap_codes is None:
                overlap_codes = verified
            else:
                overlap_codes &= verified

            # 计算每只股票的平均邻近得分
            for code, sdata in bdata['stocks'].items():
                scores = sdata.pop('proximity_scores', [0.5])
                sdata['proximity'] = round(sum(scores) / len(scores), 2) if scores else 0.5

        cdata['all_stocks'] = sorted(all_codes)
        cdata['overlap'] = sorted(overlap_codes) if overlap_codes else []
        cdata['blogger_count'] = len(cdata['bloggers'])

    # Filter: only keep concepts mentioned by ≥1 blogger
    concept_pool = {k: v for k, v in concept_pool.items() if v['blogger_count'] >= 1}

    with open(os.path.join(OUT_DIR, 'concept_stocks.json'), 'w', encoding='utf-8') as f:
        json.dump(concept_pool, f, ensure_ascii=False, indent=2)
    print(f'concept_stocks.json: {len(concept_pool)} concepts')
    return concept_pool


def build_resonance(all_posts, window_days=7):
    """检测跨博主共振：同一主题在窗口期内被≥2位博主提及。"""
    from collections import defaultdict

    # Collect: concept → [(blogger, date, sentiment), ...]
    from src.analyzers.sector_extractor import SectorExtractor
    from src.preprocess.text_cleaner import TextCleaner
    extractor = SectorExtractor(data_dir=os.path.join(DATA_DIR, 'stock_db'))
    concept_mentions = defaultdict(list)

    for uid, posts in all_posts.items():
        name = get_blogger_name(uid)
        last_valid_date = None
        for p in posts:
            if not isinstance(p, dict):
                continue
            content = p.get('content', '')
            content = TextCleaner.clean(content)
            if not content:
                continue
            date = (p.get('created_at', '') or '')[:10]
            if not date or date == '2026-06-24':
                title = p.get('title', '')
                extracted = extract_date_from_title(title)
                if extracted:
                    date = extracted
                    last_valid_date = extracted
                elif last_valid_date:
                    date = last_valid_date
            if not date:
                continue
            title = p.get('title', '') or content[:50]
            sr = extractor.extract(content)
            for s in sr.get('sectors', []):
                # 🆕 修复 D: 跳过 context_quality=passive 的顺带提及
                if s.get('context_quality', 'active') == 'passive':
                    continue
                # 🆕 从 DeepSeek 缓存获取情感
                manager = get_sentiment_manager()
                ctx = extract_concept_context(content, s['name'])
                result = manager.get_or_compute(uid, s['name'], date, [ctx])
                sent = result["sentiment"]
                # conviction/time_horizon/risk_acknowledged 暂存
                concept_mentions[s['name']].append({
                    'blogger': name,
                    'date': date,
                    'sentiment': sent,
                    'text': f"{title}: {content[:100]}"
                })

    resonances = []
    for concept, mentions in concept_mentions.items():
        bloggers = set(m['blogger'] for m in mentions)
        if len(bloggers) < 2:
            continue

        # 🆕 修复 C: 按博主汇总提及次数，至少 2 位博主 mention≥2 次才计入共振
        blogger_mention_counts = {}
        for m in mentions:
            blogger = m['blogger']
            blogger_mention_counts[blogger] = blogger_mention_counts.get(blogger, 0) + 1
        qualifying_bloggers = [b for b, c in blogger_mention_counts.items() if c >= 2]
        if len(qualifying_bloggers) < 2:
            continue
        # Filter mentions to only qualifying bloggers
        mentions = [m for m in mentions if m['blogger'] in qualifying_bloggers]

        # Group by time window
        dates = sorted(set(m['date'] for m in mentions))
        windows = []
        window_start = None
        window_bloggers = set()
        window_evidence = []

        for date in dates:
            if window_start is None:
                window_start = date
                window_bloggers = set()
                window_evidence = []

            # Check if still within window
            try:
                d = datetime.strptime(date, '%Y-%m-%d').replace(tzinfo=TZ)
                ws = datetime.strptime(window_start, '%Y-%m-%d').replace(tzinfo=TZ)
                if (d - ws).days > window_days:
                    # Close current window
                    if len(window_bloggers) >= 2:
                        windows.append({
                            'start': window_start,
                            'end': dates[dates.index(date)-1] if dates.index(date) > 0 else date,
                            'bloggers': sorted(window_bloggers),
                            'evidence': list(window_evidence),
                        })
                    window_start = date
                    window_bloggers = set()
                    window_evidence = []
            except:
                pass

            for m in mentions:
                if m['date'] == date:
                    window_bloggers.add(m['blogger'])
                    window_evidence.append({'blogger': m['blogger'], 'date': date, 'sentiment': m.get('sentiment', 'neutral'), 'text': m['text'][:100]})

        # Close last window
        if len(window_bloggers) >= 2 and window_start:
            windows.append({
                'start': window_start,
                'end': dates[-1],
                'bloggers': sorted(window_bloggers),
                'evidence': list(window_evidence),
            })

        for w in windows:
            # 🆕 修复 B: 方向必须是 ≥70% 的博主共识
            sentiments = [m.get('sentiment', 'neutral') for m in w['evidence']]
            from collections import Counter
            sent_counts = Counter(sentiments)
            total = len(sentiments)
            majority_sent = sent_counts.most_common(1)[0]
            if majority_sent[1] / total >= 0.7:
                direction = majority_sent[0]
            else:
                direction = "mixed"  # 无共识
            resonances.append({
                'concept': concept,
                'bloggers': w['bloggers'],
                'start': w['start'],
                'end': w['end'],
                'direction': direction,
                'evidence_count': len(w['evidence']),
            })

    resonances.sort(key=lambda r: (-len(r['bloggers']), r['start']))

    with open(os.path.join(OUT_DIR, 'resonance.json'), 'w', encoding='utf-8') as f:
        json.dump(resonances, f, ensure_ascii=False, indent=2)
    print(f'resonance.json: {len(resonances)} resonance events')
    return resonances


if __name__ == '__main__':
    print("Loading all posts...")
    all_posts = load_all_posts()
    total = sum(len(v) for v in all_posts.values())
    print(f"Total: {total} posts from {len(all_posts)} bloggers")

    print("\nBuilding view timeline...")
    build_timeline(all_posts)

    print("\nBuilding concept stock pools...")
    build_concept_stocks(all_posts)

    print("\nDetecting resonances...")
    resonances = build_resonance(all_posts)

    if resonances:
        print("\nTop resonances:")
        for r in resonances[:10]:
            print(f"  [{r['start']}~{r['end']}] {r['concept']}: {', '.join(r['bloggers'])} ({r['direction']})")
