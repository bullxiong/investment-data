"""
3AI 适配器 — LLM信号提取
调用 stock-blogger-tracker 的 SectorExtractor + 情感引擎
"""
import sys, os, json

_STB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), 'stock-blogger-tracker')
if _STB in sys.path:
    sys.path.remove(_STB)
sys.path.insert(0, _STB)


def extract_signals(articles, llm_config=None):
    """从文章中提取概念/股票/情感信号。

    Args:
        articles: List[Dict] (collect_articles 的输出)
        llm_config: dict, 可选

    Returns:
        List[Dict]: 8 字段 signal 列表
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

        # 暗语解析
        enriched = resolver.enrich_text(art.get('author', ''), content)

        # 概念提取
        result = extractor.extract(enriched)
        sectors = result.get('sectors', [])

        date = (art.get('created_at', '') or '')[:10] or '2026-07-28'
        author_id = art.get('author_id', '')

        for sector in sectors:
            concept = sector.get('name', '')
            if not concept:
                continue
            if sector.get('context_quality', '') == 'passive':
                continue

            sentiment_result = manager.get_or_compute(
                author_id, concept, date, [content[:800]]
            )
            sentiment = sentiment_result.get('sentiment', 'neutral')

            direction_map = {
                'bullish': 'long',
                'bearish': 'short',
                'neutral': 'watch',
                'none': 'watch',
            }
            direction = direction_map.get(sentiment, 'watch')

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
    wl_path = os.path.join(_STB, 'data', 'concept_whitelist_v2.json')
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
