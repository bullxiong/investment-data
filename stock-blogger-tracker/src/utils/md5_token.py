# -*- coding: utf-8 -*-
"""
Extract Aliyun WAF md5__1038 token from AutoGLM browser session data.

The WAF challenge JS generates this token after passing the browser challenge.
We extract it from the most recent browser task result that successfully
accessed a Xueqiu API page.

Returns None if no valid token is found.
"""
import json, os, re


def _find_latest_session_dir() -> str | None:
    """Find the most recent AutoGLM session that accessed a Xueqiu API."""
    sessions_root = os.path.expandvars(r'%USERPROFILE%\.openclaw-autoclaw\sessions')
    if not os.path.isdir(sessions_root):
        return None

    best_dir = None
    best_time = 0

    for name in os.listdir(sessions_root):
        path = os.path.join(sessions_root, name)
        if not os.path.isdir(path):
            continue
        result_file = os.path.join(path, 'task_result.md')
        if not os.path.exists(result_file):
            continue

        mtime = os.path.getmtime(result_file)
        if mtime > best_time:
            # Check if this session accessed a Xueqiu API
            try:
                with open(result_file, encoding='utf-8') as f:
                    content = f.read(10000)  # First 10KB is enough
                if 'xueqiu.com/v4/statuses/user_timeline.json' in content:
                    best_time = mtime
                    best_dir = path
            except Exception:
                continue

    return best_dir


def get_md5_token() -> str | None:
    """
    Extract the Aliyun WAF md5__1038 token from the most recent browser session.

    Returns:
        The token string (URL-encoded), or None if not found.
    """
    session_dir = _find_latest_session_dir()
    if not session_dir:
        return None

    result_file = os.path.join(session_dir, 'task_result.md')
    try:
        with open(result_file, encoding='utf-8') as f:
            content = f.read()
    except Exception:
        return None

    # Look for the URL with md5__1038 parameter in the [tabs] section
    # Format: url=https://xueqiu.com/v4/statuses/user_timeline.json?user_id=...&md5__1038=...
    match = re.search(
        r'https?://xueqiu\.com/v4/statuses/user_timeline\.json\?[^\s"\']*md5__1038=([^\s"\']+)',
        content
    )
    if match:
        return match.group(1)

    # Alternative: look for md5__1038 in any URL
    match = re.search(r'md5__1038=([^\s"\'&\]]+)', content)
    return match.group(1) if match else None


if __name__ == '__main__':
    token = get_md5_token()
    if token:
        print(f"Found token (length={len(token)}): {token[:80]}...")
    else:
        print("No token found")
