# -*- coding: utf-8 -*-
"""
雪球WAF Token循环获取器

每页需要独立WAF token。通过浏览器agent批量获取token后调用API。

流程（每页）:
  1. subprocess 调 autoglm run 打开 API URL → 浏览器解WAF → 生成 token
  2. md5_token.py 从最新浏览器session提取 token
  3. requests 带 token + cookies 调 API
  4. parse_api_response 解析 → 收集 posts

重试: 最多3次，每次间隔10秒。
回退: autoglm run 不可用时回退到现有 XueqiuCrawler.fetch_via_api()。
"""

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests

_project_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from src.crawlers.xueqiu.parser import parse_api_response

TZ = timezone(timedelta(hours=8))
API_BASE = "https://xueqiu.com/v4/statuses/user_timeline.json"
DEFAULT_COOKIES_PATH = os.path.join(_project_root, "data", "xueqiu_cookies.json")

# ── autoglm 路径探测 ────────────────────────────────────────────────

_AUTOGLM_BIN = None


def _find_autoglm() -> Optional[str]:
    """探测 autoglm 可执行文件路径。返回 None 表示不可用。"""
    global _AUTOGLM_BIN
    if _AUTOGLM_BIN is not None:
        return _AUTOGLM_BIN if _AUTOGLM_BIN else None

    # 1. 用户目录下的 bin
    candidates = [
        os.path.expandvars(r"%USERPROFILE%\.openclaw-autoclaw\bin\autoglm"),
        os.path.expandvars(r"%USERPROFILE%\.openclaw-autoclaw\bin\autoglm.exe"),
        os.path.expandvars(r"%USERPROFILE%\.openclaw-autoclaw\bin\autoglm.cmd"),
        os.path.expandvars(r"%USERPROFILE%\.openclaw-autoclaw\bin\autoglm.bat"),
    ]

    for c in candidates:
        if os.path.isfile(c):
            _AUTOGLM_BIN = c
            return c

    # 2. PATH 中查找
    import shutil
    autoglm_path = shutil.which("autoglm")
    if autoglm_path:
        _AUTOGLM_BIN = autoglm_path
        return autoglm_path

    _AUTOGLM_BIN = ""  # sentinel: tried, not found
    return None


# ── Token 提取 ───────────────────────────────────────────────────────

