# -*- coding: utf-8 -*-
"""
心跳驱动的爬取脚本 — 每次心跳轮询时调用。
检查上次爬取时间，如果超过阈值则触发爬取。

设计原则:
- 三个平台独立执行，各自保存状态，单平台失败不阻塞其他
- 雪球: open_link 直接渲染（无 cookie / 无 token / 无浏览器）
- 知乎: ZhiHuCrawler（API）
- 知识星球: ZsxqScanner（API）
- 交易日30min阈值，盘后60min阈值
"""

import json, os, sys, io, time as _time
from datetime import datetime, timezone, timedelta

# 系统 Python 路径（含 requests/pandas/jieba 等依赖）
# AutoClaw 内置 Python 缺少这些包，必须使用系统 Python
PYTHON_EXE = r'C:\Users\11\AppData\Local\Python\pythoncore-3.14-64\python.exe'

# 强制 UTF-8 输出，解决 Windows GBK 编码问题
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
elif sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

TZ = timezone(timedelta(hours=8))
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HEARTBEAT_STATE = os.path.join(PROJECT_ROOT, '..', 'memory', 'heartbeat-state.json')

# 确保项目根在 path 中
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def load_heartbeat_state():
    if os.path.exists(HEARTBEAT_STATE):
        with open(HEARTBEAT_STATE, encoding='utf-8') as f:
            return json.load(f)
    return {}


