-- ============================================================
-- 3AI 协作投资系统 - 统一数据库 Schema
-- 三方约定：GLM + Kimi + MiniMax 共享此 Schema
-- 版本: v1.0
-- ============================================================

-- 文章原始数据（GLM 写入）
CREATE TABLE IF NOT EXISTS articles (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT NOT NULL,              -- xueqiu / wechat / report
    author      TEXT,                       -- 博主名 / 公众号名
    author_id   TEXT,                       -- 博主ID（雪球）
    title       TEXT,
    url         TEXT,
    content     TEXT,                       -- 原始正文
    pub_date    DATE,                       -- 发布日期
    crawl_date  DATETIME DEFAULT CURRENT_TIMESTAMP,
    category    TEXT,                       -- 文章分类（自动/手动）
    UNIQUE(source, url)
);

CREATE INDEX IF NOT EXISTS idx_articles_author ON articles(author);
CREATE INDEX IF NOT EXISTS idx_articles_date   ON articles(pub_date);
CREATE INDEX IF NOT EXISTS idx_articles_source ON articles(source);

-- 文本信号（GLM 的 LLM 提取结果）
CREATE TABLE IF NOT EXISTS text_signals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id      INTEGER REFERENCES articles(id),
    concept         TEXT NOT NULL,          -- 提取的概念，如"液冷服务器"
    related_stocks  TEXT,                   -- JSON ["002594", "300502"]
    sentiment       TEXT CHECK(sentiment IN ('bullish', 'bearish', 'neutral')),
    direction       TEXT,                   -- 具体方向描述
    confidence      REAL,                   -- 0.0 ~ 1.0
    extracted_date  DATE,
    raw_llm_output  TEXT,                   -- LLM原始输出（调试用）
    FOREIGN KEY(article_id) REFERENCES articles(id)
);

CREATE INDEX IF NOT EXISTS idx_signals_concept ON text_signals(concept);
CREATE INDEX IF NOT EXISTS idx_signals_date    ON text_signals(extracted_date);

-- 日K数据（Kimi 写入，baostock同步）
CREATE TABLE IF NOT EXISTS stock_daily (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT NOT NULL,              -- 如 "sh.600519"
    trade_date  DATE NOT NULL,
    open        REAL,
    high        REAL,
    low         REAL,
    close       REAL,
    volume      INTEGER,
    amount      REAL,
    adjustflag  INTEGER DEFAULT 2,          -- 1: 不复权, 2: 后复权, 3: 前复权
    turn        REAL,                       -- 换手率
    pctChg      REAL,                       -- 涨跌幅
    UNIQUE(code, trade_date, adjustflag)
);

CREATE INDEX IF NOT EXISTS idx_daily_code_date ON stock_daily(code, trade_date);

-- 技术面策略信号（Kimi 写入）
CREATE TABLE IF NOT EXISTS strategy_signals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    code            TEXT NOT NULL,
    strategy_name   TEXT NOT NULL,          -- turtle / ma_volume / rps_breakout / ...
    signal_date     DATE NOT NULL,
    action          TEXT CHECK(action IN ('buy', 'sell', 'hold', 'watch')),
    trigger_price   REAL,
    target_price    REAL,
    stop_price      REAL,
    strength        REAL,                   -- 信号强度 0~1
    description     TEXT,
    backtest_id     INTEGER,                -- 关联回测
    UNIQUE(code, strategy_name, signal_date)
);

CREATE INDEX IF NOT EXISTS idx_strat_code  ON strategy_signals(code);
CREATE INDEX IF NOT EXISTS idx_strat_date  ON strategy_signals(signal_date);
CREATE INDEX IF NOT EXISTS idx_strat_name  ON strategy_signals(strategy_name);

-- 回测结果（Kimi 写入）
CREATE TABLE IF NOT EXISTS backtest_results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_name   TEXT NOT NULL,
    code            TEXT,
    start_date      DATE,
    end_date        DATE,
    total_return    REAL,
    max_drawdown    REAL,
    sharpe_ratio    REAL,
    win_rate        REAL,
    trade_count     INTEGER,
    params_json     TEXT,                   -- JSON 参数
    run_date        DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Agent 辩论决策（MiniMax 写入）
CREATE TABLE IF NOT EXISTS decisions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    concept         TEXT NOT NULL,
    code            TEXT,                   -- 关联股票代码
    target_date     DATE NOT NULL,
    direction       TEXT CHECK(direction IN ('long', 'short', 'neutral')),
    confidence      REAL,                   -- 多Agent共识置信度
    kelly_ratio     REAL,                   -- Kelly仓位比例
    final_position  REAL,                   -- 经过风控后的最终仓位
    entry_price     REAL,
    target_price    REAL,
    stop_loss       REAL,
    risk_level      TEXT CHECK(risk_level IN ('low', 'medium', 'high')),
    decision_date   DATETIME DEFAULT CURRENT_TIMESTAMP,
    status          TEXT DEFAULT 'active' CHECK(status IN ('active', 'executed', 'cancelled', 'expired'))
);

CREATE INDEX IF NOT EXISTS idx_decisions_concept ON decisions(concept);
CREATE INDEX IF NOT EXISTS idx_decisions_date    ON decisions(target_date);
CREATE INDEX IF NOT EXISTS idx_decisions_status  ON decisions(status);

-- 辩论日志（MiniMax 写入，调试用）
CREATE TABLE IF NOT EXISTS debate_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id     INTEGER REFERENCES decisions(id),
    round           INTEGER,                -- 辩论轮次
    agent_role      TEXT,                   -- 角色名
    claim_type      TEXT CHECK(claim_type IN ('bull', 'bear', 'neutral')),
    content         TEXT,                   -- 论点内容
    confidence      REAL,
    timestamp       DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(decision_id) REFERENCES decisions(id)
);

-- 系统运行日志
CREATE TABLE IF NOT EXISTS run_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date    DATETIME DEFAULT CURRENT_TIMESTAMP,
    step        TEXT,                       -- collect / technical / debate / generate
    status      TEXT CHECK(status IN ('success', 'failure', 'partial')),
    message     TEXT,
    duration_ms INTEGER
);