def _get_md5_token_from_session() -> Optional[str]:
    """从最新浏览器session提取 md5__1038 token。复用 md5_token.py 逻辑。"""
    try:
        from src.utils.md5_token import get_md5_token
        return get_md5_token()
    except ImportError:
        pass

    # 内联 fallback
    sessions_root = os.path.expandvars(r"%USERPROFILE%\.openclaw-autoclaw\sessions")
    if not os.path.isdir(sessions_root):
        return None

    best_dir = None
    best_time = 0
    for name in os.listdir(sessions_root):
        path = os.path.join(sessions_root, name)
        if not os.path.isdir(path):
            continue
        result_file = os.path.join(path, "task_result.md")
        if not os.path.exists(result_file):
            continue
        mtime = os.path.getmtime(result_file)
        if mtime > best_time:
            try:
                with open(result_file, encoding="utf-8") as f:
                    content = f.read(10000)
                if "xueqiu.com/v4/statuses/user_timeline.json" in content:
                    best_time = mtime
                    best_dir = path
            except Exception:
                continue

    if not best_dir:
        return None

    result_file = os.path.join(best_dir, "task_result.md")
    try:
        with open(result_file, encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return None

    m = re.search(
        r"https?://xueqiu\.com/v4/statuses/user_timeline\.json\?[^\s\"']*md5__1038=([^\s\"']+)",
        content,
    )
    if m:
        return m.group(1)

    m = re.search(r"md5__1038=([^\s\"'&\]]+)", content)
    return m.group(1) if m else None


# ── TokenCycler ──────────────────────────────────────────────────────

class TokenCycler:
    """
    雪球WAF Token循环获取器。

    每页需要独立token。通过 autoglm run 浏览器agent解WAF → 提取token → API调用。

    用法:
        cycler = TokenCycler()
        posts = cycler.fetch_posts("7251377368", max_pages=3)
    """

    def __init__(
        self,
        cookies_path: Optional[str] = None,
        max_retries: int = 3,
        retry_delay: float = 10.0,
        request_timeout: int = 30,
    ):
        """
        Args:
            cookies_path: 雪球cookies JSON路径。默认 data/xueqiu_cookies.json
            max_retries: token获取最大重试次数
            retry_delay: 重试间隔秒数
            request_timeout: API请求超时秒数
        """
        self.cookies_path = cookies_path or DEFAULT_COOKIES_PATH
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.request_timeout = request_timeout

        # 加载cookies
        self.cookies = {}
        if os.path.exists(self.cookies_path):
            with open(self.cookies_path, encoding="utf-8") as f:
                self.cookies = json.load(f)

        self._session = None
        self._autoglm_available: Optional[bool] = None

    @property
    def has_autoglm(self) -> bool:
        """autoglm run 是否可用。"""
        if self._autoglm_available is None:
            self._autoglm_available = _find_autoglm() is not None
        return self._autoglm_available

    def _is_browser_available(self):
        """检测浏览器是否可用（检查Chrome进程）"""
        import subprocess
        try:
            result = subprocess.run(
                ['tasklist', '/fi', 'IMAGENAME eq chrome.exe'],
                capture_output=True, text=True, timeout=5
            )
            return 'chrome.exe' in result.stdout
        except Exception:
            return False

    def _fallback_openlink(self, uid):
        """TokenCycler不可用时的 open_link 兜底"""
        try:
            from src.crawlers.xueqiu.crawler import XueqiuCrawler
            crawler = XueqiuCrawler(uid=uid)
            return crawler.fetch_via_openlink(page=1)
        except Exception as e:
            print(f"[TokenCycler] open_link fallback also failed: {e}")
            return []

    @property
    def http_session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update(
                {
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/131.0.0.0 Safari/537.36"
                    ),
                    "Accept": "application/json, text/plain, */*",
                    "Referer": "https://xueqiu.com/",
                }
            )
            for k, v in self.cookies.items():
                self._session.cookies.set(k, v, domain=".xueqiu.com")
        return self._session

    # ── Token 获取 ────────────────────────────────────────────────

    def get_fresh_token(self, uid: str, page: int = 1) -> Optional[str]:
        """
        通过 autoglm run 获取新 token。

        子进程调用 autoglm run --task "打开 API_URL"，
        阻塞等待完成，然后从浏览器session提取 token。

        Args:
            uid: 用户ID
            page: 页码

        Returns:
            URL-encoded md5__1038 token，失败返回 None。
        """
        api_url = f"{API_BASE}?user_id={uid}&page={page}"

        autoglm_bin = _find_autoglm()
        if not autoglm_bin:
            return None

        cmd = [
            autoglm_bin,
            "run",
            "--task",
            f"打开 {api_url} 等页面JSON加载完成",
        ]

        print(f"[TokenCycler] 获取 token: autoglm run (uid={uid}, page={page})")

        for attempt in range(1, self.max_retries + 1):
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    cwd=_project_root,
                    creationflags=(
                        subprocess.CREATE_NO_WINDOW
                        if sys.platform == "win32"
                        else 0
                    ),
                )

                if result.returncode != 0:
                    stderr_tail = (
                        result.stderr[-200:] if result.stderr else "(无输出)"
                    )
                    print(
                        f"[TokenCycler] autoglm 退出码 {result.returncode} "
                        f"(尝试 {attempt}/{self.max_retries}): {stderr_tail}"
                    )
                else:
                    print(f"[TokenCycler] autoglm 完成，提取 token...")

                # 尝试从session提取token
                time.sleep(1)  # 让文件系统刷新
                token = _get_md5_token_from_session()
                if token:
                    print(f"[TokenCycler] 获取到 token (长度={len(token)})")
                    return token

                print(
                    f"[TokenCycler] token提取失败 (尝试 {attempt}/{self.max_retries})"
                )

            except subprocess.TimeoutExpired:
                print(
                    f"[TokenCycler] autoglm 超时 (尝试 {attempt}/{self.max_retries})"
                )
                # 尝试杀子进程
                try:
                    result.kill()
                except Exception:
                    pass

            except FileNotFoundError:
                print(f"[TokenCycler] autoglm 不可用: 找不到 {autoglm_bin}")
                self._autoglm_available = False
                return None

            except Exception as e:
                print(
                    f"[TokenCycler] 异常 (尝试 {attempt}/{self.max_retries}): {e}"
                )

            if attempt < self.max_retries:
                print(f"[TokenCycler] 等待 {self.retry_delay}s 后重试...")
                time.sleep(self.retry_delay)

        return None

    # ── API 调用 ─────────────────────────────────────────────────

    def _call_api(self, uid: str, page: int, token: str) -> tuple[list[dict], str]:
        """
        用 token 调用API获取单页帖子。

        Returns:
            (posts, status): posts 列表 + 状态码
                status: "ok" | "empty" | "waf_block" | "http_error"
        """
        url = f"{API_BASE}?user_id={uid}&page={page}"
        if token:
            url += f"&md5__1038={token}"

        print(f"[TokenCycler/API] 请求 page={page}")
        try:
            r = self.http_session.get(url, timeout=self.request_timeout)
            r.raise_for_status()
            raw_text = r.text
            data = r.json()

            # 检查是否有实质性数据
            statuses = data.get("statuses")
            if statuses is None:
                # 响应是JSON但结构异常 → token/WAF问题
                if "error" in str(data).lower() or "captcha" in raw_text.lower():
                    print(f"[TokenCycler/API] page={page}: JSON含错误/WAF标记")
                    return [], "waf_block"
                print(f"[TokenCycler/API] page={page}: JSON无statuses字段")
                return [], "empty"

            posts = parse_api_response(data)
            if posts:
                print(f"[TokenCycler/API] page={page}: {len(posts)} 个帖子")
                return posts, "ok"
            else:
                print(f"[TokenCycler/API] page={page}: statuses为空，可能到底了")
                return [], "empty"

        except json.JSONDecodeError:
            # 非JSON响应 — 通常是WAF挑战页或token过期
            print(f"[TokenCycler/API] page={page}: 非JSON响应 (WAF拦截/token过期)")
            return [], "waf_block"
        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code if e.response else 0
            print(f"[TokenCycler/API] page={page}: HTTP {status_code}")
            return [], "http_error"
        except Exception as e:
            print(f"[TokenCycler/API] page={page} 请求失败: {e}")
            return [], "http_error"

    # ── 主入口 ───────────────────────────────────────────────────

    def fetch_posts(self, uid: str, max_pages: int = 3) -> list[dict]:
        """
        为指定 uid 拉取 max_pages 页帖子。

        每页流程:
          1. autoglm run 获取 token
          2. requests 调 API
          3. parse_api_response

        若 autoglm 不可用，回退到 XueqiuCrawler.fetch_via_api()。

        Args:
            uid: 雪球用户ID
            max_pages: 最大页数

        Returns:
            所有帖子列表（去重，按时间降序）
        """
        uid = str(uid)
        all_posts = []
        seen_ids = set()

        # 预检测：浏览器不可用则直接走 open_link fallback
        if not self._is_browser_available():
            print("[TokenCycler] Chrome not running, using open_link fallback")
            return self._fallback_openlink(uid)

        if not self.has_autoglm:
            print("[TokenCycler] autoglm 不可用，回退到 XueqiuCrawler.fetch_via_api")
            return self._fallback_fetch(uid, max_pages)

        for page in range(1, max_pages + 1):
            # ── Token 获取（含 token 失效重试）──
            posts = []
            api_status = "no_token"
            token_attempts = 0

            while token_attempts < self.max_retries:
                token = self.get_fresh_token(uid, page)
                if not token:
                    token_attempts += 1
                    if token_attempts >= self.max_retries:
                        print(
                            f"[TokenCycler] page={page}: {self.max_retries}次"
                            f"token获取均失败，尝试回退..."
                        )
                        posts = self._try_crawler_api(uid, page)
                        api_status = "fallback"
                    else:
                        print(
                            f"[TokenCycler] page={page}: token获取失败"
                            f" ({token_attempts}/{self.max_retries})，重试..."
                        )
                        time.sleep(self.retry_delay)
                    continue

                posts, api_status = self._call_api(uid, page, token)

                if api_status == "ok":
                    break  # 成功
                elif api_status == "waf_block":
                    # token失效：重试获取新token
                    token_attempts += 1
                    print(
                        f"[TokenCycler] page={page}: WAF拦截/token失效"
                        f" ({token_attempts}/{self.max_retries})"
                    )
                    if token_attempts >= self.max_retries:
                        print(
                            f"[TokenCycler] page={page}: "
                            f"多次token均被WAF拦截，可能需重新登录雪球"
                        )
                        posts = self._try_crawler_api(uid, page)
                        api_status = "fallback"
                    else:
                        time.sleep(self.retry_delay)
                        continue
                elif api_status == "http_error":
                    # HTTP错误，重试
                    token_attempts += 1
                    if token_attempts < self.max_retries:
                        time.sleep(self.retry_delay)
                        continue
                    posts = []
                else:
                    # empty: 正常到底了
                    break

            # ── 处理结果 ──
            if not posts:
                if api_status == "empty":
                    print(f"[TokenCycler] page={page}: 无数据，停止翻页")
                    break
                elif api_status in ("waf_block", "http_error"):
                    print(
                        f"[TokenCycler] page={page}: "
                        f"所有重试耗尽({api_status})，停止翻页"
                    )
                    break
                else:
                    print(f"[TokenCycler] page={page}: 无数据，停止翻页")
                    break

            new_count = 0
            for p in posts:
                pid = p.get("post_id", 0)
                if pid and pid not in seen_ids:
                    seen_ids.add(pid)
                    all_posts.append(p)
                    new_count += 1

            print(
                f"[TokenCycler] page={page}: {new_count} 新帖子 "
                f"(累计 {len(all_posts)})"
            )

            if new_count == 0:
                print("[TokenCycler] 无新帖子，停止翻页")
                break

            # 页间延迟
            if page < max_pages:
                time.sleep(2)

        all_posts.sort(key=lambda p: p.get("created_at", ""), reverse=True)
        print(f"[TokenCycler] 完成: {len(all_posts)} 个帖子 ({max_pages} 页)")
        return all_posts

    # ── 回退 ─────────────────────────────────────────────────────

    def _try_crawler_api(self, uid: str, page: int) -> list[dict]:
        """
        尝试直接用 XueqiuCrawler.fetch_via_api() 拉取单页。
        这个会从已有的浏览器session提取 token（如果有的话）。
        """
        try:
            from src.crawlers.xueqiu.crawler import XueqiuCrawler

            xq_token = self.cookies.get("xq_a_token", "")
            crawler = XueqiuCrawler(
                uid=uid, xq_token=xq_token, rate_limit=1.0
            )
            return crawler.fetch_via_api(page)
        except Exception as e:
            print(f"[TokenCycler] 回退 fetch_via_api 也失败 ({e})")
            return []

    def _fallback_fetch(self, uid: str, max_pages: int) -> list[dict]:
        """autoglm 完全不可用时的全局回退。"""
        try:
            from src.crawlers.xueqiu.crawler import XueqiuCrawler

            xq_token = self.cookies.get("xq_a_token", "")
            crawler = XueqiuCrawler(
                uid=uid, xq_token=xq_token, rate_limit=1.0
            )
            return crawler.fetch_all_posts(max_pages=max_pages)
        except Exception as e:
            print(f"[TokenCycler] 全局回退失败: {e}")
            return []


