# 爬虫系统规范 v2

> 2026-06-29 梳理，替代散落在聊天记录/cron_runner/token_cycler/orchestrator 中的碎片知识

---

## 一、定时器规则

| 任务 | cron | 说明 |
|------|------|------|
| `stock-tracker-trading-morning` | `*/15 9,10 * * 1-5` | 早盘高频：工作日 9:00-10:59，每 15 分钟 |
| `stock-tracker-trading-afternoon` | `*/30 11,12,13,14 * * 1-5` | 午盘中频：工作日 11:00-14:59，每 30 分钟 |
| `stock-tracker-offhours` | `0 0,2,4,6,8,15,17,19,21,23 * * *` | 盘后低频：每天 0-23 点奇数小时整点（含周末） |
| `stock-tracker-dashboard-refresh` | `0 19 * * *` | 仪表盘刷新：每天 19:00 |

### 定时器问题检查

**当前问题**: 三条爬虫 cron 全部 timeout（连错 5-8 次）。根因是 cron_runner 调用了需要浏览器的 TokenCycler，但 cron isolated session 没有桌面环境。

**规则合理性**:
- ⚠️ `trading-morning`: 每 15 分钟太密。API 限流 + WAF token 单次有效，15 分钟内至多完成 1 个博主的 1 页。建议降为每 30 分钟，和 afternoon 合并为单条 `*/30 9-14 * * 1-5`
- ⚠️ `trading-afternoon`: 时间窗口覆盖到 14:59，但股市收盘 15:00，15 点那批数据在 offhours 覆盖。合理
- ✅ `offhours`: 奇数整点覆盖全天，但周末没必要每小时爬。改为 `0 15,19,21,23,1,3,5,7,9 * * 1-5` 覆盖盘后到次日开盘
- ✅ `dashboard-refresh`: 正常，无依赖浏览器

**timeout 设置**: 当前 300 秒。修复后建议 120 秒——爬成就好，爬不成就等下一轮。

---

## 二、目标平台与博主

| 平台 | 博主 | 标识 | 认证方式 | 扩展性 |
|------|------|------|---------|--------|
| 雪球 | 白河愁博士 | uid=7251377368 | xq_a_token + WAF md5__1038 | ✅ 加 uid 即可 |
| 雪球 | 雪球大V(燕云1988) | uid=1034624503 | xq_a_token (同账号级) | 同上 |
| 知乎 | 派大星皮皮 | slug=xiao-peng-61-47 | z_c0 cookie (时效未知) | ✅ 加 slug 即可 |
| 微信 | 波king(只爱甜妹) | biz=MzYyMzUyMjQ1NA== | 无自动化认证 | ❌ 需浏览器转发 |

**扩展方式**: 在 `bloggers.json` 中新增条目，cron_runner 自动识别平台路由:

```json
{
  "新UID": {
    "platform": "xueqiu|zhihu|weixin",
    "name": "博主名",
    // 雪球: 无需额外字段（xq_token 账号级共享）
    // 知乎: 无需额外字段（cookie 共享）
    // 微信: 需 biz 参数
  }
}
```

---

## 三、爬取方式（按优先级）

### 雪球

| 优先级 | 方式 | 完整性 | 需要 | 适用场景 |
|--------|------|--------|------|---------|
| P1 | API + WAF token | ✅ 完整 (21帖/页) | Chrome + 扩展 | 交互式/手动触发 |
| P2 | API (仅 cookie) | ⚠️ WAF 拦截 | 无 | 不可用 |
| P3 | open_link | ❌ 截断 (200字) | 无 | cron fallback |

**当前 cron 用的 P1 → timeout**。修复后 cron 应走 P3 fallback。

### 知乎

| 优先级 | 方式 | 完整性 | 需要 | 适用场景 |
|--------|------|--------|------|---------|
| P1 | API + full_content | ✅ 完整 | cookie (z_c0) | 所有场景 |
| P2 | API (excerpt only) | ❌ 摘要 (150字) | cookie | cookie 过期时 |
| P3 | open_link | ❌ 摘要 | 无 | 无 cookie 时 |

