# -*- coding: utf-8 -*-
"""
Xueqiu post parser — handles both API JSON responses and open_link page text.

Unified output format for all parse functions:

    {
        "post_id": int,         # 帖子ID (from API: id)
        "user_id": int,         # 用户ID (from API: user_id)
        "author": str,          # 作者昵称
        "title": str,           # 标题 (from API: title)
        "content": str,         # 正文 (from API: description / openlink extracted)
        "created_at_ts": int,   # Unix毫秒时间戳
        "created_at": str,      # ISO 8601 +08:00
        "time_before": str,     # 人类可读时间 (from API: timeBefore)
        "source": str,          # 来源设备 (from API: source)
        "type": str,            # 帖子类型 "0"=普通 "3"=长文
        "is_retweet": bool,     # 是否转帖 (retweeted_status_id > 0)
        "retweet_id": int,      # 转帖原文ID
        "retweet_content": str, # 转帖原文内容
        "reply_count": int,     # 回复数
        "like_count": int,      # 点赞数
        "stocks": [str],        # 关联股票代码 (from API: stockCorrelation)
        "is_pinned": bool,      # 是否置顶
    }
"""

import hashlib
import re
from datetime import datetime, timezone, timedelta
from typing import Optional

TZ = timezone(timedelta(hours=8))

# ── Regex patterns for open_link text parsing ──────────────────────────

# Regex: author + time + optional source + optional "置顶"
# Groups: 1=author, 2=time_text, 3=source (after ·)
RE_POST_HEADER = re.compile(
    r'^(.+?)\s+'
    r'(\d+分钟前|\d+小时前|昨天\s*\d{1,2}:\d{2}|'
    r'\d{1,2}-\d{1,2}\s+\d{1,2}:\d{2}|'
    r'\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2}(?::\d{2})?)'
    r'(?:·\s*(.+?))?(?:\s*置顶)?$'
)

# Retweet detection: "回复@user:" or "转发@user:"
RE_RETWEET = re.compile(r'^(回复|转发)@(\S+?)[:：]\s*(.*)', re.DOTALL)

# Stats line: has "转发" and "收藏" with icons
RE_STATS = re.compile(r'转发.*?收藏')

# Pinned marker
RE_PINNED = re.compile(r'置顶')


# ── Time parsing utilities ─────────────────────────────────────────────

def _ms_to_iso(ts_ms: int) -> str:
    """Convert Unix millisecond timestamp to ISO 8601 +08:00 string."""
    dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=TZ)
    return dt.strftime('%Y-%m-%dT%H:%M:%S+08:00')


def _parse_time(time_raw: str, now: Optional[datetime] = None) -> str:
    """Convert raw time text (open_link style) to ISO 8601 string with +08:00."""
    if now is None:
        now = datetime.now(TZ)

    time_raw = time_raw.strip()

    # "X分钟前"
    m = re.match(r'(\d+)分钟前', time_raw)
    if m:
        dt = now - timedelta(minutes=int(m.group(1)))
        return dt.strftime('%Y-%m-%dT%H:%M:%S+08:00')

    # "X小时前"
    m = re.match(r'(\d+)小时前', time_raw)
    if m:
        dt = now - timedelta(hours=int(m.group(1)))
        return dt.strftime('%Y-%m-%dT%H:%M:%S+08:00')

    # "昨天 HH:MM"
    m = re.match(r'昨天\s*(\d{1,2}):(\d{2})', time_raw)
    if m:
        yesterday = now - timedelta(days=1)
        dt = yesterday.replace(hour=int(m.group(1)), minute=int(m.group(2)),
                               second=0, microsecond=0)
        return dt.strftime('%Y-%m-%dT%H:%M:%S+08:00')

    # "MM-DD HH:MM"
    m = re.match(r'(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{2})$', time_raw)
    if m:
        dt = datetime(now.year, int(m.group(1)), int(m.group(2)),
                      int(m.group(3)), int(m.group(4)), 0, tzinfo=TZ)
        return dt.strftime('%Y-%m-%dT%H:%M:%S+08:00')

    # "YYYY-MM-DD HH:MM:SS" or "YYYY-MM-DD HH:MM"
    m = re.match(r'(\d{4})-(\d{2})-(\d{2})\s+(\d{1,2}):(\d{2})(?::(\d{2}))?', time_raw)
    if m:
        sec = int(m.group(6)) if m.group(6) else 0
        dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                      int(m.group(4)), int(m.group(5)), sec, tzinfo=TZ)
        return dt.strftime('%Y-%m-%dT%H:%M:%S+08:00')

    return time_raw


# ── API response parser ────────────────────────────────────────────────

