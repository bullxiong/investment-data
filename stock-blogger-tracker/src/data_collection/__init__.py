"""
3AI协作投资系统 · 数据采集模块
对外接口: collect_articles() + extract_signals()
内部调用现有爬虫/分析管线
"""
import sys
import os

# Ensure project root is on path for sub-module imports
PROJECT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT not in sys.path:
    sys.path.insert(0, PROJECT)

from .xueqiu import collect_xueqiu
from .wechat import collect_wechat, collect_zhihu, collect_fuli
from .extractor import extract_signals


def collect_articles(sources, target_date, config):
    """采集原始文章
    
    Args:
        sources: list of str, e.g. ['xueqiu', 'zhihu']
        target_date: str, 'YYYY-MM-DD' (reserved for future filtering)
        config: dict with platform-specific keys
    
    Returns:
        List[Dict] — 标准化文章列表
    """
    results = []
    for source in sources:
        if source == 'xueqiu':
            results.extend(collect_xueqiu(config))
        elif source == 'wechat':
            results.extend(collect_wechat(config))
        elif source == 'zhihu':
            results.extend(collect_zhihu(config))
        elif source == 'fuli':
            results.extend(collect_fuli(config))
    return results
