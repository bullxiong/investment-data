"""
3AI 协作投资系统 — 数据采集模块 (GLM/鸦 实现)
对外接口: collect_articles() + extract_signals()

适配自 stock-blogger-tracker/src/data_collection/
各源适配器在 _adapters/ 下，实际调爬虫/分析引擎。
"""
import sys
import os

# 把 stock-blogger-tracker 加入 sys.path，让内部爬虫/分析器模块可用
_STB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'stock-blogger-tracker')
if _STB not in sys.path:
    sys.path.insert(0, _STB)

from src.data_collection._adapters.xueqiu import collect_xueqiu
from src.data_collection._adapters.wechat import collect_wechat, collect_zhihu, collect_zsxq, collect_fuli
from src.data_collection._adapters.extractor import extract_signals

__all__ = ['collect_articles', 'extract_signals']


def collect_articles(sources, target_date, max_per_source=50):
    """采集原始文章 — 3AI 统一入口

    Args:
        sources: List[str]  数据源: 'xueqiu'|'zhihu'|'zsxq'|'wechat'|'fuli'
        target_date: str    目标日期 'YYYY-MM-DD'
        max_per_source: int 每个数据源最多收集条数

    Returns:
        List[Dict]  标准化文章列表 (8 字段)
    """
    config = _build_config(target_date, max_per_source)

    results = []
    for source in sources:
        collector = _DISPATCH.get(source)
        if collector is None:
            print(f"  [skip] unknown source: {source}")
            continue
        try:
            items = collector(config)
            results.extend(items)
        except Exception as e:
            print(f"  [err] {source}: {e}")

    return results


# ─── 内部调度 ───────────────────────────────────────────

def _build_config(target_date, max_per_source):
    return {
        'target_date': target_date,
        'max_per_source': max_per_source,
        'uids': ['7251377368', '1034624503'],
        'zhihu_slugs': ['xiao-peng-61-47'],
        'zsxq_groups': ['48888522142558', '51115848448254', '28888221524121'],
        'hold_date': target_date,
    }


_DISPATCH = {
    'xueqiu': collect_xueqiu,
    'zhihu':  collect_zhihu,
    'zsxq':   collect_zsxq,
    'wechat': collect_wechat,
    'fuli':   collect_fuli,
}