def parse_api_response(json_data: dict) -> list[dict]:
    """
    Parse Xueqiu API JSON response into unified post dicts.

    API endpoint: GET /v4/statuses/user_timeline.json?user_id={uid}&page={n}
    Response format: {"count": 20, "statuses": [...]}

    Real field mapping:
        id          → post_id
        user_id     → user_id
        created_at  → created_at_ts (Unix毫秒时间戳)
        description → content (正文, 优先取text字段避免截断)
        title       → title
        type        → type ("0"=普通, "3"=长文)
        source      → source (设备来源)
        reply_count → reply_count
        like_count  → like_count
        retweeted_status_id → retweet_id (有值且>0表示转帖)
        retweeted_status    → retweet_content 来源
        stockCorrelation    → stocks (关联股票代码列表)
        timeBefore  → time_before (人类可读时间)
        user (dict) → author (screen_name)
    """
    posts = []
    statuses = json_data.get('statuses', [])
    if not statuses:
        return posts

    for s in statuses:
        try:
            # Core fields
            post_id = int(s.get('id', 0))
            user_id = int(s.get('user_id', 0))
            created_at_ts = int(s.get('created_at', 0))
            content = s.get('text', '') or s.get('description', '') or ''
            title = s.get('title', '') or ''
            post_type = str(s.get('type', '0'))
            source = s.get('source', '') or ''
            reply_count = int(s.get('reply_count', 0))
            like_count = int(s.get('like_count', 0))
            time_before = s.get('timeBefore', '') or ''

            # Author from nested user object
            author = ''
            user_obj = s.get('user')
            if isinstance(user_obj, dict):
                author = user_obj.get('screen_name', '') or ''

            # Retweet detection
            retweet_id = int(s.get('retweeted_status_id', 0) or 0)
            is_retweet = retweet_id > 0

            retweet_content = ''
            if is_retweet:
                rt = s.get('retweeted_status')
                if isinstance(rt, dict):
                    retweet_content = rt.get('text', '') or rt.get('description', '') or ''
                elif isinstance(rt, str):
                    retweet_content = rt

            # Stock correlation
            stocks = s.get('stockCorrelation', [])
            if not isinstance(stocks, list):
                stocks = []
            stocks = [str(x) for x in stocks]

            # Pinned detection (heuristic: check title/description for pin markers)
            is_pinned = bool(s.get('is_pinned', False))

            # Time conversion
            created_at = _ms_to_iso(created_at_ts) if created_at_ts else ''

            posts.append({
                'post_id': post_id,
                'user_id': user_id,
                'author': author,
                'title': title,
                'content': content,
                'created_at_ts': created_at_ts,
                'created_at': created_at,
                'time_before': time_before,
                'source': source,
                'type': post_type,
                'is_retweet': is_retweet,
                'retweet_id': retweet_id,
                'retweet_content': retweet_content,
                'reply_count': reply_count,
                'like_count': like_count,
                'stocks': stocks,
                'is_pinned': is_pinned,
            })
        except Exception:
            # Skip malformed posts, don't crash the whole parser
            continue

    return posts


# ── OpenLink text parser (existing logic, improved) ───────────────────

def _parse_stats(stats_line: str) -> tuple:
    """Extract comment and like counts from a stats line. Returns (comments, likes, shares)."""
    numbers = [int(n) for n in re.findall(r'(\d+)', stats_line)]
    if len(numbers) >= 2:
        return numbers[0], numbers[1], 0
    elif len(numbers) == 1:
        return numbers[0], 0, 0
    return 0, 0, 0


def _parse_header(first_line: str, now: Optional[datetime] = None) -> dict:
    """Parse a post header line. Returns dict with author, time_raw, created_at, source, is_pinned."""
    result = {'author': '', 'time_raw': '', 'created_at': '', 'source': '', 'is_pinned': False}
    line = first_line.strip()

    # Check for pinned
    if '置顶' in line:
        result['is_pinned'] = True

    m = RE_POST_HEADER.match(line)
    if m:
        result['author'] = m.group(1).strip()
        result['time_raw'] = m.group(2).strip()
        result['created_at'] = _parse_time(result['time_raw'], now)
        result['source'] = (m.group(3) or '').strip()
    else:
        # Fallback: try to split by known patterns
        time_match = re.search(
            r'(\d+分钟前|\d+小时前|昨天\s*\d{1,2}:\d{2}|'
            r'\d{1,2}-\d{1,2}\s+\d{1,2}:\d{2}|'
            r'\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2}(?::\d{2})?)',
            line
        )
        if time_match:
            result['author'] = line[:time_match.start()].strip()
            result['time_raw'] = time_match.group(1).strip()
            result['created_at'] = _parse_time(result['time_raw'], now)
            after = line[time_match.end():].strip()
            if after.startswith('·'):
                after = after[1:].strip()
            result['source'] = after.replace('置顶', '').strip()

    return result


