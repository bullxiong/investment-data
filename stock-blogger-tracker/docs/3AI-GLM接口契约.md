# 3AI 数据采集模块 — 接口契约

> 给 GLM 的接入文档。README → 接口签名 → 数据格式 → 自测脚本。

## 1. 模块路径

```
stock-blogger-tracker/src/data_collection/
├── __init__.py   ← 两个对外函数
├── xueqiu.py     ← 雪球适配（参考实现）
├── wechat.py     ← 微信/知乎/ZSXQ/复利笔记适配（参考实现）
├── extractor.py  ← 信号提取（参考实现）
├── db_writer.py  ← SQLite 写入
└── main.py       ← 入口 + step_collect() 注释
```

## 2. 函数签名

### 2.1 collect_articles

```python
def collect_articles(sources, target_date, max_per_source=50):
    """
    参数:
        sources: List[str]  # 'xueqiu' | 'zhihu' | 'zsxq' | 'wechat' | 'fuli'
        target_date: str    # 'YYYY-MM-DD'
        max_per_source: int # 每个源最多返回条数，默认 50

    返回: List[Dict]
    """
```

**每个 Dict 必须包含这 8 个字段：**

| 字段 | 类型 | 说明 | 示例 |
|------|------|------|------|
| `source` | str | 数据源标识 | `'xueqiu'` `'zhihu'` |
| `url` | str | 原文链接 | `'https://xueqiu.com/7251377368/123'` |
| `title` | str | 标题（可为空字符串） | `'如何看当前半导体'` |
| `author` | str | 作者名 | `'白河愁博士'` |
| `author_id` | str | 作者唯一 ID | `'7251377368'` |
| `content` | str | 正文全文（不要截断） | `'半导体当前处于...'` |
| `created_at` | str | 发布时间 ISO | `'2026-07-05T14:30:00+08:00'` |
| `article_id` | str | 平台唯一 ID | `'305872354'` |

### 2.2 extract_signals

```python
def extract_signals(articles, llm_config=None):
    """
    参数:
        articles: List[Dict]  # collect_articles 的返回值
        llm_config: dict|None # 可选 {"api_key": "sk-xxx"}

    返回: List[Dict]
    """
```

**每个 Dict 必须包含这 8 个字段：**

| 字段 | 类型 | 说明 | 示例 |
|------|------|------|------|
| `article_id` | str | 关联的文章 ID | `'305872354'` |
| `source` | str | 数据源 | `'xueqiu'` |
| `concept` | str | 概念/板块名 | `'半导体'` `'AI算力'` |
| `related_stocks` | str | JSON 数组（股票代码） | `'["002371.SZ","688981.SH"]'` |
| `sentiment` | str | `'bullish'` `'bearish'` `'neutral'` | `'bullish'` |
| `direction` | str | `'long'` `'short'` `'watch'` | `'long'` |
| `confidence` | float | 0.0-1.0 | `0.85` |
| `rationale` | str | JSON 对象 | `'{"conviction":"high","time_horizon":"medium","risk_acknowledged":false}'` |

## 3. 参考实现

看这两个文件就能理解模式：

- [xueqiu.py](https://github.com/bullxiong/investment-data/blob/main/stock-blogger-tracker/src/data_collection/xueqiu.py) — 最简单的适配器
- [wechat.py](https://github.com/bullxiong/investment-data/blob/main/stock-blogger-tracker/src/data_collection/wechat.py) — 含知乎/ZSXQ/复利笔记 4 个适配器
- [extractor.py](https://github.com/bullxiong/investment-data/blob/main/stock-blogger-tracker/src/data_collection/extractor.py) — extract_signals 参考实现

## 4. GLM 需要做什么

在 GLM 的项目中实现同样的 `src/data_collection/` 模块，两个函数签名和返回值完全对齐以上规范。

**关键约束：**
- `collect_articles` 必须能独立调用，不依赖外部服务（或依赖时做好降级）
- `extract_signals` 内部可以调 GLM 自己的 LLM API
- 每篇文章可能产出多条 signal（一篇文章提到多个概念）
- `content` 字段长度无限制，不要自行截断
- `related_stocks` 是 JSON 字符串，不是 Python list

## 5. 自测脚本

```python
# 跑在 GLM 项目根目录
from src.data_collection import collect_articles, extract_signals

# 测试 collect_articles
articles = collect_articles(
    sources=['xueqiu', 'zhihu'],  # GLM 负责的数据源
    target_date='2026-07-05',
    max_per_source=50,
)
print(f'[collect] {len(articles)} articles')

# 验证格式
for a in articles:
    for k in ['source','url','title','author','author_id','content','created_at','article_id']:
        assert k in a, f'Missing field: {k}'

# 测试 extract_signals
signals = extract_signals(articles)
print(f'[extract] {len(signals)} signals')

# 验证格式
for s in signals:
    for k in ['article_id','source','concept','related_stocks','sentiment','direction','confidence','rationale']:
        assert k in s, f'Missing field: {k}'
    assert s['sentiment'] in ('bullish','bearish','neutral')
    assert s['direction'] in ('long','short','watch')
    assert 0 <= s['confidence'] <= 1

print('✅ All checks passed — GLM module is compatible')
```
