"""AutoGLM Open Link API client — bypasses WAF for page content extraction."""
import time
import hashlib
import json
import urllib.request
import urllib.error
from typing import Optional

APP_ID = "100003"
APP_KEY = "38d2391985e2369a5fb8227d8e6cd5e5"
API_URL = "https://autoglm-api.zhipuai.cn/agentdr/v1/assistant/skills/open-link"
TOKEN_URL = "http://127.0.0.1:18432/get_token"

# Cache token for reuse
_token_cache: Optional[str] = None
_token_ts: float = 0
TOKEN_TTL = 300  # 5 minutes


def _get_token() -> str:
    global _token_cache, _token_ts
    now = time.time()
    if _token_cache and (now - _token_ts) < TOKEN_TTL:
        return _token_cache

    with urllib.request.urlopen(TOKEN_URL) as resp:
        token = resp.read().decode("utf-8").strip()
    if not token.lower().startswith("bearer "):
        token = f"Bearer {token}"
    _token_cache = token
    _token_ts = now
    return token


def fetch_page(url: str, timeout: int = 30) -> str:
    """
    Fetch and extract text content from a URL via AutoGLM Open Link API.
    Returns the extracted text, or raises an exception on failure.
    """
    token = _get_token()
    ts = int(time.time())
    sign = hashlib.md5(f"{APP_ID}&{ts}&{APP_KEY}".encode()).hexdigest()

    payload = json.dumps({"url": url}).encode("utf-8")
    req = urllib.request.Request(API_URL, data=payload, method="POST")
    req.add_header("Authorization", token)
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Auth-Appid", APP_ID)
    req.add_header("X-Auth-TimeStamp", str(ts))
    req.add_header("X-Auth-Sign", sign)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Open Link HTTP {e.code}: {e.read().decode()[:500]}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Open Link request failed: {e.reason}")

    if data.get("code") != 0:
        raise RuntimeError(f"Open Link API error: {data.get('msg', 'unknown')}")

    return data["data"]["text"]
