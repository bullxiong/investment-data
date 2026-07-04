"""
雪球博主文章采集 — 适配层
调用: XueqiuCrawler.fetch_via_api() + fetch_all_posts()
输出: 标准化 List[Dict]
"""
import sys
import os
import json

PROJECT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT not in sys.path:
    sys.path.insert(0, PROJECT)


def _load_xq_token():
    """从 data/xueqiu_cookies.json 读取 xq_a_token"""
    cookies_path = os.path.join(PROJECT, 'data', 'xueqiu_cookies.json')
    if os.path.exists(cookies_path):
        try:
            with open(cookies_path, 'r', encoding='utf-8') as f:
                cookies = json.load(f)
            return cookies.get('xq_a_token', '')
        except (json.JSONDecodeError, IOError):
            pass
    return ''


def collect_xueqiu(config):
    """
    config: {"uids": ["7251377368", "1034624503"], "xq_token": "..."}
    xq_token 可选，不传则自动从 data/xueqiu_cookies.json 读取
    """
    from src.crawlers.xueqiu.crawler import XueqiuCrawler

    uids = config.get('uids', ['7251377368', '1034624503'])
    xq_token = config.get('xq_token', '') or _load_xq_token()

    articles = []
    for uid in uids:
        crawler = XueqiuCrawler(uid=uid, xq_token=xq_token)
        try:
            posts = crawler.fetch_via_api(page=1)  # 每天1页
        except Exception:
            posts = crawler.fetch_all_posts(max_pages=1)

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
