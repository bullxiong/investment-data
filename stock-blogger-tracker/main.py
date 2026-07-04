"""
3AI 协作投资系统 — main.py
入口脚本：采集 → 提取信号 → 写入DB → 作为3AI系统数据源
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.data_collection import collect_articles, extract_signals
from src.data_collection.db_writer import init_db, save_articles, save_signals


def step_collect():
    """
    Step 1: collect_articles(sources, target_date, max_per_source=50) -> List[Dict]

    参数:
        sources:       List[str]  数据源列表，可选: 'xueqiu', 'zhihu', 'zsxq', 'wechat', 'fuli'
        target_date:   str        目标日期 'YYYY-MM-DD'
        max_per_source: int       每个数据源最多收集条数 (default 50)

    返回值 List[Dict]，每条 article 包含:
        source:      str  数据源标识 ('xueqiu'|'zhihu'|'zsxq'|'wechat'|'fuli')
        url:         str  原文链接
        title:       str  标题
        author:      str  作者名
        author_id:   str  作者ID
        content:     str  正文全文
        created_at:  str  ISO时间戳
        article_id:  str  平台唯一ID

    ---

    Step 2: extract_signals(articles, llm_config) -> List[Dict]

    参数:
        articles:    List[Dict]  collect_articles() 的返回值
        llm_config:  dict        可选，LLM配置 {"api_key": "..."}

    返回值 List[Dict]，每条 signal 包含:
        article_id:      str   关联的 article_id
        source:          str   数据源
        concept:         str   提取的概念/板块名
        related_stocks:  str   JSON数组，关联股票代码 ['000001.SZ', '600000.SH']
        sentiment:       str   情感方向: 'bullish'|'bearish'|'neutral'
        direction:       str   操作方向: 'long'|'short'|'watch'
        confidence:      float 置信度 0.0-1.0
        rationale:       str   JSON，含 conviction/time_horizon/risk_acknowledged
    """
    pass


def example_usage():
    """示例：3AI系统调用本模块的标准流程"""
    # Step 1: 采集
    articles = collect_articles(
        sources=['xueqiu', 'zhihu', 'zsxq', 'fuli'],
        target_date='2026-07-05',
        max_per_source=50,
    )
    print(f"[collect] {len(articles)} articles")

    # Step 2: 提取信号
    signals = extract_signals(articles, llm_config=None)
    print(f"[extract] {len(signals)} signals")

    # Step 3: 写入DB
    init_db()
    save_articles(articles)
    save_signals(signals)
    print("[db] saved")

    # Step 4: 3AI系统读取 DB 进行分析
    return articles, signals


if __name__ == '__main__':
    example_usage()