cron 走 P1（已修复 `full_content=True`）。

### 微信

| 优先级 | 方式 | 完整性 | 需要 | 适用场景 |
|--------|------|--------|------|---------|
| P1 | 用户转发到 bot | ✅ 完整 | 你手动转发 | 当前唯一方式 |
| P2 | 浏览器 agent 定时 | ✅ 完整 | Chrome + 扩展 | 未实现 |

---

## 四、数据要求

### 内容完整性

- ❌ 不可接受: 截断标记（"..."，"展开"），仅标题无正文，post_id=0
- ✅ 最低要求: 正文 ≥ 50 字，有真实 post_id（API）或可定位来源
- ✅ 理想要求: 正文全部获取，时间戳准确，股票代码齐全

### 去重与增量

| 机制 | 说明 |
|------|------|
| 按 post_id 去重 | API 模式：每个帖子的唯一 ID，跨次抓取自动去重 |
| 按 content hash 去重 | open_link 模式：无 post_id 时用标题+内容前 100 字去重 |
| 按日期增量 | 每天保存到 `data/posts/{uid}/YYYY-MM-DD.json`，新旧合并 |
| 不覆盖历史 | 新抓取追加到当日文件，旧数据保留 |

### 数据格式

所有平台爬取结果统一为:
```json
{
  "post_id": "string | int",
  "user_id": "string",
  "author": "string",
  "title": "string",
  "content": "string",        // 正文（优先 text 字段）
  "created_at": "ISO 8601",
  "source": "xueqiu|zhihu|weixin",
  "stocks": ["股票代码"],     // 关联股票（雪球 API 自动提供）
  "url": "原始链接"
}
```

---

## 五、关键细节与教训

### WAF 机制
- 阿里云 WAF，md5__1038 token 由浏览器 JS 动态生成
- **单次有效**，用完即废。同一 token 不能用于第 2 页或不同用户
- 获取方式: `autoglm run` 打开 API URL → 浏览器解 WAF → `md5_token.py` 提取

### Cookie 管理
- xq_a_token: 账号级认证，存放 `data/xueqiu_cookies.json`。与 u、cookiesu 一起使用
- z_c0: 知乎认证，存放 `data/zhihu_cookies.json`。时效未知，失效时需重取
- ZSXQ token: `C7A5B7B0-D9FE...`，群组 `48888522142558`

### Cron 环境限制
- isolated session **无桌面环境**，不能调浏览器
- autoglm/browser agent **不能在 cron 中使用**
- 因此 cron_runner 必须使用不依赖浏览器的爬取方式

### 爬虫改进历史
- 6/27: 发现雪球 description 截断 → 改 text 字段优先
- 6/27: 发现知乎内容为空 → 改 full_content=True
- 6/27: 雪球 SOP 四步法验证通过
- 6/28: SOP 固化为 TokenCycler + CrawlerOrchestrator
- 6/28: 发现 cron 全部 timeout → TokenCycler 不兼容 cron → 待修复
- 6/29: 新增波king星球(51115848448254) + 奥特之父星球(28888221524121) 知识星球接入
- 6/29: 波king星球数据融合到 weixin-boking 目录，双源统一分析
- 6/29: 奥特之父星球启用星主过滤，仅保留星主帖子

---

## 六、知识星球多群组架构

### 群组一览

| Group ID | 名称 | 类型 | 博主UID | 特殊处理 |
|----------|------|------|---------|---------|
| 48888522142558 | 投资研报库 | research | — | SQLite分析 |
| 51115848448254 | 波king星球 | blogger | weixin-boking | **融合到微信目录** |
| 28888221524121 | 奥特之父星球 | blogger | zsxq-outofather | **星主过滤** |

### 数据路径

