# -*- coding: utf-8 -*-
"""
复利笔记小程序爬虫 — 通过 Fiddler 代理访问微信内部 API
=====================================================
前置条件: Fiddler Everywhere 运行在 127.0.0.1:8866，系统代理已设置

微信小程序「复利笔记」的后端域名 www.fuyinkeji.top 只在微信内部可解析，
公网 DNS 查不到。必须通过 Fiddler 代理（会走微信的 DNS 解析）访问。

用法:
    python src/crawlers/fuli_crawler.py                    # 测试：拉今天的数据
    python src/crawlers/fuli_crawler.py 2026-07-01         # 拉指定日期
    python src/crawlers/fuli_crawler.py 2026-07-01 --all   # 拉全部端点

Windows 用户: 如遇 UnicodeEncodeError，设置环境变量 PYTHONIOENCODING=utf-8
或在命令前加 `python -X utf8`。
"""
import json
import os
import sqlite3
import ssl
import sys
import time
import urllib.request
import urllib.error
import urllib.parse
from datetime import date, datetime, timedelta
from typing import Optional, Dict, List, Any

# Windows 控制台 UTF-8 编码修复
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass


# ============================================================
# 项目路径
# ============================================================
PROJECT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(PROJECT, 'data', 'fuli_notes')
SATOKEN_FILE = os.path.join(DATA_DIR, 'satoken.txt')
DB_PATH = os.path.join(DATA_DIR, 'holdings.db')

# 确保目录存在
os.makedirs(DATA_DIR, exist_ok=True)


