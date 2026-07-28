# -*- coding: utf-8 -*-
"""
GitHub Sync — 心跳采集完成后自动推送关键文件到 bullxiong/investment-data

调用链条:
    heartbeat_crawl.py:main() → _sync_to_github() → github_sync.py

推送内容分两层:
    Layer A (核心源码): src/analyzers/ + src/utils/ + src/crawlers/ + src/zsxq/
    Layer B (分析产物): data/cross_blogger/*.json + data/changes_log.json
"""
import json, urllib.request, base64, io, sys, os, subprocess
from urllib.parse import quote
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# ── Token: 从用户环境变量读取（非硬编码） ──
def _get_token():
    """从 Windows 用户环境变量读取 GITHUB_TOKEN"""
    result = subprocess.run(
        ['powershell', '-NoProfile', '-Command',
         '[Environment]::GetEnvironmentVariable("GITHUB_TOKEN", "User")'],
        capture_output=True, text=True, timeout=5
    )
    token = result.stdout.strip()
    if not token or not token.startswith('ghp_'):
        print(f"  ⚠️ GitHub token invalid or missing (got {len(token)} chars)")
        return None
    return token

TOKEN = _get_token()
if not TOKEN:
    print("  ⚠️ GitHub sync disabled: no valid GITHUB_TOKEN in environment")
    sys.exit(0)

OWNER = 'bullxiong'
REPO = 'investment-data'
PREFIX = 'stock-blogger-tracker/'
PROJECT = os.path.dirname(os.path.abspath(__file__))


def gh_request(method, path, data=None):
    url = f'https://api.github.com{path}'
    body = json.dumps(data).encode('utf-8') if data else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header('Authorization', f'Bearer {TOKEN}')
    req.add_header('Accept', 'application/vnd.github+json')
    req.add_header('X-GitHub-Api-Version', '2022-11-28')
    if body:
        req.add_header('Content-Type', 'application/json')
    r = urllib.request.urlopen(req, timeout=30)
    return json.loads(r.read())


def upload_file(local_path, repo_path):
    """Upload/update a single file on GitHub"""
    if not os.path.exists(local_path):
        return None
    size = os.path.getsize(local_path) / 1024
    with open(local_path, 'rb') as f:
        content = base64.b64encode(f.read()).decode('ascii')

    encoded_path = '/'.join(quote(part, safe='') for part in repo_path.replace('\\', '/').split('/'))

    try:
        result = gh_request('PUT', f'/repos/{OWNER}/{REPO}/contents/{encoded_path}', {
            'message': f'auto: sync {os.path.basename(local_path)} ({size:.0f}KB)',
            'content': content,
        })
        return result.get('content', {}).get('html_url', 'ok')
    except Exception as e:
        error_str = str(e)
        if '422' in error_str:
            # File exists → get sha and update
            try:
                existing = gh_request('GET', f'/repos/{OWNER}/{REPO}/contents/{encoded_path}')
                sha = existing.get('sha', '')
                result = gh_request('PUT', f'/repos/{OWNER}/{REPO}/contents/{encoded_path}', {
                    'message': f'auto: update {os.path.basename(local_path)} ({size:.0f}KB)',
                    'content': content,
                    'sha': sha,
                })
                return result.get('content', {}).get('html_url', 'updated')
            except:
                return None
        return None


# ═══════════════════════════════════════════════════════════
# 推送清单
# ═══════════════════════════════════════════════════════════

