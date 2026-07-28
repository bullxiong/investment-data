# -*- coding: utf-8 -*-
"""
workspace/src/ — 命名空间包。同时包含:
  - 本目录下的模块 (data_collection, core, decision_engine 等)
  - stock-blogger-tracker/src/ 下的模块 (analyzers, crawlers, zsxq 等)

使用 pkgutil.extend_path 实现多路径命名空间。
"""
from pkgutil import extend_path
__path__ = extend_path(__path__, __name__)

import os
_stb_src = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'stock-blogger-tracker', 'src'
)
if os.path.isdir(_stb_src) and _stb_src not in __path__:
    __path__.append(_stb_src)