# ============================================================
# Fiddler 代理连通性检查
# ============================================================
def check_fiddler_alive(proxy_url: str = 'http://127.0.0.1:8866', timeout: int = 3) -> bool:
    """检查 Fiddler 代理是否可用"""
    try:
        proxy_handler = urllib.request.ProxyHandler({
            'http': proxy_url,
            'https': proxy_url,
        })
        opener = urllib.request.build_opener(proxy_handler)
        req = urllib.request.Request('http://127.0.0.1:8866/', method='GET')
        urllib.request.install_opener(opener)
        with opener.open(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


# ============================================================
# satoken 管理
# ============================================================
def load_satoken(filepath: str = SATOKEN_FILE) -> Optional[str]:
    """
    从本地文件读取 satoken。
    配合 FiddlerScript 自动写入 satoken.txt 时使用。
    """
    if os.path.exists(filepath):
        try:
            token = open(filepath, 'r', encoding='utf-8').read().strip()
            if token:
                return token
        except (IOError, OSError):
            pass
    return None


def save_satoken(token: str, filepath: str = SATOKEN_FILE) -> None:
    """手动保存 satoken 到本地文件"""
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(token)


# ============================================================
# SQLite 初始化
# ============================================================
def init_database(db_path: str = DB_PATH) -> sqlite3.Connection:
    """初始化数据库和表结构"""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    cursor = conn.cursor()

    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS daily_holdings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hold_date TEXT NOT NULL,
            stock_name TEXT,
            stock_code TEXT,
            stock_users INTEGER,
            hold_price TEXT,
            hold_price_num REAL,
            prev_day_users INTEGER,
            user_change INTEGER,
            rank_type TEXT,
            top5_players TEXT,
            raw_json TEXT,
            fetched_at TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(hold_date, stock_code, rank_type)
        );

        CREATE TABLE IF NOT EXISTS daily_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hold_date TEXT NOT NULL,
            game_session TEXT,
            stat_type TEXT NOT NULL,
            data TEXT NOT NULL,
            fetched_at TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(hold_date, game_session, stat_type)
        );

        CREATE TABLE IF NOT EXISTS notebooks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            notebook_id TEXT UNIQUE NOT NULL,
            open_id TEXT,
            title TEXT,
            content TEXT,
            author_name TEXT,
            mark_type TEXT,
            tags TEXT,
            create_time TEXT,
            like_count INTEGER DEFAULT 0,
            comment_count INTEGER DEFAULT 0,
            raw_json TEXT,
            fetched_at TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS weekly_votes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vote_date TEXT NOT NULL,
            game_session TEXT,
            stock_name TEXT,
            stock_code TEXT,
            vote_count INTEGER,
            rank_position INTEGER,
            raw_json TEXT,
            fetched_at TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(vote_date, game_session, stock_code)
        );

        CREATE TABLE IF NOT EXISTS comment_replies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            comment_date TEXT NOT NULL,
            game_session TEXT,
            content TEXT,
            author_name TEXT,
            author_open_id TEXT,
            stock_name TEXT,
            sentiment TEXT,
            raw_json TEXT,
            fetched_at TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS explode_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hold_date TEXT NOT NULL,
            game_session TEXT,
            open_id TEXT,
            player_name TEXT,
            explode_amount REAL,
            stock_name TEXT,
            raw_json TEXT,
            fetched_at TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS mood_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            open_id TEXT NOT NULL,
            mood_type TEXT,
            mood_value REAL,
            record_time TEXT,
            raw_json TEXT,
            fetched_at TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS hot_index (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_name TEXT,
            stock_code TEXT,
            hot_value REAL,
            rank_position INTEGER,
            trend TEXT,
            raw_json TEXT,
            fetched_at TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS crawl_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            endpoint TEXT NOT NULL,
            status TEXT NOT NULL,
            error_msg TEXT,
            response_size INTEGER,
            duration_ms INTEGER,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE INDEX IF NOT EXISTS idx_holdings_date ON daily_holdings(hold_date);
        CREATE INDEX IF NOT EXISTS idx_holdings_stock ON daily_holdings(stock_name);
        CREATE INDEX IF NOT EXISTS idx_holdings_code ON daily_holdings(stock_code);
        CREATE INDEX IF NOT EXISTS idx_stats_date ON daily_stats(hold_date);
        CREATE INDEX IF NOT EXISTS idx_stats_type ON daily_stats(stat_type);
        CREATE INDEX IF NOT EXISTS idx_notebooks_author ON notebooks(open_id);
        CREATE INDEX IF NOT EXISTS idx_notebooks_mark ON notebooks(mark_type);
        CREATE INDEX IF NOT EXISTS idx_votes_date ON weekly_votes(vote_date);
        CREATE INDEX IF NOT EXISTS idx_comments_date ON comment_replies(comment_date);
        CREATE INDEX IF NOT EXISTS idx_explode_date ON explode_orders(hold_date);
        CREATE INDEX IF NOT EXISTS idx_mood_time ON mood_history(record_time);
        CREATE INDEX IF NOT EXISTS idx_hot_index_rank ON hot_index(rank_position);
        CREATE INDEX IF NOT EXISTS idx_crawl_log_time ON crawl_log(created_at);
    """)

    conn.commit()
    return conn


# ============================================================
# 主爬虫类
# ============================================================
class FuliCrawler:
    """
    复利笔记小程序爬虫

    通过 Fiddler 代理访问微信小程序后端 API（域名仅在微信内解析）。
    前置条件:
        1. Fiddler Everywhere 运行在 127.0.0.1:8866
        2. 系统代理已设置（或手动在代码中配置）
        3. 微信小程序已打开，satoken 有效
    """

    BASE_URL = 'https://www.fuyinkeji.top'
    FIDDLER_PROXY = 'http://127.0.0.1:8866'

    # 微信小程序 User-Agent（模拟微信环境）
    UA = (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/107.0.0.0 Safari/537.36 '
        'MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI MiniProgramEnv/Windows '
        'WindowsWechat/WMPF XWEB/8557'
    )

    REFERER = 'https://servicewechat.com/wx8f3e0e45d8b7a8b6/0/page-frame.html'

    def __init__(
        self,
        satoken: Optional[str] = None,
        proxy_url: Optional[str] = None,
        db_path: Optional[str] = None,
        timeout: int = 15,
        retries: int = 2,
    ):
        """
        Args:
            satoken: 微信小程序 session token。None 则自动从 satoken.txt 读取。
            proxy_url: Fiddler 代理地址。None 则用默认 127.0.0.1:8866。
            db_path: SQLite 数据库路径。None 则用默认路径。
            timeout: 请求超时（秒）
            retries: 失败重试次数
        """
        self.proxy_url = proxy_url or self.FIDDLER_PROXY
        self.db_path = db_path or DB_PATH
        self.timeout = timeout
        self.retries = retries

        # satoken: 参数优先 → 文件读取 → None
        self.satoken = satoken or load_satoken()
        if not self.satoken:
            print("[WARN] 未提供 satoken，将从 satoken.txt 读取。"
                  "请确保微信小程序已打开，且 FiddlerScript 已配置自动提取。")

        # 配置代理
        self._setup_proxy()

        # 初始化数据库
        self.conn = init_database(self.db_path)

        # 配置 SSL（Fiddler 会做中间人解密，需要信任其根证书）
        self._setup_ssl()

    # ----- 代理配置 -----

    def _setup_proxy(self):
        """配置 urllib 使用 Fiddler 代理"""
        proxy_handler = urllib.request.ProxyHandler({
            'http': self.proxy_url,
            'https': self.proxy_url,
        })
        self.opener = urllib.request.build_opener(proxy_handler)
        urllib.request.install_opener(self.opener)

    def _setup_ssl(self):
        """创建不验证证书的 SSL 上下文（Fiddler 中间人证书）"""
        self.ssl_context = ssl.create_default_context()
        self.ssl_context.check_hostname = False
        self.ssl_context.verify_mode = ssl.CERT_NONE

    # ----- 核心请求方法 -----

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """GET 请求，带认证头"""
        url = f"{self.BASE_URL}{path}"
        if params:
            cleaned = {k: v for k, v in params.items() if v is not None and v != ''}
            if cleaned:
                url += '?' + urllib.parse.urlencode(cleaned)

        headers = {
            'User-Agent': self.UA,
            'Referer': self.REFERER,
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Cache-Control': 'no-cache',
        }
        if self.satoken:
            headers['satoken'] = self.satoken

        return self._request(url, headers, method='GET')

    def _post(self, path: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """POST 请求，带认证头"""
        url = f"{self.BASE_URL}{path}"
        headers = {
            'User-Agent': self.UA,
            'Referer': self.REFERER,
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Content-Type': 'application/json;charset=UTF-8',
            'Connection': 'keep-alive',
            'Cache-Control': 'no-cache',
        }
        if self.satoken:
            headers['satoken'] = self.satoken

        body = json.dumps(data or {}).encode('utf-8')
        headers['Content-Length'] = str(len(body))
        return self._request(url, headers, method='POST', body=body)

    def _request(
        self, url: str, headers: Dict[str, str],
        method: str = 'GET', body: Optional[bytes] = None,
    ) -> Dict[str, Any]:
        """底层 HTTP 请求，带重试和日志"""
        last_error = None
        for attempt in range(self.retries + 1):
            t0 = time.time()
            try:
                req = urllib.request.Request(url, headers=headers, method=method, data=body)
                with urllib.request.urlopen(req, timeout=self.timeout, context=self.ssl_context) as resp:
                    raw = resp.read()
                    elapsed_ms = int((time.time() - t0) * 1000)
                    self._log_request(url, 'ok', len(raw), elapsed_ms)
                    return json.loads(raw.decode('utf-8'))

            except urllib.error.HTTPError as e:
                elapsed_ms = int((time.time() - t0) * 1000)
                body_text = ''
                try:
                    body_text = e.read().decode('utf-8', errors='replace')[:500]
                except Exception:
                    pass
                last_error = f"HTTP {e.code}: {e.reason} | {body_text}"
                self._log_request(url, f'error_http_{e.code}', 0, elapsed_ms, last_error)

                if e.code == 401:
                    print(f"[AUTH FAIL] satoken 可能已过期 (HTTP 401)。"
                          f"请重新打开微信小程序，确保 FiddlerScript 自动更新 satoken.txt")
                    break  # 401 不重试
                if e.code in (429, 503):
                    wait = 2 ** attempt
                    print(f"[RATE LIMITED] {url} — 等待 {wait}s 后重试...")
                    time.sleep(wait)
                    continue

            except (urllib.error.URLError, OSError, ConnectionError) as e:
                elapsed_ms = int((time.time() - t0) * 1000)
                last_error = str(e)
                self._log_request(url, 'error_network', 0, elapsed_ms, last_error)

                # 判断是否是代理不可达
                err_str = str(e).lower()
                if 'connection refused' in err_str or 'cannot connect' in err_str:
                    print(f"[FIDDLER DOWN] 无法连接到代理 {self.proxy_url}。"
                          f"请确保 Fiddler Everywhere 正在运行。")
                    break

                if attempt < self.retries:
                    wait = 2 ** attempt
                    print(f"[RETRY {attempt+1}/{self.retries}] {url} — 等待 {wait}s...")
                    time.sleep(wait)
                    continue

            except json.JSONDecodeError as e:
                elapsed_ms = int((time.time() - t0) * 1000)
                last_error = f"JSON decode: {e}"
                self._log_request(url, 'error_json', 0, elapsed_ms, last_error)
                break

            except Exception as e:
                elapsed_ms = int((time.time() - t0) * 1000)
                last_error = str(e)
                self._log_request(url, 'error_unknown', 0, elapsed_ms, last_error)
                if attempt < self.retries:
                    time.sleep(2 ** attempt)
                    continue

        # 所有重试失败
        print(f"[FAIL] {url}: {last_error}")
        return {'code': -1, 'msg': str(last_error), 'data': None}

    def _log_request(
        self, url: str, status: str, size: int, duration_ms: int,
        error_msg: Optional[str] = None,
    ):
        """写入请求日志到 crawl_log 表"""
        try:
            path = urllib.parse.urlparse(url).path
            self.conn.execute(
                "INSERT INTO crawl_log (endpoint, status, error_msg, response_size, duration_ms) "
                "VALUES (?, ?, ?, ?, ?)",
                (path, status, error_msg, size, duration_ms),
            )
            self.conn.commit()
        except Exception:
            pass

    # ----- 数据持久化 -----

    def _upsert_holdings(self, hold_date: str, holdings: List[Dict], rank_type: str = ''):
        """写入持仓排行数据"""
        for h in holdings:
            try:
                self.conn.execute("""
                    INSERT INTO daily_holdings
                        (hold_date, stock_name, stock_code, stock_users, hold_price,
                         hold_price_num, prev_day_users, user_change, rank_type,
                         top5_players, raw_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(hold_date, stock_code, rank_type) DO UPDATE SET
                        stock_users=excluded.stock_users,
                        hold_price=excluded.hold_price,
                        hold_price_num=excluded.hold_price_num,
                        user_change=excluded.user_change,
                        top5_players=excluded.top5_players,
                        raw_json=excluded.raw_json,
                        fetched_at=datetime('now','localtime')
                """, (
                    hold_date,
                    h.get('stockName') or h.get('stock_name', ''),
                    h.get('stockCode') or h.get('stock_code', ''),
                    h.get('holdUsers') or h.get('stock_users', 0),
                    str(h.get('holdPrice') or h.get('hold_price', '')),
                    float(h.get('holdPriceNum') or h.get('hold_price_num', 0) or 0),
                    h.get('prevDayUsers') or h.get('prev_day_users', 0),
                    h.get('userChange') or h.get('user_change', 0),
                    rank_type,
                    json.dumps(h.get('top5Players') or h.get('top5_players', []), ensure_ascii=False),
                    json.dumps(h, ensure_ascii=False),
                ))
            except Exception as e:
                print(f"  [DB WARN] 写入持仓失败: {h.get('stockName', '?')} — {e}")
        self.conn.commit()

    def _upsert_stats(self, hold_date: str, stat_type: str, data: Any, game_session: str = ''):
        """写入统计数据"""
        try:
            self.conn.execute("""
                INSERT INTO daily_stats (hold_date, game_session, stat_type, data)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(hold_date, game_session, stat_type) DO UPDATE SET
                    data=excluded.data,
                    fetched_at=datetime('now','localtime')
            """, (hold_date, game_session or '', stat_type, json.dumps(data, ensure_ascii=False)))
            self.conn.commit()
        except Exception as e:
            print(f"  [DB WARN] 写入统计失败: {stat_type} — {e}")

    def _upsert_notebooks(self, notebooks: List[Dict]):
        """写入精华笔记"""
        for n in notebooks:
            try:
                nid = str(n.get('id') or n.get('notebookId', ''))
                self.conn.execute("""
                    INSERT INTO notebooks
                        (notebook_id, open_id, title, content, author_name, mark_type,
                         tags, create_time, like_count, comment_count, raw_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(notebook_id) DO UPDATE SET
                        title=excluded.title,
                        content=excluded.content,
                        like_count=excluded.like_count,
                        comment_count=excluded.comment_count,
                        raw_json=excluded.raw_json,
                        fetched_at=datetime('now','localtime')
                """, (
                    nid,
                    n.get('openId') or n.get('open_id', ''),
                    n.get('title', ''),
                    n.get('content', ''),
                    n.get('authorName') or n.get('author_name', ''),
                    n.get('markType') or n.get('mark_type', ''),
                    json.dumps(n.get('tags', []), ensure_ascii=False),
                    n.get('createTime') or n.get('create_time', ''),
                    n.get('likeCount') or n.get('like_count', 0),
                    n.get('commentCount') or n.get('comment_count', 0),
                    json.dumps(n, ensure_ascii=False),
                ))
            except Exception as e:
                print(f"  [DB WARN] 写入笔记失败: {n.get('title', '?')} — {e}")
        self.conn.commit()

    def _upsert_votes(self, vote_date: str, votes: List[Dict], game_session: str = ''):
        """写入每周投票"""
        for v in votes:
            try:
                self.conn.execute("""
                    INSERT INTO weekly_votes
                        (vote_date, game_session, stock_name, stock_code,
                         vote_count, rank_position, raw_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(vote_date, game_session, stock_code) DO UPDATE SET
                        vote_count=excluded.vote_count,
                        rank_position=excluded.rank_position,
                        raw_json=excluded.raw_json,
                        fetched_at=datetime('now','localtime')
                """, (
                    vote_date,
                    game_session or '',
                    v.get('stockName') or v.get('stock_name', ''),
                    v.get('stockCode') or v.get('stock_code', ''),
                    v.get('voteCount') or v.get('vote_count', 0),
                    v.get('rankPosition') or v.get('rank_position', 0),
                    json.dumps(v, ensure_ascii=False),
                ))
            except Exception as e:
                print(f"  [DB WARN] 写入投票失败: {v.get('stockName', '?')} — {e}")
        self.conn.commit()

    def _upsert_explode_orders(self, hold_date: str, orders: List[Dict], game_session: str = ''):
        """写入爆仓排名"""
        for o in orders:
            try:
                self.conn.execute("""
                    INSERT INTO explode_orders
                        (hold_date, game_session, open_id, player_name,
                         explode_amount, stock_name, raw_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    hold_date,
                    game_session or '',
                    o.get('openId') or o.get('open_id', ''),
                    o.get('playerName') or o.get('player_name', ''),
                    o.get('explodeAmount') or o.get('explode_amount', 0),
                    o.get('stockName') or o.get('stock_name', ''),
                    json.dumps(o, ensure_ascii=False),
                ))
            except Exception as e:
                print(f"  [DB WARN] 写入爆仓失败: {o.get('playerName', '?')} — {e}")
        self.conn.commit()

    # ----- 业务 API 方法 -----

    def fetch_holdings(
        self, hold_date: str, rank_type: str = '', open_id: str = '',
    ) -> List[Dict]:
        """
        拉取日持仓排行榜

        Args:
            hold_date: 日期 YYYY-MM-DD
            rank_type: 排行类型（空=全部）
            open_id: 按用户筛选（可选）

        Returns:
            List[Dict]: 持仓记录列表
        """
        path = '/hold/selectHoldHoldStocksByType' if rank_type else '/hold/selectHoldHoldStocks'
        params = {'holdDate': hold_date}
        if rank_type:
            params['rankType'] = rank_type
        if open_id:
            params['openId'] = open_id

        print(f"[FETCH] 持仓排行: {hold_date}" + (f" (type={rank_type})" if rank_type else ""))
        resp = self._get(path, params)

        records = self._extract_list(resp)
        if records:
            self._upsert_holdings(hold_date, records, rank_type)
            print(f"  ✅ {len(records)} 条持仓记录")
        else:
            print(f"  ⚠️  无持仓数据或 API 返回异常: {resp.get('msg', 'unknown')}")
        return records

    def fetch_stats(
        self, game_session: str, hold_date: str = '',
    ) -> Dict[str, Any]:
        """
        拉取综合统计 + 历史趋势

        Returns:
            {'comprehensive': ..., 'trend': ...}
        """
        result = {}

        # 综合统计
        print(f"[FETCH] 综合统计: session={game_session}, date={hold_date}")
        resp = self._get('/platformStats/getComprehensiveStats', {
            'gameSession': game_session,
            'holdDate': hold_date,
        })
        comp_data = resp.get('data')
        if comp_data:
            self._upsert_stats(hold_date or str(date.today()), 'comprehensive', comp_data, game_session)
            print(f"  ✅ 综合统计已保存")
        else:
            print(f"  ⚠️  综合统计无数据: {resp.get('msg', 'unknown')}")
        result['comprehensive'] = comp_data

        # 历史趋势
        print(f"[FETCH] 历史趋势: session={game_session}")
        resp = self._get('/platformStats/getHistoricalTrend', {
            'gameSession': game_session,
        })
        trend_data = resp.get('data')
        if trend_data:
            self._upsert_stats(hold_date or str(date.today()), 'trend', trend_data, game_session)
            print(f"  ✅ 历史趋势已保存")
        else:
            print(f"  ⚠️  历史趋势无数据: {resp.get('msg', 'unknown')}")
        result['trend'] = trend_data

        return result

    def fetch_hot_index(self) -> List[Dict]:
        """拉取热门指数"""
        print("[FETCH] 热门指数")
        resp = self._get('/hotIndex/overview')
        records = self._extract_list(resp)
        if records:
            # 先清空旧数据再写入（热门指数通常全量替换）
            self.conn.execute("DELETE FROM hot_index")
            for r in records:
                try:
                    self.conn.execute("""
                        INSERT INTO hot_index
                            (stock_name, stock_code, hot_value, rank_position, trend, raw_json)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (
                        r.get('stockName') or r.get('stock_name', ''),
                        r.get('stockCode') or r.get('stock_code', ''),
                        r.get('hotValue') or r.get('hot_value', 0),
                        r.get('rankPosition') or r.get('rank_position', 0),
                        r.get('trend', ''),
                        json.dumps(r, ensure_ascii=False),
                    ))
                except Exception:
                    pass
            self.conn.commit()
            print(f"  ✅ {len(records)} 条热门指数")
        else:
            print(f"  ⚠️  热门指数无数据: {resp.get('msg', 'unknown')}")
        return records

    def fetch_notebooks(
        self, open_id: str = '', page_num: int = 1, page_size: int = 20,
    ) -> List[Dict]:
        """
        拉取精华笔记

        Args:
            open_id: 作者 openId（空=全部精华）
            page_num: 页码
            page_size: 每页条数
        """
        print(f"[FETCH] 精华笔记: page={page_num}, size={page_size}")
        resp = self._get('/notebook/pageNotebookByMark', {
            'openId': open_id,
            'pageNum': page_num,
            'pageSize': page_size,
        })
        records = self._extract_list(resp)
        if records:
            self._upsert_notebooks(records)
            print(f"  ✅ {len(records)} 条笔记")
        else:
            print(f"  ⚠️  无笔记数据: {resp.get('msg', 'unknown')}")
        return records

    def fetch_notebook_detail(self, notebook_id: str) -> Dict:
        """拉取笔记详情"""
        print(f"[FETCH] 笔记详情: id={notebook_id}")
        resp = self._get('/notebook/getNotebookInfoById', {'id': notebook_id})
        detail = resp.get('data')
        if detail:
            self._upsert_notebooks([detail])
            print(f"  ✅ 笔记详情: {detail.get('title', notebook_id)}")
        else:
            print(f"  ⚠️  笔记详情无数据: {resp.get('msg', 'unknown')}")
        return detail or {}

    def fetch_votes(
        self, game_session: str, vote_date: str, current_user_open_id: str = '',
    ) -> List[Dict]:
        """
        拉取每周投票排行

        Args:
            game_session: 游戏赛季
            vote_date: 投票日期
            current_user_open_id: 当前用户 openId（可选）
        """
        print(f"[FETCH] 每周投票: date={vote_date}, session={game_session}")
        resp = self._get('/weeklyVote/rank', {
            'currentUserOpenId': current_user_open_id,
            'gameSession': game_session,
            'date': vote_date,
        })
        records = self._extract_list(resp)
        if records:
            self._upsert_votes(vote_date, records, game_session)
            print(f"  ✅ {len(records)} 条投票")
        else:
            print(f"  ⚠️  无投票数据: {resp.get('msg', 'unknown')}")
        return records

    def fetch_comments(
        self, game_session: str, comment_date: str, page_num: int = 1, page_size: int = 20,
    ) -> List[Dict]:
        """拉取评论"""
        print(f"[FETCH] 评论: date={comment_date}, page={page_num}")
        resp = self._get('/commentReply/page', {
            'gameSession': game_session,
            'commentDate': comment_date,
            'pageNum': page_num,
            'pageSize': page_size,
        })
        records = self._extract_list(resp)
        if records:
            for c in records:
                try:
                    self.conn.execute("""
                        INSERT INTO comment_replies
                            (comment_date, game_session, content, author_name,
                             author_open_id, stock_name, sentiment, raw_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        comment_date,
                        game_session or '',
                        c.get('content', ''),
                        c.get('authorName') or c.get('author_name', ''),
                        c.get('authorOpenId') or c.get('author_open_id', ''),
                        c.get('stockName') or c.get('stock_name', ''),
                        c.get('sentiment', ''),
                        json.dumps(c, ensure_ascii=False),
                    ))
                except Exception:
                    pass
            self.conn.commit()
            print(f"  ✅ {len(records)} 条评论")
        else:
            print(f"  ⚠️  无评论数据: {resp.get('msg', 'unknown')}")
        return records

    def fetch_explode_orders(
        self, hold_date: str, game_session: str = '',
    ) -> List[Dict]:
        """拉取爆仓排名"""
        print(f"[FETCH] 爆仓排名: date={hold_date}, session={game_session}")
        resp = self._get('/sys/umsExplodeOrder/rankList', {
            'holdDate': hold_date,
            'gameSession': game_session,
        })
        records = self._extract_list(resp)
        if records:
            self._upsert_explode_orders(hold_date, records, game_session)
            print(f"  ✅ {len(records)} 条爆仓记录")
        else:
            print(f"  ⚠️  无爆仓数据: {resp.get('msg', 'unknown')}")
        return records

    def fetch_mood_history(self, open_id: str) -> List[Dict]:
        """拉取最近 24 小时情绪历史"""
        print(f"[FETCH] 情绪历史: openId={open_id}")
        resp = self._get('/sys/umsMoodHistory/getLatestMoodInLast24Hours', {
            'openId': open_id,
        })
        records = self._extract_list(resp)
        if records:
            for m in records:
                try:
                    self.conn.execute("""
                        INSERT INTO mood_history
                            (open_id, mood_type, mood_value, record_time, raw_json)
                        VALUES (?, ?, ?, ?, ?)
                    """, (
                        open_id,
                        m.get('moodType') or m.get('mood_type', ''),
                        m.get('moodValue') or m.get('mood_value', 0),
                        m.get('recordTime') or m.get('record_time', ''),
                        json.dumps(m, ensure_ascii=False),
                    ))
                except Exception:
                    pass
            self.conn.commit()
            print(f"  ✅ {len(records)} 条情绪记录")
        else:
            print(f"  ⚠️  无情绪数据: {resp.get('msg', 'unknown')}")
        return records

    def fetch_game_config(self) -> Dict:
        """拉取游戏配置（当前赛季信息）"""
        print("[FETCH] 游戏配置")
        resp = self._post('/gameConfig/getUmsGameConfigListByCurrentSession')
        config = resp.get('data')
        if config:
            print(f"  ✅ 游戏配置已获取")
        else:
            print(f"  ⚠️  游戏配置无数据: {resp.get('msg', 'unknown')}")
        return config or {}

    # ----- 综合拉取 -----

    def fetch_all_daily(
        self, hold_date: str, game_session: str = '', open_id: str = '',
    ) -> Dict[str, Any]:
        """
        一键拉取当天全部数据

        Returns:
            Dict 包含所有拉取结果
        """
        print(f"\n{'='*60}")
        print(f"  复利笔记 — 全量数据拉取: {hold_date}")
        print(f"{'='*60}")

        results = {}

        # 1. 游戏配置（获取当前赛季）
        config = self.fetch_game_config()
        results['game_config'] = config

        # 从配置中提取 gameSession
        if not game_session and config:
            if isinstance(config, dict):
                game_session = config.get('gameSession') or config.get('game_session', '')
            elif isinstance(config, list) and len(config) > 0:
                gs = config[0]
                game_session = gs.get('gameSession') or gs.get('game_session', '')

        # 2. 综合统计 + 历史趋势
        if game_session:
            results['stats'] = self.fetch_stats(game_session, hold_date)
        else:
            print("[SKIP] 综合统计 — 无 gameSession")

        # 3. 持仓排行
        results['holdings'] = self.fetch_holdings(hold_date)

        # 4. 热门指数
        results['hot_index'] = self.fetch_hot_index()

        # 5. 爆仓排名
        results['explode_orders'] = self.fetch_explode_orders(hold_date, game_session)

        # 6. 精华笔记（第一页）
        results['notebooks'] = self.fetch_notebooks('', 1, 20)

        # 7. 每周投票
        results['votes'] = self.fetch_votes(game_session, hold_date)

        # 8. 评论（第一页）
        results['comments'] = self.fetch_comments(game_session, hold_date, 1, 20)

        # 汇总
        print(f"\n{'='*60}")
        print(f"  拉取完成")
        total = sum(
            len(v) if isinstance(v, list) else 1 if v else 0
            for v in results.values()
        )
        print(f"  总计: {total} 条数据")
        print(f"{'='*60}\n")

        return results

    # ----- 工具方法 -----

    def _extract_list(self, resp: Dict) -> List[Dict]:
        """
        从 API 响应中提取列表数据。
        兼容多种返回格式:
        - resp['data'] 是 list
        - resp['data']['records'] 是 list (分页)
        - resp['data']['list'] 是 list
        """
        data = resp.get('data')
        if data is None:
            return []

        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            # 分页格式
            records = data.get('records')
            if isinstance(records, list):
                return records
            # list 格式
            lst = data.get('list')
            if isinstance(lst, list):
                return lst
            # 单条结果
            if data:
                return [data]
        return []

    def get_game_session(self) -> str:
        """获取当前游戏赛季"""
        config = self.fetch_game_config()
        if isinstance(config, dict):
            return config.get('gameSession') or config.get('game_session', '')
        if isinstance(config, list) and len(config) > 0:
            return config[0].get('gameSession') or config[0].get('game_session', '')
        return ''

    def close(self):
        """关闭数据库连接"""
        if self.conn:
            self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


# ============================================================
# 命令行入口
# ============================================================
def main():
    import argparse

    parser = argparse.ArgumentParser(
        description='复利笔记小程序爬虫 — 通过 Fiddler 代理拉取数据',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python fuli_crawler.py                              # 拉今天的数据
  python fuli_crawler.py 2026-07-01                   # 拉指定日期
  python fuli_crawler.py 2026-07-01 --all             # 拉全部端点
  python fuli_crawler.py --check-fiddler              # 检查 Fiddler 状态
  python fuli_crawler.py --init-db                    # 仅初始化数据库

前置条件:
  1. Fiddler Everywhere 运行在 127.0.0.1:8866
  2. 微信小程序「复利笔记」正在运行
  3. satoken 已通过 FiddlerScript 自动写入 satoken.txt
        """,
    )
    parser.add_argument('date', nargs='?', default=None,
                        help='拉取日期 (YYYY-MM-DD)，默认今天')
    parser.add_argument('--all', action='store_true',
                        help='拉取全部端点')
    parser.add_argument('--satoken', '-t', default=None,
                        help='手动提供 satoken (优先于文件读取)')
    parser.add_argument('--proxy', '-p', default=None,
                        help='代理地址 (默认 http://127.0.0.1:8866)')
    parser.add_argument('--game-session', '-s', default='',
                        help='游戏赛季 ID')
    parser.add_argument('--open-id', '-o', default='',
                        help='用户 openId')
    parser.add_argument('--check-fiddler', action='store_true',
                        help='检查 Fiddler 代理连通性')
    parser.add_argument('--init-db', action='store_true',
                        help='仅初始化数据库')
    parser.add_argument('--save-satoken', default=None,
                        help='保存 satoken 到本地文件')

    args = parser.parse_args()

    # 保存 satoken
    if args.save_satoken:
        save_satoken(args.save_satoken)
        print(f"✅ satoken 已保存到 {SATOKEN_FILE}")
        return

    # 仅初始化数据库
    if args.init_db:
        conn = init_database()
        conn.close()
        print(f"✅ 数据库已初始化: {DB_PATH}")
        return

    # 检查 Fiddler
    if args.check_fiddler:
        proxy = args.proxy or 'http://127.0.0.1:8866'
        alive = check_fiddler_alive(proxy)
        if alive:
            print(f"✅ Fiddler 代理可用: {proxy}")
        else:
            print(f"❌ Fiddler 代理不可达: {proxy}")
            print("   请检查:")
            print("   1. Fiddler Everywhere 是否正在运行")
            print("   2. 代理端口是否为 8866")
            print("   3. 防火墙是否阻止连接")
        return

    # 拉取数据
    hold_date = args.date or date.today().isoformat()

    print(f"复利笔记爬虫启动")
    print(f"  日期: {hold_date}")
    print(f"  代理: {args.proxy or 'http://127.0.0.1:8866'}")
    print(f"  satoken: {'手动提供' if args.satoken else '自动读取 satoken.txt'}")
    print()

    # 先检查 Fiddler
    if not check_fiddler_alive(args.proxy or 'http://127.0.0.1:8866'):
        print("❌ Fiddler 代理不可用！请启动 Fiddler Everywhere 后重试。")
        sys.exit(1)

    with FuliCrawler(
        satoken=args.satoken,
        proxy_url=args.proxy,
    ) as crawler:
        if not crawler.satoken:
            print("❌ 未找到 satoken！请:")
            print("   1. 打开微信小程序「复利笔记」")
            print("   2. 确保 FiddlerScript 已配置自动提取")
            print("   3. 或手动传入: --satoken <your_token>")
            sys.exit(1)

        if args.all:
            game_session = args.game_session or crawler.get_game_session()
            crawler.fetch_all_daily(hold_date, game_session, args.open_id)
        else:
            # 默认：持仓排行 + 热门指数
            game_session = args.game_session or crawler.get_game_session()
            crawler.fetch_holdings(hold_date)
            crawler.fetch_hot_index()
            if game_session:
                crawler.fetch_stats(game_session, hold_date)
                crawler.fetch_explode_orders(hold_date, game_session)
            crawler.fetch_notebooks('', 1, 20)


if __name__ == '__main__':
    main()