# ── 便捷函数 ─────────────────────────────────────────────────────────

def fetch_xueqiu_posts(
    uid: str,
    max_pages: int = 3,
    cookies_path: Optional[str] = None,
) -> list[dict]:
    """便捷函数：爬取雪球用户帖子。"""
    cycler = TokenCycler(cookies_path=cookies_path)
    return cycler.fetch_posts(uid, max_pages=max_pages)


# ── 命令行 ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="雪球 TokenCycler")
    parser.add_argument("uid", nargs="?", default="7251377368", help="用户ID")
    parser.add_argument("--pages", type=int, default=3, help="最大页数")
    parser.add_argument(
        "--cookies",
        default=None,
        help="cookies JSON路径 (默认 data/xueqiu_cookies.json)",
    )
    args = parser.parse_args()

    cycler = TokenCycler(cookies_path=args.cookies)
    print(f"autoglm 可用: {cycler.has_autoglm}")
    print(f"cookies 加载: {len(cycler.cookies)} 个键")
    print(f"开始爬取 uid={args.uid}, max_pages={args.pages}")

    posts = cycler.fetch_posts(args.uid, max_pages=args.pages)
    print(f"\n结果: {len(posts)} 个帖子")
    for i, p in enumerate(posts[:5]):
        print(
            f"  [{i+1}] id={p.get('post_id')} "
            f"title={p.get('title','')[:50]} "
            f"time={p.get('created_at','')}"
        )