def save_heartbeat_state(state):
    os.makedirs(os.path.dirname(HEARTBEAT_STATE), exist_ok=True)
    with open(HEARTBEAT_STATE, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def should_crawl(platform, threshold_minutes=30):
    state = load_heartbeat_state()
    last_ts = state.get('last_crawl', {}).get(platform, 0)
    now = int(datetime.now(TZ).timestamp() * 1000)
    return now - last_ts > threshold_minutes * 60 * 1000


def mark_crawled(platform):
    state = load_heartbeat_state()
    state.setdefault('last_crawl', {})
    state['last_crawl'][platform] = int(datetime.now(TZ).timestamp() * 1000)
    save_heartbeat_state(state)



RETRY_INTERVAL_SEC = 300       # 5min retry interval
MAX_RETRIES = 2               # max 2 retries (3 total attempts)
ALERT_FLAG_FILE = os.path.join(PROJECT_ROOT, 'data', 'crawl_alert.txt')
TOKEN_EXPIRED_FILE = os.path.join(PROJECT_ROOT, 'data', 'token_expired_alert.txt')


def _get_failure_tracker():
    state = load_heartbeat_state()
    state.setdefault('failure_tracker', {})
    return state


def _save_failure_tracker(state):
    save_heartbeat_state(state)


def _record_failure(platform, error_msg):
    state = _get_failure_tracker()
    tracker = state['failure_tracker'].setdefault(platform, {
        'failures': 0, 'first_fail_ts': 0, 'last_error': ''
    })
    now = int(datetime.now(TZ).timestamp() * 1000)
    if tracker['failures'] == 0:
        tracker['first_fail_ts'] = now
    tracker['failures'] += 1
    tracker['last_error'] = error_msg[:300]
    _save_failure_tracker(state)
    return tracker['failures']


def _clear_failure(platform):
    state = _get_failure_tracker()
    if platform in state.get('failure_tracker', {}):
        del state['failure_tracker'][platform]
        _save_failure_tracker(state)


def _should_retry(platform):
    state = _get_failure_tracker()
    tracker = state.get('failure_tracker', {}).get(platform, {})
    failures = tracker.get('failures', 0)
    if failures == 0 or failures > MAX_RETRIES:
        return False
    first_fail = tracker.get('first_fail_ts', 0)
    now = int(datetime.now(TZ).timestamp() * 1000)
    elapsed = (now - first_fail) / 1000
    return elapsed >= failures * RETRY_INTERVAL_SEC


def _check_and_alert():
    state = _get_failure_tracker()
    alerts = []
    token_expired = []
    for platform, tracker in state.get('failure_tracker', {}).items():
        failures = tracker.get('failures', 0)
        last_error = tracker.get('last_error', '')
        if failures > MAX_RETRIES:
            is_token = any(kw in last_error.lower() for kw in [
                'token', 'cookie', '401', 'unauthorized', 'auth', 'expired', 'login'
            ])
            if is_token:
                token_expired.append({'platform': platform, 'error': last_error})
            else:
                alerts.append({'platform': platform, 'error': last_error})
    flag_dir = os.path.join(PROJECT_ROOT, 'data')
    os.makedirs(flag_dir, exist_ok=True)
    if token_expired:
        lines = ['[ALERT] TOKEN/COOKIE EXPIRED - UPDATE REQUIRED']
        lines.append(datetime.now(TZ).isoformat())
        for a in token_expired:
            lines.append('Platform ' + a['platform'] + ': ' + a['error'])
        with open(TOKEN_EXPIRED_FILE, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
        print()
        print('=' * 60)
        print('TOKEN / COOKIE EXPIRED - NEEDS UPDATE')
        for a in token_expired:
            print('  Platform: ' + a['platform'])
            print('  Error: ' + a['error'])
        print('=> Please update cookies/tokens immediately')
        print('=' * 60)
        print()
    if alerts:
        lines = ['[ALERT] CRAWL FAILURE - ' + str(len(alerts)) + ' platforms']
        lines.append(datetime.now(TZ).isoformat())
        for a in alerts:
            lines.append('Platform ' + a['platform'] + ': ' + a['error'])
        lines.append('All retries exhausted.')
        with open(ALERT_FLAG_FILE, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
        print()
        print('=' * 60)
        print('CRAWL FAILURE - All retries exhausted')
        for a in alerts:
            print('  Platform: ' + a['platform'] + ' -> ' + a['error'])
        print('=' * 60)
        print()
    return {'alerts': alerts, 'token_expired': token_expired}


def _recently_ran(state_key, minutes=60):
    state = load_heartbeat_state()
    last_ts = state.get(state_key, 0)
    now = int(datetime.now(TZ).timestamp() * 1000)
    return (now - last_ts) < minutes * 60 * 1000


# ═══════════════════════════════════════════════════════════════
# 知乎爬取
# ═══════════════════════════════════════════════════════════════

def crawl_zhihu():
    """爬取知乎博主。使用 ZhiHuCrawler (API)。"""
    from src.crawlers.zhihu.crawler import ZhiHuCrawler

    try:
        crawler = ZhiHuCrawler(url_slug='xiao-peng-61-47')
        posts = crawler.fetch_all(full_content=True, limit=20)
        if posts:
            crawler.save_posts(posts)
            print(f"  [zhihu]: {len(posts)} posts saved")
            return len(posts)
        else:
            print(f"  [zhihu]: 0 posts (empty)")
            return 0
    except Exception as e:
        print(f"  [zhihu]: ERROR - {e}")
        return -1


# ═══════════════════════════════════════════════════════════════
# 雪球爬取（open_link 直连，不依赖 token/autoglm/cookie）
# ═══════════════════════════════════════════════════════════════

def crawl_xueqiu():
    """
    爬取雪球博主。两段式：open_link 优先 → 失败则标记需 Chrome。
    open_link：不依赖 cookie/token，单页 ~20 帖。
    Chrome：在用户在线时手动触发，绕过 IP 限流。
    """
    from src.crawlers.xueqiu.crawler import XueqiuCrawler
    from src.utils.open_link import fetch_page
    from src.crawlers.xueqiu.parser import parse_openlink_text
    import time

    results = {}
    blocked_uids = []
    for uid in ['7251377368', '1034624503']:
        for attempt in range(1, 4):
            try:
                text = fetch_page(f'https://xueqiu.com/u/{uid}', timeout=30)
                posts = parse_openlink_text(text)
                if posts:
                    c = XueqiuCrawler(uid=uid)
                    c.save_posts(posts)
                    results[uid] = len(posts)
                    print(f"  [xueqiu/open_link] {uid}: {len(posts)} posts (attempt {attempt})")
                    break
                elif len(text) < 500:
                    # Short response = IP blocked
                    print(f"  [xueqiu/open_link] {uid}: IP blocked ({len(text)} chars, attempt {attempt})")
                    if attempt < 3:
                        time.sleep(5)  # longer wait for rate limit
                else:
                    print(f"  [xueqiu/open_link] {uid}: 0 posts, text={len(text)} chars, parser may need update (attempt {attempt})")
                    if attempt < 3:
                        time.sleep(2)
            except Exception as e:
                print(f"  [xueqiu/open_link] {uid}: ERROR - {e} (attempt {attempt})")
                if attempt < 3:
                    time.sleep(3)
        else:
            results[uid] = 0
            blocked_uids.append(uid)
            print(f"  [xueqiu/open_link] {uid}: all retries exhausted")

    if blocked_uids:
        warning = f"[xueqiu] open_link blocked for: {', '.join(blocked_uids)} — needs Chrome browser agent"
        print(warning)
        # Write flag file for main session to pick up
        flag_dir = os.path.join(PROJECT_ROOT, 'data')
        os.makedirs(flag_dir, exist_ok=True)
        with open(os.path.join(flag_dir, 'xueqiu_needs_chrome.txt'), 'w') as f:
            f.write(f"{datetime.now(TZ).isoformat()}\n{','.join(blocked_uids)}")

    return results


# ═══════════════════════════════════════════════════════════════
# 复利笔记爬取（Fiddler 代理 + 微信小程序）
# ═══════════════════════════════════════════════════════════════

def _check_fiddler():
    """检查 Fiddler 代理是否可用"""
    try:
        import urllib.request
        proxy = urllib.request.ProxyHandler({'http': 'http://127.0.0.1:8866'})
        opener = urllib.request.build_opener(proxy)
        urllib.request.install_opener(opener)
        req = urllib.request.Request('http://127.0.0.1:8866/', method='GET')
        urllib.request.urlopen(req, timeout=3)
        return True
    except:
        return False


def crawl_fuli():
    """爬取复利笔记小程序数据（需要 Fiddler 代理 + 微信小程序运行）"""
    from src.crawlers.fuli_crawler import FuliCrawler

    if not _check_fiddler():
        return None

    try:
        crawler = FuliCrawler()
        data = crawler.fetch_holdings(datetime.now(TZ).strftime('%Y-%m-%d'))
        if data:
            return len(data)
        return 0
    except Exception as e:
        print(f"  Error crawling fuli: {e}")
        return None


# ═══════════════════════════════════════════════════════════════
# 知识星球爬取
# ═══════════════════════════════════════════════════════════════

def _save_zsxq_posts(posts, save_dir, filename, label=''):
    """保存帖子到JSON文件，去重合并。返回新增数。"""
    os.makedirs(save_dir, exist_ok=True)
    save_path = os.path.join(save_dir, filename)
    existing = []
    if os.path.exists(save_path):
        with open(save_path, encoding='utf-8') as f:
            existing = json.load(f)
    existing_ids = {p['topic_id'] for p in existing}
    new_posts = [p for p in posts if p['topic_id'] not in existing_ids]
    all_posts = existing + new_posts
    with open(save_path, 'w', encoding='utf-8') as f:
        json.dump(all_posts, f, ensure_ascii=False, indent=2)
    if label:
        print(f"  [{label}]: {len(posts)} fetched, {len(new_posts)} new -> {save_path}")
    return len(new_posts)


def crawl_zsxq():
    """
    爬取知识星球所有群组。
    """
    from src.zsxq.zsxq_scanner import ZsxqScanner

    config_path = os.path.join(PROJECT_ROOT, 'data', 'zsxq', 'config.json')
    try:
        with open(config_path, encoding='utf-8') as f:
            cfg = json.load(f)
    except Exception as e:
        print(f"  [zsxq] Cannot load config: {e}")
        return -1

    token = cfg['zsxq']['access_token']
    groups = cfg['zsxq'].get('groups', [
        {"group_id": cfg['zsxq']['group_id'], "name": "默认群组", "type": "research"}
    ])

    bloggers = {}
    bloggers_path = os.path.join(PROJECT_ROOT, 'bloggers.json')
    if os.path.exists(bloggers_path):
        with open(bloggers_path, encoding='utf-8') as f:
            bloggers = json.load(f)

    group_to_blogger = {}
    for uid, bcfg in bloggers.items():
        zg = bcfg.get('zsxq_group', '')
        if zg:
            group_to_blogger[zg] = uid

    total_posts = 0
    all_new_posts = 0
    date_str = datetime.now(TZ).strftime('%Y-%m-%d')

    for group in groups:
        gid = group['group_id']
        gname = group.get('name', gid)
        gtype = group.get('type', 'blogger')

        print(f"  [zsxq] Scanning {gname} ({gid}) type={gtype}...")

        try:
            scanner = ZsxqScanner(token=token, group_id=gid)

            if gid == '28888221524121':
                posts = scanner.scan_with_filter(star_owner_only=True)
            else:
                posts = scanner.get_recent_posts(hours=24)

            if not posts:
                print(f"    no posts")
                continue

            total_posts += len(posts)

            if gid in group_to_blogger:
                blogger_uid = group_to_blogger[gid]
                save_dir = os.path.join(PROJECT_ROOT, 'data', 'posts', blogger_uid)
                fused_posts = []
                for p in posts:
                    fused_posts.append({
                        **p,
                        'post_id': str(p.get('topic_id', '')),
                        'user_id': blogger_uid,
                        'platform': 'weixin',
                        'source': 'zsxq',
                    })
                filename = f'zsxq_{date_str}.json'
                new_count = _save_zsxq_posts(fused_posts, save_dir, filename,
                                             label=f'zsxq/{gname}->{blogger_uid}')
                all_new_posts += new_count

            elif gtype == 'research':
                save_dir = os.path.join(PROJECT_ROOT, 'data', 'zsxq', 'posts')
                filename = f'zsxq_posts_{date_str}.json'
                new_count = _save_zsxq_posts(posts, save_dir, filename,
                                             label=f'zsxq/{gname}')
                all_new_posts += new_count

            else:
                save_dir = os.path.join(PROJECT_ROOT, 'data', 'zsxq', 'posts', gid)
                filename = f'zsxq_posts_{date_str}.json'
                new_count = _save_zsxq_posts(posts, save_dir, filename,
                                             label=f'zsxq/{gname}')
                all_new_posts += new_count

        except Exception as e:
            print(f"  [zsxq] {gname}: ERROR - {e}")
            continue

    if all_new_posts > 0:
        print(f"  [zsxq] Syncing to SQLite...")
        try:
            import subprocess
            export_script = os.path.join(PROJECT_ROOT, 'scripts', 'export_zsxq_sqlite.py')
            env = os.environ.copy()
            env['PYTHONIOENCODING'] = 'utf-8'
            result = subprocess.run(
                [PYTHON_EXE, export_script],
                capture_output=True, text=True, encoding='utf-8', timeout=120,
                cwd=PROJECT_ROOT, env=env
            )
            if result.stdout:
                for line in result.stdout.strip().split('\n'):
                    if line.strip():
                        print(f"    {line.strip()}")
            if result.returncode != 0:
                print(f"  [zsxq] SQLite sync failed: {result.stderr[:300]}")
        except Exception as e:
            print(f"  [zsxq] SQLite sync error: {e}")

    print(f"  [zsxq] Total: {total_posts} posts across {len(groups)} groups, {all_new_posts} new")
    return total_posts


# ═══════════════════════════════════════════════════════════════
# 逾期任务检查（仪表盘 + 分析管线）
# ═══════════════════════════════════════════════════════════════

def _sync_to_github():
    """同步数据文件到 GitHub"""
    try:
        import subprocess
        result = subprocess.run(
            [PYTHON_EXE, 'github_sync.py'],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
        uploaded = sum(1 for line in result.stdout.split('\n') if '✅' in line)
        if uploaded > 0:
            print(f'  📤 GitHub sync: {uploaded} files')
        else:
            print(f'  📤 GitHub sync: no changes')
    except Exception as e:
        print(f'  ⚠️ GitHub sync failed: {e}')


def _check_and_refresh_dashboard():
    if _recently_ran('last_dashboard_ts', 60):
        return False

    state = load_heartbeat_state()
    last_dashboard = state.get('last_dashboard_ts', 0)
    now = int(datetime.now(TZ).timestamp() * 1000)
    hour = datetime.now(TZ).hour

    if hour >= 19 and (now - last_dashboard) > 24 * 3600 * 1000:
        print("[Heartbeat] Dashboard refresh due, running...")
        import subprocess
        subprocess.run(
            [PYTHON_EXE, 'src/cron/refresh_dashboard.py'],
            cwd=PROJECT_ROOT, timeout=120,
            env={**os.environ, 'PYTHONIOENCODING': 'utf-8'}
        )
        state['last_dashboard_ts'] = now
        save_heartbeat_state(state)
        return True
    return False


def _check_and_run_analysis():
    if _recently_ran('last_analysis_ts', 60):
        return False

    state = load_heartbeat_state()
    last_analysis = state.get('last_analysis_ts', 0)
    now = int(datetime.now(TZ).timestamp() * 1000)
    hour = datetime.now(TZ).hour

    if hour >= 20 and (now - last_analysis) > 24 * 3600 * 1000:
        print("[Heartbeat] Analysis pipeline due, running...")
        import subprocess
        scripts = [
            'src/analyzers/consensus.py',
            'src/analyzers/change_detector.py',
            'src/daily_brief.py',
        ]
        for script in scripts:
            print(f"  [Heartbeat] Running {script}...")
            subprocess.run(
                [PYTHON_EXE, script],
                cwd=PROJECT_ROOT, timeout=300,
                env={**os.environ, 'PYTHONIOENCODING': 'utf-8'}
            )
        state['last_analysis_ts'] = now
        save_heartbeat_state(state)
        return True
    return False


# ═══════════════════════════════════════════════════════════════
# 主入口：三平台独立执行，各自保存状态
# ═══════════════════════════════════════════════════════════════

def _run_platform(platform, crawl_func, threshold):
    """运行单个平台爬取，独立异常处理和状态保存。支持失败重试。"""
    if not should_crawl(platform, threshold):
        if not _should_retry(platform):
            print(f"[Heartbeat] {platform}: skip (within threshold)")
            return None
        tracker = _get_failure_tracker().get('failure_tracker', {}).get(platform, {})
        retry_n = tracker.get('failures', 0)
        print(f"[Heartbeat] {platform}: retry #{retry_n} (after {retry_n*5}min wait)")

    print(f"[Heartbeat] Crawling {platform}...")
    try:
        result = crawl_func()
        mark_crawled(platform)
        _clear_failure(platform)
        print(f"[Heartbeat] {platform}: done (result={result})")
        return result
    except Exception as e:
        err_str = str(e)
        if 'No module named' in err_str:
            import re as _re
            match = _re.search(r"No module named '(\w+)'", err_str)
            if match:
                print(f"  [WARN] Missing dependency '{match.group(1)}', run: py -m pip install {match.group(1)}")
            else:
                print(f"  [WARN] Missing dependency, run: py -m pip install <module>")

        failures = _record_failure(platform, err_str)
        total_tries = MAX_RETRIES + 1
        print(f"[Heartbeat] {platform}: FAILED (attempt {failures}/{total_tries}) - {e}")

        if failures <= MAX_RETRIES:
            wait_min = failures * (RETRY_INTERVAL_SEC // 60)
            print(f"  -> will retry in ~{wait_min}min")
        else:
            print(f"  -> ALL RETRIES EXHAUSTED for {platform}")

        return -1


def main():
    now = datetime.now(TZ)
    is_trading_hour = (now.weekday() < 5 and 9 <= now.hour <= 15)
    threshold = 30 if is_trading_hour else 60

    # ── 依赖自检：确保系统 Python 环境有所需依赖 ──
    import subprocess
    dep_check = subprocess.run(
        [PYTHON_EXE, '-c', 'import requests, pandas, jieba'],
        capture_output=True, text=True, timeout=30,
        env={**os.environ, 'PYTHONIOENCODING': 'utf-8'}
    )
    if dep_check.returncode != 0:
        print(f"[ERROR] System Python missing dependencies: {dep_check.stderr.strip()}")
        print("[ERROR] Run: py -m pip install requests pandas jieba")
        sys.exit(1)

    print(f"[Heartbeat] {now.strftime('%Y-%m-%d %H:%M:%S')} "
          f"| trading={is_trading_hour} | threshold={threshold}min")

    results = {}

    # ── 知乎 (约 15s) ──
    results['zhihu'] = _run_platform('zhihu', crawl_zhihu, threshold)

    # ── 知识星球 (约 30-90s) ──
    results['zsxq'] = _run_platform('zsxq', crawl_zsxq, threshold)

    # ── 复利笔记 — Fiddler 代理 + 微信小程序 (不记录 last_crawl，直接拉取) ──
    try:
        n = crawl_fuli()
        if n is not None:
            if n:
                print(f"  fuli: {n} stocks")
            else:
                print(f"  fuli: 0 stocks (empty)")
            results['fuli'] = n
    except:
        pass

    # ── 雪球 — 纯 open_link，无 token/cookie/autoglm (约 20s) ──
    results['xueqiu'] = _run_platform('xueqiu', crawl_xueqiu, threshold)

    # Summary
    crawled = {k: v for k, v in results.items() if v is not None}
    skipped = [k for k, v in results.items() if v is None]
    failed = [k for k, v in results.items() if v is not None and (isinstance(v, int) and v < 0)]

    if crawled:
        print(f"[Heartbeat] Crawled: {crawled}")
    if skipped:
        print(f"[Heartbeat] Skipped (too soon): {skipped}")
    if failed:
        print(f"[Heartbeat] Failed: {failed}")
    if not crawled and not skipped:
        print(f"[Heartbeat] Nothing to do.")

    # 逾期任务补做
    dashboard_ran = _check_and_refresh_dashboard()
    analysis_ran = _check_and_run_analysis()
    if dashboard_ran or analysis_ran:
        print(f"[Heartbeat] Auto catch-up: dashboard={dashboard_ran}, analysis={analysis_ran}")

    # GitHub sync
    _sync_to_github()

    # Check alerts after crawl summary
    _check_and_alert()

    print(f"[Heartbeat] Done: {results}")
    return results


if __name__ == '__main__':
    main()
