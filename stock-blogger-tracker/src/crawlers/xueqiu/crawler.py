# -*- coding: utf-8 -*-
"""
Xueqiu crawler — fetches blogger posts via open_link and/or direct API.

Two fetch modes:
1. open_link: Bypasses WAF, 21 posts per page (1 page only for SPA)
2. API direct: Uses md5__1038 WAF token for structured JSON (20 posts/page, paginated)
"""

import json
import os
import sys
import time
import requests
from datetime import datetime, timezone, timedelta
from typing import Optional

# Add project root to path
_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from src.utils.open_link import fetch_page
from src.crawlers.xueqiu.parser import parse_openlink_text, parse_api_response

TZ = timezone(timedelta(hours=8))


class XueqiuCrawler:
    """
    Crawler for Xueqiu blogger posts.

    Usage:
        crawler = XueqiuCrawler(uid="7251377368")
        posts = crawler.fetch_posts()          # auto-select best method
        posts = crawler.fetch_via_api(page=1)   # direct API with WAF token
        posts = crawler.fetch_via_openlink()    # open_link bypass
        crawler.save_posts(posts)
    """

    API_BASE = "https://xueqiu.com/v4/statuses/user_timeline.json"

    def __init__(self, uid: str, data_dir: Optional[str] = None,
                 rate_limit: float = 3.0, xq_token: Optional[str] = None):
        """
        Initialize the crawler.

        Args:
            uid: Xueqiu user ID
            data_dir: Base data directory (default: project_root/data)
            rate_limit: Seconds between requests
            xq_token: xq_a_token cookie value for direct API access
        """
        self.uid = str(uid)
        self.rate_limit = rate_limit
        self.xq_token = xq_token

        if data_dir is None:
            data_dir = os.path.join(_project_root, 'data')
        self.data_dir = data_dir

        # Post storage path: data/posts/{uid}/
        self.post_dir = os.path.join(self.data_dir, 'posts', self.uid)
        os.makedirs(self.post_dir, exist_ok=True)

        # Portfolio storage path
        self.portfolio_dir = os.path.join(self.data_dir, 'portfolios', self.uid)
        os.makedirs(self.portfolio_dir, exist_ok=True)

        self._last_request_time = 0.0
        self._md5_token = None
        self._http_session = None

    def _rate_limit_wait(self):
        """Wait to respect rate limiting between requests."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self.rate_limit:
            time.sleep(self.rate_limit - elapsed)
        self._last_request_time = time.time()

    def _build_page_url(self, page: int = 1) -> str:
        """
        Build the URL for a given page.

        Xueqiu profile page URLs:
        - Main: https://xueqiu.com/u/{uid}
        - Timeline: https://xueqiu.com/u/{uid}?page={page}

        The open_link API renders the page, so we use the user-friendly URL
        and rely on scrolling/query params.
        """
        if page <= 1:
            return f"https://xueqiu.com/u/{self.uid}"
        return f"https://xueqiu.com/u/{self.uid}?page={page}"

    def _get_http_session(self) -> requests.Session:
        """Get or create a requests session with proper cookies."""
        if self._http_session is None:
            self._http_session = requests.Session()
            self._http_session.headers.update({
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            })
            if self.xq_token:
                self._http_session.cookies.set('xq_a_token', self.xq_token, domain='.xueqiu.com')
        return self._http_session

    def _get_md5_token(self) -> Optional[str]:
        """Try to get WAF token from browser session or cache."""
        if self._md5_token:
            return self._md5_token
        try:
            from src.utils.md5_token import get_md5_token
            self._md5_token = get_md5_token()
            return self._md5_token
        except ImportError:
            return None

    def fetch_via_api(self, page: int = 1) -> list[dict]:
        """
        Fetch posts via direct API call (requires WAF md5 token).

        Args:
            page: Page number (1-based)

        Returns:
            List of parsed post dicts in unified format
        """
        md5 = self._get_md5_token()
        url = f"{self.API_BASE}?user_id={self.uid}&page={page}"
        if md5:
            url += f"&md5__1038={md5}"

        print(f"[Crawler/API] Fetching page {page}")
        self._rate_limit_wait()

        session = self._get_http_session()
        try:
            r = session.get(url, timeout=30)
            r.raise_for_status()
        except Exception as e:
            print(f"[Crawler/API] Error page {page}: {e}")
            return []

        try:
            data = r.json()
        except Exception:
            print(f"[Crawler/API] Page {page}: WAF or non-JSON response")
            return []

        posts = parse_api_response(data)
        print(f"[Crawler/API] Page {page}: {len(posts)} posts")
        return posts

    def fetch_via_openlink(self, page: int = 1) -> list[dict]:
        """
        Fetch posts via open_link API (bypasses WAF).

        Args:
            page: Page number (1-based, but SPA only returns first page)

        Returns:
            List of parsed post dicts in unified format
        """
        url = self._build_page_url(page)
        print(f"[Crawler/OL] Fetching page {page}")

        self._rate_limit_wait()

        try:
            text = fetch_page(url)
        except Exception as e:
            print(f"[Crawler/OL] Error page {page}: {e}")
            return []

        if not text:
            print(f"[Crawler/OL] Empty response for page {page}")
            return []

        posts = parse_openlink_text(text)
        print(f"[Crawler/OL] Page {page}: {len(posts)} posts")
        return posts

    def fetch_posts(self, page: int = 1) -> list[dict]:
        """
        Fetch one page of posts. Tries direct API first, falls back to open_link.

        Args:
            page: Page number (1-based)

        Returns:
            List of parsed post dicts
        """
        # Try API first if we have auth
        if self.xq_token:
            posts = self.fetch_via_api(page)
            if posts:
                return posts
            print("[Crawler] API failed, falling back to open_link...")

        return self.fetch_via_openlink(page)

    def fetch_all_posts(self, max_pages: int = 5) -> list[dict]:
        """
        Fetch multiple pages of posts.

        Args:
            max_pages: Maximum number of pages to fetch

        Returns:
            All posts sorted by created_at descending (newest first)
        """
        all_posts = []
        seen_titles = set()

        for page in range(1, max_pages + 1):
            posts = self.fetch_posts(page)

            if not posts:
                # Empty page means no more content
                print(f"[Crawler] No more posts at page {page}, stopping.")
                break

            # Deduplicate by post_id (API) or title+content hash (open_link)
            new_posts = 0
            for post in posts:
                pid = post.get('post_id', 0)
                if pid > 0:
                    if pid not in seen_titles:
                        seen_titles.add(pid)
                        all_posts.append(post)
                        new_posts += 1
                else:
                    key = (post.get('title', ''), post.get('content', '')[:100])
                    if key not in seen_titles:
                        seen_titles.add(key)
                        all_posts.append(post)
                        new_posts += 1

            print(f"[Crawler] Page {page}: {new_posts} new posts (deduped)")

            if new_posts == 0:
                # No new content, likely hit the end
                print(f"[Crawler] No new posts at page {page}, stopping.")
                break

        # Sort by created_at descending
        all_posts.sort(key=lambda p: p.get('created_at', ''), reverse=True)
        return all_posts

    def fetch_portfolio(self) -> dict:
        """
        Fetch portfolio / holdings data.

        Note: This is a placeholder. Portfolio data requires structured parsing
        of the portfolio page or API access. To be implemented when the parsing
        logic is defined.

        Returns:
            Portfolio dict with holdings
        """
        url = f"https://xueqiu.com/p/{self.uid}"
        print(f"[Crawler] Fetching portfolio: {url}")
        self._rate_limit_wait()

        try:
            text = fetch_page(url)
        except Exception as e:
            print(f"[Crawler] Error fetching portfolio: {e}")
            return {'error': str(e), 'holdings': []}

        # Placeholder: portfolio parsing not yet implemented
        return {
            'uid': self.uid,
            'fetched_at': datetime.now(TZ).strftime('%Y-%m-%dT%H:%M:%S+08:00'),
            'raw_text_length': len(text) if text else 0,
            'holdings': [],
            'note': 'Portfolio parsing not yet implemented. Raw text available.'
        }

    def save_posts(self, posts: list[dict], date_str: Optional[str] = None):
        """
        Save posts to JSON file.

        Args:
            posts: List of post dicts
            date_str: Date string for filename (default: today)
        """
        if date_str is None:
            date_str = datetime.now(TZ).strftime('%Y-%m-%d')

        filepath = os.path.join(self.post_dir, f'{date_str}.json')

        # Load existing data if file exists (merge)
        existing = []
        if os.path.exists(filepath):
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    existing = json.load(f)
            except (json.JSONDecodeError, IOError):
                pass

        # Merge: overwrite by post_id (API posts) or title+created_at (open_link posts)
        merged = {}
        for post in existing:
            pid = post.get('post_id', 0)
            if pid > 0:
                key = ('pid', pid)
            else:
                key = ('tt', post.get('title', ''), post.get('created_at', ''))
            merged[key] = post
        for post in posts:
            pid = post.get('post_id', 0)
            if pid > 0:
                key = ('pid', pid)
            else:
                key = ('tt', post.get('title', ''), post.get('created_at', ''))
            merged[key] = post

        merged_list = sorted(merged.values(),
                             key=lambda p: p.get('created_at', ''),
                             reverse=True)

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(merged_list, f, ensure_ascii=False, indent=2)

        print(f"[Crawler] Saved {len(merged_list)} posts to {filepath}")
        return filepath

    def load_posts(self, date_str: str) -> list[dict]:
        """
        Load posts from a saved JSON file.

        Args:
            date_str: Date string (YYYY-MM-DD)

        Returns:
            List of post dicts
        """
        filepath = os.path.join(self.post_dir, f'{date_str}.json')
        if not os.path.exists(filepath):
            return []

        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)


# Convenience function
def crawl_xueqiu_user(uid: str, max_pages: int = 3, rate_limit: float = 3.0,
                       xq_token: Optional[str] = None) -> list[dict]:
    """
    Convenience function: crawl and save posts for a user.

    Args:
        uid: Xueqiu user ID
        max_pages: Maximum pages to crawl
        rate_limit: Seconds between requests
        xq_token: xq_a_token for direct API access

    Returns:
        All fetched posts
    """
    crawler = XueqiuCrawler(uid=uid, rate_limit=rate_limit, xq_token=xq_token)
    posts = crawler.fetch_all_posts(max_pages=max_pages)
    if posts:
        crawler.save_posts(posts)
    return posts
