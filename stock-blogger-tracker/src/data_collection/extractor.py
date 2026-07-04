"""
LLM信号提取 — 3AI协作适配层
调用: SectorExtractor + ConceptSentiment + AliasResolver
"""
import sys, os, json
PROJECT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT)


def extract_signals(articles, llm_config=None):
    """
    从文章中提取概念/股票/情感信号。
    
    Args:
        articles: List[Dict] (collect_articles的输出)
        llm_config: dict, 可选 {"api_key": "..."}
    
    Returns:
        List[Dict]: [{
            'article_id': str,
            'source': str,
            'concept': str,
            'related_stocks': str (JSON array),
            'sentiment': str ('bullish'|'bearish'|'neutral'),
            'direction': str ('long'|'short'|'watch'),
            'confidence': float,
            'rationale': str (JSON)
        }]
    """
    from src.analyzers.sector_extractor import SectorExtractor
    from src.analyzers.concept_sentiment import get_sentiment_manager
    from src.analyzers.alias_resolver import AliasResolver

    manager = get_sentiment_manager()
    extractor = SectorExtractor()
    resolver = AliasResolver()

    signals = []
    for art in articles:
        content = art.get('content', '')
        if not content or len(content) < 30:
            continue

        # 1. 暗语解析
        enriched = resolver.enrich_text(art.get('author', ''), content)

        # 2. 概念提取
        result = extractor.extract(enriched)
        sectors = result.get('sectors', [])

        # 3. 对每个概念判断情感
        date = (art.get('created_at', '') or '')[:10] or '2026-06-29'
        author_id = art.get('author_id', '')

        for sector in sectors:
            concept = sector.get('name', '')
            if not concept:
                continue

            # Skip passive mentions
            if sector.get('context_quality', '') == 'passive':
                continue

            # 情感判断
            sentiment_result = manager.get_or_compute(
                author_id, concept, date, [content[:800]]
            )
            sentiment = sentiment_result.get('sentiment', 'neutral')

            # 方向映射
            direction_map = {
                'bullish': 'long',
                'bearish': 'short',
                'neutral': 'watch',
                'none': 'watch',
            }
            direction = direction_map.get(sentiment, 'watch')

            # 关联股票
            related_stocks = _get_stocks_for_concept(concept)

            signals.append({
                'article_id': str(art.get('article_id', '')),
                'source': art.get('source', ''),
                'concept': concept,
                'related_stocks': json.dumps(related_stocks, ensure_ascii=False),
                'sentiment': sentiment,
                'direction': direction,
                'confidence': sentiment_result.get('conviction_score', 0.5),
                'rationale': json.dumps({
                    'conviction': sentiment_result.get('conviction', 'medium'),
                    'time_horizon': sentiment_result.get('time_horizon', 'medium'),
                    'risk_acknowledged': sentiment_result.get('risk_acknowledged', False),
                }, ensure_ascii=False),
            })

    return signals


def _get_stocks_for_concept(concept):
    """从白名单获取概念的关联股票代码"""
    wl_path = os.path.join(PROJECT, 'data', 'concept_whitelist_v2.json')
    try:
        with open(wl_path, encoding='utf-8') as f:
            whitelist = json.load(f)
    except FileNotFoundError:
        return []
    
    info = whitelist.get('whitelist', {}).get(concept, {})
    stocks_dict = info.get('stocks', {})
    if isinstance(stocks_dict, dict):
        return list(stocks_dict.keys())[:10]
    return []
