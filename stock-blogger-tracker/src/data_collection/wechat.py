"""
微信公众号/知乎/ZSXQ文章采集 — 3AI协作适配层
调用: ZhiHuCrawler + ZsxqScanner
"""
import sys, os, json
PROJECT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT)


def collect_wechat(config=None):
    """微信当前为转发即处理模式，不做自动采集"""
    return []


def collect_zhihu(config=None):
    """采集知乎博主回答"""
    if config is None:
        config = {}
    from src.crawlers.zhihu.crawler import ZhiHuCrawler
    slugs = config.get('zhihu_slugs', ['xiao-peng-61-47'])
    articles = []
    for slug in slugs:
        crawler = ZhiHuCrawler(url_slug=slug)
        posts = crawler.fetch_all(full_content=True, limit=20)
        for p in posts:
            articles.append({
                'source': 'zhihu',
                'url': p.get('url', ''),
                'title': p.get('title', '') or '',
                'author': p.get('author', ''),
                'author_id': str(p.get('user_id', '')),
                'content': p.get('content', ''),
                'created_at': p.get('created_at', ''),
                'article_id': p.get('post_id', ''),
            })
    return articles


def collect_zsxq(config=None):
    """采集知识星球帖子"""
    if config is None:
        config = {}
    from src.zsxq.zsxq_scanner import ZsxqScanner
    group_ids = config.get('zsxq_groups', [
        '48888522142558', '51115848448254', '28888221524121'
    ])
    articles = []
    for gid in group_ids:
        scanner = ZsxqScanner(group_id=gid)
        star_only = (gid == '28888221524121')
        posts = scanner.scan_recent(days=1)
        for p in posts:
            articles.append({
                'source': 'zsxq',
                'url': p.get('url', ''),
                'title': p.get('title', '') or '',
                'author': p.get('author', ''),
                'author_id': str(p.get('topic_id', '')),
                'content': p.get('content', ''),
                'created_at': p.get('created_at', '') or p.get('create_time', ''),
                'article_id': p.get('topic_id', ''),
            })
    return articles


def collect_fuli(config=None):
    """采集复利笔记小程序持仓数据"""
    from src.crawlers.fuli_crawler import FuliCrawler
    from datetime import datetime, timezone, timedelta

    TZ = timezone(timedelta(hours=8))
    date_str = config.get('hold_date', datetime.now(TZ).strftime('%Y-%m-%d')) if config else datetime.now(TZ).strftime('%Y-%m-%d')

    try:
        crawler = FuliCrawler()
        holdings = crawler.fetch_holdings(date_str)
    except Exception as e:
        print(f"  [fuli] Fiddler/API error: {e}")
        return []

    if not holdings:
        return []

    articles = []
    for h in holdings:
        stock_name = h.get('stockName', '')
        hold_price = h.get('holdPrice', '')
        stock_users = h.get('stockUser', 0)
        top5 = h.get('topFivePlayer', [])
        top5_names = ', '.join([p.get('gameName','') + '(' + p.get('holdPrice','') + ')' for p in top5[:3]])

        content = f"复利笔记持仓: {stock_name} 总持仓{hold_price}, {stock_users}人持有。TOP3: {top5_names}"

        articles.append({
            'source': 'fuli',
            'url': f'https://www.fuyinkeji.top/hold/selectHoldHoldStocks?holdDate={date_str}',
            'title': f'复利笔记 {date_str} 持仓: {stock_name}',
            'author': '复利笔记(游戏平台)',
            'author_id': 'fuli-platform',
            'content': content,
            'created_at': date_str + 'T20:00:00+08:00',
            'article_id': f'fuli_{date_str}_{stock_name}',
        })

    return articles
