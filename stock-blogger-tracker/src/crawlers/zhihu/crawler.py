# -*- coding: utf-8 -*-
"""
Zhihu crawler — fetches blogger answers and articles.

Supports two modes:
1. API (with cookies): Direct API access when cookies are provided
2. open_link (fallback): Renders profile page via browser agent

Cookies can be provided as a dict or loaded from data/zhihu_cookies.json.

APIs:
- Profile: GET /api/v4/members/{url_slug}
- Answers: GET /api/v4/members/{url_slug}/answers?limit=20&sort_by=created
- Articles: GET /api/v4/members/{url_slug}/articles?limit=20
- Full content: GET /api/v4/answers/{answer_id}
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests

TZ = timezone(timedelta(hours=8))

# Project root for path resolution
_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

API_BASE = "https://www.zhihu.com/api/v4"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://www.zhihu.com/",
    "Origin": "https://www.zhihu.com",
}

COOKIES_FILE = os.path.join(_project_root, "data", "zhihu_cookies.json")


def _ts_to_iso(ts: int) -> str:
    """Convert Unix timestamp to ISO 8601 string in Asia/Shanghai."""
    dt = datetime.fromtimestamp(ts, tz=TZ)
    return dt.strftime('%Y-%m-%dT%H:%M:%S+08:00')


def _strip_html(html: str, max_len: int = 5000) -> str:
    """Simple HTML tag stripper for zhihu content."""
    text = re.sub(r'<[^>]+>', '', html or '')
    text = re.sub(r'&nbsp;', ' ', text)
    text = re.sub(r'&amp;', '&', text)
    text = re.sub(r'&lt;', '<', text)
    text = re.sub(r'&gt;', '>', text)
    text = re.sub(r'&quot;', '"', text)
    text = re.sub(r'\s+', ' ', text).strip()
    if len(text) > max_len:
        text = text[:max_len] + '...'
    return text


def load_cookies(cookies=None) -> Optional[dict]:
    """
    Load cookies from parameter, file, or environment.

    Priority: cookies param > cookies file > ZHI_COOKIE env var > None
    """
    if cookies and isinstance(cookies, dict):
        return cookies
    if cookies and isinstance(cookies, str) and os.path.exists(cookies):
        with open(cookies, encoding='utf-8') as f:
            return json.load(f)
    if os.path.exists(COOKIES_FILE):
        with open(COOKIES_FILE, encoding='utf-8') as f:
            return json.load(f)
    # Try env var ZHI_COOKIE (JSON string or cookie string)
    env_cookie = os.environ.get("ZHI_COOKIE", "")
    if env_cookie:
        try:
            return json.loads(env_cookie)
        except json.JSONDecodeError:
            # Assume format: "key1=val1; key2=val2"
            result = {}
            for part in env_cookie.split(";"):
                part = part.strip()
                if "=" in part:
                    k, v = part.split("=", 1)
                    result[k.strip()] = v.strip()
            if result:
                return result
    return None


def parse_openlink_answers(text: str, slug: str, author_name: str = "") -> list[dict]:
    """
    Parse zhihu profile/answers page text from open_link.

    The open_link renders the page and extracts text. We look for:
    - Answer titles (question titles)
    - Content excerpts
    - Dates
    - Vote counts
    """
    posts = []
    lines = text.split('\n')

    # Try to extract structured info from the rendered text
    # Zhihu profile pages render answers as blocks with question title + excerpt + meta

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue

        # Look for answer blocks: question title as a substantial line followed by content
        # Heuristic: lines that look like questions or have substantial length
        is_title = (
            len(line) > 10 and len(line) < 200 and
            ('？' in line or '?' in line or '如何' in line or '怎么' in line or
             '什么是' in line or '为什么' in line or '如何看待' in line)
        )

        if is_title or (len(line) > 15 and not line.startswith('http') and not line.startswith('#')):
            title = line
            content_parts = []
            meta_parts = []

            # Collect content lines until we hit metadata or next title
            j = i + 1
            while j < len(lines) and j < i + 30:
                nl = lines[j].strip()
                if not nl:
                    j += 1
                    continue

                # Check if this looks like the start of next answer
                next_title = (
                    len(nl) > 10 and len(nl) < 200 and
                    ('？' in nl or '?' in nl or '如何' in nl or '怎么' in nl)
                )
                if next_title:
                    break

                # Check for metadata patterns
                date_match = re.match(r'^(\d{4}-\d{2}-\d{2}|\d{2}-\d{2})\b', nl)
                vote_match = re.match(r'^(\d+[\s]*赞|赞同\s*\d+|．\d+)', nl)
                if date_match or vote_match or re.match(r'^\d+条评论', nl) or re.match(r'^发布于', nl):
                    meta_parts.append(nl)
                elif len(nl) > 3:
                    content_parts.append(nl)

                j += 1

            if content_parts or title:
                content = '\n'.join(content_parts) if content_parts else title
                meta_str = ' '.join(meta_parts)

                posts.append({
                    "post_id": f"zhihu_ol_{slug}_{len(posts)}",
                    "user_id": slug,
                    "author": author_name or slug,
                    "title": title[:200],
                    "content": content[:5000],
                    "created_at": datetime.now(TZ).strftime('%Y-%m-%dT%H:%M:%S+08:00'),
                    "source": "zhihu",
                    "author_uid": slug,
                    "url": f"https://www.zhihu.com/people/{slug}/answers",
                    "type": "answer",
                    "is_retweet": False,
                    "reply_count": 0,
                    "like_count": 0,
                    "stocks": [],
                })

            i = j
        else:
            i += 1

    return posts


class ZhiHuCrawler:
    """
    Crawler for Zhihu blogger answers and articles.

    Usage:
        crawler = ZhiHuCrawler(url_slug='xiao-peng-61-47')
        answers = crawler.fetch_answers(limit=20)
        all_posts = crawler.fetch_all()
        crawler.save_posts(all_posts)
    """

    def __init__(self, url_slug: str, data_dir: Optional[str] = None,
                 cookies: any = None):
        self.slug = str(url_slug)
        if data_dir is None:
            data_dir = os.path.join(_project_root, 'data')
        self.data_dir = data_dir
        self.post_dir = os.path.join(self.data_dir, 'posts', self.slug)
        os.makedirs(self.post_dir, exist_ok=True)

        self._session = None
        self._cookies = load_cookies(cookies)
        self._author_name = None
        self._auth_failed = False

        if self._cookies:
            print(f"[Zhihu] Cookies loaded ({len(self._cookies)} keys)")
            # Warn if cookies file is older than 7 days
            cookies_path = cookies if isinstance(cookies, str) and os.path.exists(cookies) else COOKIES_FILE
            if os.path.exists(cookies_path):
                mtime = os.path.getmtime(cookies_path)
                age_seconds = time.time() - mtime
                age_days = age_seconds / 86400
                if age_days > 7:
                    print(f"[Zhihu] Cookies file is {age_days:.0f} days old, may be expired")

    @property
    def has_auth(self) -> bool:
        return bool(self._cookies) and not self._auth_failed

    @property
    def session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update(DEFAULT_HEADERS)
            if self._cookies:
                for k, v in self._cookies.items():
                    self._session.cookies.set(k, v, domain='.zhihu.com')
        return self._session

    def _request(self, url: str, retries: int = 3) -> Optional[dict]:
        """Make a GET request with retry and backoff."""
        for attempt in range(retries):
            time.sleep(2)  # Rate limit: 2s between requests
            try:
                r = self.session.get(url, timeout=30)
                if r.status_code == 429:
                    wait = min((attempt + 1) * 10, 30)
                    print(f"[Zhihu] 429 rate limited, waiting {wait}s...")
                    time.sleep(wait)
                    continue
                if r.status_code == 401:
                    print(f"[Zhihu] Cookies expired/invalid, switching to open_link fallback permanently")
                    self._auth_failed = True
                    self._cookies = None
                    return None
                if r.status_code == 403:
                    wait = min((attempt + 1) * 5, 15)
                    print(f"[Zhihu] 403 forbidden, waiting {wait}s...")
                    time.sleep(wait)
                    continue
                r.raise_for_status()
                return r.json()
            except requests.exceptions.Timeout:
                print(f"[Zhihu] Timeout for {url}, attempt {attempt + 1}/{retries}")
                time.sleep(3)
            except requests.exceptions.RequestException as e:
                print(f"[Zhihu] Error for {url}: {e}")
                time.sleep(3)
            except json.JSONDecodeError:
                print(f"[Zhihu] Invalid JSON response, attempt {attempt + 1}/{retries}")
                time.sleep(2)
        return None

    def _fetch_author_info(self) -> dict:
        """Fetch author profile to get display name."""
        if self._author_name:
            return {"name": self._author_name}
        url = f"{API_BASE}/members/{self.slug}"
        data = self._request(url)
        if data:
            self._author_name = data.get("name", self.slug)
            return {
                "name": self._author_name,
                "headline": data.get("headline", ""),
                "gender": data.get("gender", -1),
                "follower_count": data.get("follower_count", 0),
            }
        return {"name": self.slug}

    def fetch_answers(self, limit: int = 20) -> list[dict]:
        """
        Fetch answers list via API (requires cookies).
        Falls back to open_link if no cookies or auth failed.
        """
        if self._auth_failed:
            print("[Zhihu] Auth previously failed, using open_link fallback...")
            return self._fetch_via_openlink("answers")
        if not self.has_auth:
            print("[Zhihu] No cookies available, using open_link fallback...")
            return self._fetch_via_openlink("answers")

        url = f"{API_BASE}/members/{self.slug}/answers?limit={min(limit, 20)}&sort_by=created"
        print(f"[Zhihu/API] Fetching answers: {url}")
        data = self._request(url)
        if not data:
            print("[Zhihu] API failed, trying open_link fallback...")
            return self._fetch_via_openlink("answers")

        items = data.get("data", [])
        author_info = self._fetch_author_info()
        author_name = author_info.get("name", self.slug)

        posts = []
        for item in items:
            aid = item.get("id")
            question = item.get("question", {})
            qid = question.get("id", "")
            title = question.get("title", "")
            excerpt = item.get("excerpt", "")
            created_ts = item.get("created_time", 0)
            voteup = item.get("voteup_count", 0)
            comment_count = item.get("comment_count", 0)

            zhihu_url = item.get("url", "")
            if not zhihu_url and qid and aid:
                zhihu_url = f"https://www.zhihu.com/question/{qid}/answer/{aid}"

            posts.append({
                "post_id": f"zhihu_aid_{aid}",
                "user_id": self.slug,
                "author": author_name,
                "title": title,
                "content": excerpt or "",
                "created_at": _ts_to_iso(created_ts),
                "source": "zhihu",
                "author_uid": self.slug,
                "url": zhihu_url,
                "type": "answer",
                "is_retweet": False,
                "reply_count": comment_count,
                "like_count": voteup,
                "stocks": [],
                "question_id": qid,
                "answer_id": aid,
            })

        print(f"[Zhihu/API] Got {len(posts)} answers")
        return posts

    def fetch_answer_content(self, answer_id: int) -> str:
        """Fetch full answer content. Returns text with HTML stripped."""
        url = f"{API_BASE}/answers/{answer_id}?include=content,excerpt"
        data = self._request(url)
        if data:
            return _strip_html(data.get("content", ""))
        return ""

    def fetch_articles(self, limit: int = 20) -> list[dict]:
        """
        Fetch articles list via API (requires cookies).
        Falls back to open_link if no cookies or auth failed.
        """
        if self._auth_failed:
            print("[Zhihu] Auth previously failed, skipping articles (open_link for answers only)")
            return []
        if not self.has_auth:
            print("[Zhihu] No cookies available for articles, skipping (open_link for answers only)")
            return []

        url = f"{API_BASE}/members/{self.slug}/articles?limit={min(limit, 20)}"
        print(f"[Zhihu/API] Fetching articles: {url}")
        data = self._request(url)
        if not data:
            return []

        items = data.get("data", [])
        author_info = self._fetch_author_info()
        author_name = author_info.get("name", self.slug)

        posts = []
        for item in items:
            aid = item.get("id")
            title = item.get("title", "")
            excerpt = item.get("excerpt", "")
            created_ts = item.get("created", 0)
            voteup = item.get("voteup_count", 0)
            comment_count = item.get("comment_count", 0)
            zhihu_url = item.get("url", "")
            if not zhihu_url and aid:
                zhihu_url = f"https://zhuanlan.zhihu.com/p/{aid}"

            posts.append({
                "post_id": f"zhihu_article_{aid}",
                "user_id": self.slug,
                "author": author_name,
                "title": title,
                "content": excerpt or "",
                "created_at": _ts_to_iso(created_ts),
                "source": "zhihu",
                "author_uid": self.slug,
                "url": zhihu_url,
                "type": "article",
                "is_retweet": False,
                "reply_count": comment_count,
                "like_count": voteup,
                "stocks": [],
                "article_id": aid,
            })

        print(f"[Zhihu/API] Got {len(posts)} articles")
        return posts

    def _fetch_via_openlink(self, tab: str = "answers") -> list[dict]:
        """Fetch answers using open_link browser agent."""
        from src.utils.open_link import fetch_page

        if tab == "answers":
            url = f"https://www.zhihu.com/people/{self.slug}/answers"
        else:
            url = f"https://www.zhihu.com/people/{self.slug}"

        print(f"[Zhihu/OL] Fetching: {url}")
        try:
            text = fetch_page(url, timeout=60)
        except Exception as e:
            print(f"[Zhihu/OL] Error: {e}")
            return []

        if not text or len(text) < 50:
            print(f"[Zhihu/OL] Empty/too-short response ({len(text) if text else 0} chars)")
            return []

        # Check for captcha/block
        if '验证' in text[:500] or '异常行为' in text[:500]:
            print("[Zhihu/OL] Captcha detected — page blocked by anti-bot")
            return []

        author_info = self._fetch_author_info()
        author_name = author_info.get("name", self.slug) if author_info else self.slug

        print(f"[Zhihu/OL] Got {len(text)} chars, parsing...")
        posts = parse_openlink_answers(text, self.slug, author_name)
        return posts

    def fetch_answers_with_full_content(self, limit: int = 20) -> list[dict]:
        """Fetch answers and enrich with full content (API-only, requires cookies)."""
        if self._auth_failed:
            print("[Zhihu] Auth failed — cannot enrich with full content, returning basic answers only")
        posts = self.fetch_answers(limit=limit)
        if self.has_auth:
            for post in posts:
                aid = post.get("answer_id")
                if aid:
                    full = self.fetch_answer_content(aid)
                    if full and len(full) > len(post.get("content", "")):
                        post["content"] = full
        return posts

    def fetch_all(self, limit: int = 20, full_content: bool = False) -> list[dict]:
        """
        Fetch answers + articles, merge and de-duplicate.

        Args:
            limit: Max items per category
            full_content: Whether to fetch full content (requires cookies, more API calls)

        Returns:
            Combined list of post dicts, sorted by created_at descending
        """
        if full_content:
            answers = self.fetch_answers_with_full_content(limit=limit)
        else:
            answers = self.fetch_answers(limit=limit)
        articles = self.fetch_articles(limit=limit)

        # Merge and deduplicate by post_id
        seen = set()
        all_posts = []
        for post in answers + articles:
            pid = post.get("post_id", "")
            if pid and pid not in seen:
                seen.add(pid)
                all_posts.append(post)

        all_posts.sort(key=lambda p: p.get("created_at", ""), reverse=True)
        print(f"[Zhihu] Total: {len(all_posts)} posts ({len(answers)} answers + {len(articles)} articles)")
        return all_posts

    def save_posts(self, posts: list[dict], date_str: Optional[str] = None):
        """
        Save posts to JSON file, merging with existing data by post_id.

        Args:
            posts: List of post dicts
            date_str: Date string for filename (default: today)
        """
        if date_str is None:
            date_str = datetime.now(TZ).strftime('%Y-%m-%d')

        filepath = os.path.join(self.post_dir, f'{date_str}.json')

        existing = []
        if os.path.exists(filepath):
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    existing = json.load(f)
            except (json.JSONDecodeError, IOError):
                pass

        merged = {}
        for post in existing:
            pid = post.get("post_id", "")
            if pid:
                merged[pid] = post
        for post in posts:
            pid = post.get("post_id", "")
            if pid:
                merged[pid] = post

        merged_list = sorted(
            merged.values(),
            key=lambda p: p.get("created_at", ""),
            reverse=True,
        )

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(merged_list, f, ensure_ascii=False, indent=2)

        print(f"[Zhihu] Saved {len(merged_list)} posts to {filepath}")
        return filepath

    def load_posts(self, date_str: str) -> list[dict]:
        """Load posts from a saved JSON file."""
        filepath = os.path.join(self.post_dir, f'{date_str}.json')
        if not os.path.exists(filepath):
            return []
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