def _make_post_id(author: str, title: str, created_at: str) -> str:
    """Generate a deterministic hash-based post ID for open_link posts."""
    raw = (author + title + created_at).encode('utf-8')
    return hashlib.md5(raw).hexdigest()[:12]


def _parse_post_block(lines: list, now: Optional[datetime] = None) -> Optional[dict]:
    """
    Parse a single post block (list of lines from header to before next post).

    Returns post dict in unified format, or None if not a valid post block.
    """
    if not lines:
        return None

    # Strip leading/trailing blank lines
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()

    if not lines:
        return None

    # Parse header
    header = _parse_header(lines[0], now)

    # Build content body (everything between header and stats line)
    body_lines = []
    stats_line = ''
    for i in range(1, len(lines)):
        stripped = lines[i].strip()
        if ('转发' in stripped and '收藏' in stripped):
            stats_line = stripped
            break
        body_lines.append(lines[i])

    # Clean body: remove _投诉_, _举报_, UX elements
    content_lines = []
    for line in body_lines:
        stripped = line.strip()
        if stripped in ('_投诉_', '_举报_', '__ 投诉', '投诉'):
            continue
        content_lines.append(line)

    full_body = '\n'.join(content_lines).strip()

    # Detect retweet and extract
    is_retweet = False
    retweet_author = ''
    retweet_content = ''
    title = ''

    retweet_match = RE_RETWEET.match(full_body)
    if retweet_match:
        is_retweet = True
        retweet_author = retweet_match.group(2)
        rt_content = retweet_match.group(3)
        # Remove subsequent //@ chains (cascaded retweets)
        rt_content = re.sub(r'//@\S+?[:：][^@]*$', '', rt_content).strip()
        # Remove quoted original post display (查看对话, > ...)
        rt_content = re.sub(r'\n查看对话\n.*', '', rt_content, flags=re.DOTALL).strip()
        retweet_content = rt_content

    # Determine title: first non-empty content line
    if content_lines:
        for line in content_lines:
            stripped = line.strip()
            if stripped and not stripped.startswith('>') and stripped != '查看对话':
                if not re.match(r'^(回复|转发)@', stripped):
                    title = stripped
                    break

    if not title and full_body:
        # Fallback title from body
        lines_body = full_body.split('\n')
        for line in lines_body:
            s = line.strip()
            if s and not s.startswith('>'):
                title = s[:100]
                break

    # Parse stats
    comments = 0
    likes = 0
    if stats_line:
        comments, likes, _ = _parse_stats(stats_line)

    post_id = _make_post_id(header['author'], title, header['created_at'])
    return {
        'post_id': post_id,
        'post_id_int': 0,  # open_link doesn't provide a numeric post_id
        'post_id_source': 'openlink_hash',
        'user_id': 0,
        'author': header['author'],
        'title': title,
        'content': full_body,
        'created_at_ts': 0,
        'created_at': header['created_at'],
        'time_before': header['time_raw'],
        'source': header['source'],
        'type': '0',
        'is_retweet': is_retweet,
        'retweet_id': 0,
        'retweet_content': retweet_content,
        'reply_count': comments,
        'like_count': likes,
        'stocks': [],
        'is_pinned': header['is_pinned'],
    }


def parse_openlink_text(text: str, now: Optional[datetime] = None) -> list[dict]:
    """
    Parse open_link page text into a list of structured post dicts.

    Strategy: find all lines that match the post header pattern (author + time),
    then extract the block from each header to the next header.

    Args:
        text: Raw text from open_link
        now: Reference time for relative time parsing

    Returns:
        List of post dicts in unified format
    """
    if not text or not text.strip():
        return []

    lines = text.split('\n')

    # Find indices of all post header lines
    header_indices = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        # Skip quoted lines (retweet display) and lines with discussion metadata
        if stripped.startswith('>'):
            continue
        if '讨论' in stripped and '赞' in stripped:
            continue
        if RE_POST_HEADER.match(stripped):
            header_indices.append(i)

    if not header_indices:
        return []

    posts = []
    for idx, start in enumerate(header_indices):
        end = header_indices[idx + 1] if idx + 1 < len(header_indices) else len(lines)
        block = lines[start:end]

        try:
            post = _parse_post_block(block, now)
            if post and post['author']:
                posts.append(post)
        except Exception:
            # Skip malformed posts, don't crash the whole parser
            continue

    return posts


# ── Backward-compatible aliases ───────────────────────────────────────

def parse_posts(text: str, now: Optional[datetime] = None) -> list[dict]:
    """Backward-compatible alias for parse_openlink_text()."""
    return parse_openlink_text(text, now)


def parse_time(raw_time: str, now: Optional[datetime] = None) -> str:
    """Public wrapper for time parsing."""
    return _parse_time(raw_time, now)
