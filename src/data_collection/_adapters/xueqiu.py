"""
3AI 适配器 — 雪球博主文章采集
调用 stock-blogger-tracker 的 XueqiuCrawler
"""
import sys, os, json

_STB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), 'stock-blogger-tracker')
# 确保 stock-blogger-tracker/src/ 优先于 workspace/src/
if _STB in sys.path:
    sys.path.remove(_STB)
sys.path.insert(0, _STB)


def _load_xq_token():
    cookies_path = os.path.join(_STB, 'data', 'xueqiu_cookies.json')
    if os.path.exists(cookies_path):
        try:
            with open(cookies_path, 'r', encoding='utf-8') as f:
                cookies = json.load(f)
            return cookies.get('xq_a_token', '')
        except (json.JSONDecodeError, IOError):
            pass
    return ''


def collect_xueqiu(config):
    from src.crawlers.xueqiu.crawler import XueqiuCrawler

    uids = config.get('uids', ['7251377368', '1034624503'])
    xq_token = config.get('xq_token', '') or _load_xq_token()

    articles = []
    for uid in uids:
        crawler = XueqiuCrawler(uid=uid, xq_token=xq_token)
        try:
            posts = crawler.fetch_via_api(page=1)
        except Exception:
            try:
                posts = crawler.fetch_all_posts(max_pages=1)
            except Exception:
                posts = []

        for p in posts:
            articles.append({
                'source': 'xueqiu',
                'url': f'https://xueqiu.com/{p.get("user_id")}/{p.get("post_id")}',
                'title': p.get('title', '') or '',
                'author': p.get('author', ''),
                'author_id': str(p.get('user_id', '')),
                'content': p.get('content', '') or p.get('text', ''),
                'created_at': p.get('created_at', ''),
                'article_id': str(p.get('post_id', '')),
            })
    return articles