# Layer A: 核心源码（KIMI 拉下来就能 import）
LAYER_A_SOURCE = [
    # 分析引擎
    'src/analyzers/stock_decoder.py',
    'src/analyzers/sector_extractor.py',
    'src/analyzers/consensus.py',
    'src/analyzers/concept_sentiment.py',
    'src/analyzers/change_detector.py',
    'src/analyzers/view_tracker.py',
    'src/analyzers/sentiment.py',
    'src/analyzers/cross_analysis.py',
    'src/analyzers/narrative_engine.py',
    'src/analyzers/alias_resolver.py',
    # 爬虫
    'src/crawlers/xueqiu/crawler.py',
    'src/crawlers/xueqiu/parser.py',
    'src/crawlers/xueqiu/token_cycler.py',
    'src/crawlers/zhihu/crawler.py',
    'src/crawlers/orchestrator.py',
    'src/crawlers/fuli_crawler.py',
    # 知识星球
    'src/zsxq/zsxq_scanner.py',
    'src/zsxq/stock_analyzer.py',
    'src/zsxq/stock_pool_manager.py',
    'src/zsxq/concept_hierarchy.py',
    'src/zsxq/sentiment_extractor.py',
    # 预处理
    'src/preprocess/text_cleaner.py',
    # 数据采集适配层
    'src/data_collection/__init__.py',
    'src/data_collection/xueqiu.py',
    'src/data_collection/wechat.py',
    'src/data_collection/extractor.py',
    'src/data_collection/db_writer.py',
    # 工具
    'src/utils/md5_token.py',
    'src/utils/open_link.py',
    'src/utils/pinyin_map.py',
    # 心跳脚本
    'scripts/heartbeat_crawl.py',
    'github_sync.py',
]

# Layer B: 分析产物（数据文件，跨日累积）
LAYER_B_DATA = [
    'data/cross_blogger/resonance.json',
    'data/cross_blogger/concept_stocks_cleaned_v2.json',
    'data/cross_blogger/cross_signals.json',
    'data/cross_blogger/research_baseline.json',
    'data/cross_blogger/view_timeline.json',
    'data/changes_log.json',
    'data/concept_match_report.md',
    'data/daily_2026-07-01_deepseek.md',
]

# Layer C: 元数据（静态，变化少）
LAYER_C_META = [
    'data/stock_db/stocks.json',
    'data/stock_db/alias.json',
    'data/stock_db/community_concepts.json',
    'data/stock_db/industries.json',
    'data/stock_db/concept_names.json',
    'data/stock_db/industry_names.json',
    'data/stock_db/pinyin_stocks.json',
    'data/zsxq/canonical_taxonomy.json',
    'data/zsxq/stock_code_cache.json',
]


# ═══════════════════════════════════════════════════════════
# 增量策略：只推送有变化的文件
# ═══════════════════════════════════════════════════════════

def get_last_sync_state():
    """读取上次同步的文件 hash 快照"""
    state_path = os.path.join(PROJECT, 'data', '.github_sync_state.json')
    if os.path.exists(state_path):
        with open(state_path, encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_sync_state(state):
    state_path = os.path.join(PROJECT, 'data', '.github_sync_state.json')
    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    with open(state_path, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def file_hash(path):
    """简单 hash: 文件大小 + 最后修改时间"""
    stat = os.stat(path)
    return f"{stat.st_size}:{int(stat.st_mtime)}"


print(f"[github_sync] {os.path.basename(__file__)} starting...")

# Build all file lists
all_layers = {
    'source': LAYER_A_SOURCE,
    'data': LAYER_B_DATA,
    'meta': LAYER_C_META,
}

last_state = get_last_sync_state()
new_state = {}
uploaded = 0
skipped = 0
errors = 0

for layer_name, file_list in all_layers.items():
    for rel_path in file_list:
        full_path = os.path.join(PROJECT, rel_path.replace('/', os.sep))
        if not os.path.exists(full_path):
            continue
        
        h = file_hash(full_path)
        prev_h = last_state.get(rel_path, '')
        
        if h == prev_h:
            skipped += 1
            new_state[rel_path] = h
            continue
        
        result = upload_file(full_path, PREFIX + rel_path)
        if result and 'ERROR' not in str(result):
            uploaded += 1
            new_state[rel_path] = h
            if uploaded <= 5:  # only print first 5 to keep output clean
                print(f"  ✅ {rel_path}")
        else:
            errors += 1
            print(f"  ❌ {rel_path}")

if uploaded > 0:
    save_sync_state(new_state)
    print(f"[github_sync] {uploaded} changed files pushed, {skipped} unchanged, {errors} errors")
    print(f"[github_sync] https://github.com/{OWNER}/{REPO}")
else:
    print(f"[github_sync] no changes ({skipped} files unchanged)")
