# 股票博主观点追踪系统 · 项目说明

> 最后更新: 2026-06-29 · Phase 4 收尾 · 52/56 (93%)

## 一、系统概述

多平台股票博主内容爬取 → 概念提取 → 情感判断 → 共振/分歧检测 → LLM叙事 → 日报输出。

**核心价值**: 自动追踪 5 位博主在 4 个平台的观点，横向对比共识与分歧，产出彭博社风格的每日观点简报。

## 二、博主覆盖

| 博主 | 平台 | UID/群组 | 数据量 | 特点 |
|------|------|----------|--------|------|
| 白河愁博士 | 雪球 | 7251377368 | 126帖 | 6组合调仓跟踪，暗语7条 |
| 雪球大V(燕云1988) | 雪球 | 1034624503 | 21帖 | 产业研究深度 |
| 派大星皮皮 | 知乎 | xiao-peng-61-47 | 105帖 | 长文分析为主，暗语7条 |
| 波king | 微信 + ZSXQ | 51115848448254 | 64帖 | 双源数据融合 |
| 知乎奥特之父 | ZSXQ | 28888221524121 | 🆕 | 仅星主帖子 |

## 三、16个核心模块

| 模块 | 路径 | 功能 |
|------|------|------|
| 雪球爬虫 | src/crawlers/xueqiu/ | TokenCycler + WAF浏览器过墙 |
| 知乎爬虫 | src/crawlers/zhihu/ | API full_content |
| ZSXQ扫描器 | src/zsxq/zsxq_scanner.py | 知识星球API |
| 编排器 | src/crawlers/orchestrator.py | 四平台统一调度 |
| 概念提取 | src/analyzers/sector_extractor.py | 上下文质量分类 |
| 情感判断 | src/analyzers/concept_sentiment.py | DeepSeek四维标签 |
| 共识引擎 | src/analyzers/consensus.py | 共振/分歧检测 |
| 变化检测 | src/analyzers/change_detector.py | 5类变化+调仓信号 |
| 调仓跟踪 | src/analyzers/portfolio_tracker.py | 6组合134信号 |
| 叙事引擎 | src/analyzers/narrative_engine.py | DeepSeek 300-600字叙事 |
| 暗语解析 | src/analyzers/alias_resolver.py | 14暗语→股票映射 |
| 日报生成 | src/daily_brief.py | 多段式+叙事头条 |
| 文本清洗 | src/preprocess/text_cleaner.py | 噪声过滤 |
| Prompt管理 | prompt_registry.py | 版本化+可回滚 |
| 仪表盘 | src/visualization/dashboard.html | 5Tab可视化 |
| SQLite研报API | scripts/stock_api_server.py | REST on :8765 |

## 四、数据资产

| 数据 | 规模 | 路径 |
|------|------|------|
| 帖子原始数据 | 655帖 | data/posts/ |
| 概念白名单 | 47概念3042股票 | data/concept_whitelist_v2.json |
| 共识时间线 | 171概念 | data/cross_blogger/view_timeline.json |
| 共振信号 | 12条(去噪) | data/cross_blogger/resonance.json |
| 变化日志 | 97条 | data/changes_log.json |
| SQLite研报库 | 1003股72研报 | data/zsxq/stock_pool.db |
| 暗语映射 | 14条 | data/nickname_map.json |
| Golden set | 10篇标注 | eval/golden_set_annotations.json |

## 五、调度系统

| 任务 | 频率 | 说明 |
|------|------|------|
| 心跳爬取 | 交易日30min/盘后2h | 雪球×2+知乎+ZSXQ×3 |
| 仪表盘刷新 | 每天19:00 | 5Tab可视化 |
| 分析管线 | 每天20:00 | consensus→change→daily |
| 每日复盘 | 每天17:30 | 两轮CoT深度分析(明日起) |

## 六、Prompt资产 (Prompt as Code)

| Prompt | 用途 |
|--------|------|
| prompts/concept_sentiment.txt | 概念四维情感 v2 |
| prompts/stock_sentiment.txt | 个股情感+暗语规则 |
| prompts/narratives/portfolio_summary.txt | 调仓叙事 |
| prompts/narratives/daily_headline.txt | 日报头条 |
| prompts/narratives/concept_consensus.txt | 概念共振分析 |
| prompts/narratives/divergence_explain.txt | 言行矛盾分析 |
| prompts/narratives/daily_blogger_review.txt | 两轮CoT复盘 |

## 七、已知问题

1. 热力图颜色单一 — 待优化
2. 雪球WAF需Chrome开启 — TokenCycler依赖
3. 微信推送未通 — 微信插件路由问题
4. 单博主热力图日期范围 — 用户反馈待修
5. 概念本体/产业链图谱 — 下一个大迭代