| 群组 | JSON保存路径 | 格式 |
|------|-------------|------|
| 研报库 | `data/zsxq/posts/zsxq_posts_YYYY-MM-DD.json` | 原始ZSXQ格式 |
| 波king星球 | `data/posts/weixin-boking/zsxq_YYYY-MM-DD.json` | 融合格式(user_id=weixin-boking) |
| 奥特之父 | `data/zsxq/posts/28888221524121/zsxq_posts_YYYY-MM-DD.json` | 原始ZSXQ格式 |

### 波king双源融合

- ZSXQ帖子: `source="zsxq"`, `user_id="weixin-boking"`, `platform="weixin"`
- 微信帖子: `source="weixin"`, `user_id="weixin-boking"`, `platform="weixin"`
- 分析管线对"weixin-boking"的分析自动覆盖两个来源
- 保存为独立文件避免互相覆盖：`zsxq_YYYY-MM-DD.json` vs `YYYY-MM-DD.json`

### 星主过滤

- `scan_with_filter(star_owner_only=True)` 自动调用 `_get_group_owner()`
- 仅保留 author_info.user_id == 星主ID 的帖子
- 星主ID获取失败时降级为不过滤（打印警告）

---

## 七、全平台调度防呆总览

### 全平台一览

| 平台 | 博主 | 爬取方式 | 调度频率 | 防呆策略 | 成功率 |
|------|------|---------|---------|---------|--------|
| 雪球 | 白河愁 | 心跳 TokenCycler(autoglm→WAF) | 交易日30min/盘后2h | Chrome检测+fallback open_link | ⚠️ WAF单页 |
| 雪球 | 雪球大V | 同上 | 同上 | 同上 | ⚠️ WAF单页 |
| 知乎 | 派大星 | 心跳 API full_content | 同上 | cookie过期检测+excerpt fallback | ✅ |
| 微信 | 波king | 手动转发 | — | — | — |
| ZSXQ | 研报库(4888…) | 心跳 ZsxqScanner | 同上 | API错误重试3次 | ✅ |
| ZSXQ | 波king(5111…) | 心跳 ZsxqScanner | 同上 | 同上+合并微信数据 | 🆕 |
| ZSXQ | 奥特之父(2888…) | 心跳 ZsxqScanner | 同上 | 同上+星主过滤 | 🆕 |

### 心跳检查清单

```python
platforms = [
    'xueqiu',      # 雪球(白河愁+大V)
    'zhihu',       # 知乎(派大星)
    'zsxq',        # ZSXQ(研报库+波king星球+奥特之父)
]
```

### 防呆规则

1. **Chrome 不可用**: 雪球回退到 open_link（截断内容但不会卡死）
2. **API 超时**: 3次重试，每次间隔递增(5s/10s/20s)
3. **cookie 过期**: z_c0/xq_a_token 过期时记录告警，不影响其他平台
4. **WAF 拦截**: TokenCycler 失败时跳过该博主，下轮重试
5. **单平台失败**: 不阻塞其他平台
6. **单群组失败**: ZSXQ 多群组中单个群组失败不阻塞其他群组

### 爬取后自动处理链

```
爬取完成 → save_posts → export_zsxq_sqlite(仅ZSXQ) → mark_crawled
```

### 重试参数

| 场景 | 最大重试 | 重试间隔 | 备注 |
|------|---------|---------|------|
| ZSXQ API 请求 | 3 | 10s(固定) | ZsxqScanner 内置 |
| 雪球 WAF token | 3 | 按TokenCycler配置 | TokenCycler 内置 |
| 知乎 API | 3 | 按crawler配置 | ZhiHuCrawler 内置 |
| SQLite导出 | 1 | — | 非关键路径，失败不重试 |

### Token 管理

| Token | 存储位置 | 用途 | 有效期 |
|-------|---------|------|--------|
| xq_a_token | data/xueqiu_cookies.json | 雪球认证 | 会话级 |
| md5__1038 | TokenCycler 动态提取 | 雪球WAF | 单次 |
| z_c0 | data/zhihu_cookies.json | 知乎认证 | 未知 |
| zsxq_access_token | data/zsxq/config.json | 知识星球API | 长期 |

> **注意**: 所有知识星球群组共用同一个 zsxq_access_token
