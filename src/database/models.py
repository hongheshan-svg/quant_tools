"""
数据库模型定义 - SQLAlchemy ORM
覆盖：热搜、财经新闻、行情数据、涨停板、AI分析结果、交易信号、国际数据等
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """ORM 基类"""
    pass


# ============================================================
# 数据采集层 - 舆情数据
# ============================================================

class HotSearch(Base):
    """热搜数据（微博/抖音/头条统一存储）"""
    __tablename__ = "hot_search"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(String(20), nullable=False, comment="来源: weibo/douyin/toutiao")
    title = Column(String(500), nullable=False, comment="热搜标题")
    rank = Column(Integer, comment="排名")
    hot_value = Column(Integer, comment="热度值")
    category = Column(String(50), comment="分类")
    url = Column(String(1000), comment="链接")
    collected_at = Column(DateTime, default=datetime.now, comment="采集时间")
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index("idx_hotsearch_source_time", "source", "collected_at"),
        Index("idx_hotsearch_title", "title"),
    )


class FinanceNews(Base):
    """财经新闻（财联社等）"""
    __tablename__ = "finance_news"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(String(20), nullable=False, comment="来源: cailianshe/xueqiu/jiuyan")
    title = Column(String(500), nullable=False, comment="标题")
    content = Column(Text, comment="内容摘要")
    news_time = Column(DateTime, comment="新闻发布时间")
    category = Column(String(50), comment="分类: 快讯/研报/复盘等")
    tags = Column(String(500), comment="标签，逗号分隔")
    url = Column(String(1000), comment="链接")
    collected_at = Column(DateTime, default=datetime.now)
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index("idx_news_source_time", "source", "news_time"),
        Index("idx_news_collected_at", "collected_at"),
        Index("idx_news_source_collected", "source", "collected_at"),
    )


# ============================================================
# 数据采集层 - 行情数据
# ============================================================

class StockDaily(Base):
    """个股日线行情"""
    __tablename__ = "stock_daily"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(10), nullable=False, comment="股票代码")
    name = Column(String(20), comment="股票名称")
    trade_date = Column(String(10), nullable=False, comment="交易日 YYYY-MM-DD")
    open = Column(Float, comment="开盘价")
    close = Column(Float, comment="收盘价")
    high = Column(Float, comment="最高价")
    low = Column(Float, comment="最低价")
    volume = Column(Float, comment="成交量（手）")
    amount = Column(Float, comment="成交额（元）")
    change_pct = Column(Float, comment="涨跌幅 %")
    turnover = Column(Float, comment="换手率 %")
    total_mv = Column(Float, comment="总市值（元）")
    circ_mv = Column(Float, comment="流通市值（元）")
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment="最后更新时间")

    __table_args__ = (
        Index("idx_daily_code_date", "code", "trade_date", unique=True),
    )


class LimitUpStock(Base):
    """涨停板数据"""
    __tablename__ = "limit_up_stock"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(10), nullable=False, comment="股票代码")
    name = Column(String(20), comment="股票名称")
    trade_date = Column(String(10), nullable=False, comment="交易日")
    close = Column(Float, comment="收盘价")
    change_pct = Column(Float, comment="涨跌幅 %")
    limit_up_type = Column(String(20), comment="涨停类型: 首板/连板")
    continuous_days = Column(Integer, default=1, comment="连板天数")
    seal_amount = Column(Float, comment="封单金额（元）")
    seal_ratio = Column(Float, comment="封单占比（封单/流通市值）")
    first_limit_time = Column(String(10), comment="首次涨停时间 HH:MM")
    last_limit_time = Column(String(10), comment="最后涨停时间 HH:MM")
    open_count = Column(Integer, default=0, comment="打开涨停次数")
    sector = Column(String(200), comment="所属板块")
    reason = Column(String(500), comment="涨停原因/题材")
    concepts = Column(String(300), comment="涨停原因题材标签（同花顺，用+连接）")
    circ_mv = Column(Float, comment="流通市值（元）")
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index("idx_limitup_code_date", "code", "trade_date", unique=True),
        Index("idx_limitup_date", "trade_date"),
    )


class StockInfo(Base):
    """股票基础信息（来自沪深北交易所官方列表）"""
    __tablename__ = "stock_info"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(10), nullable=False, comment="股票代码（6位，不带交易所前缀）")
    name = Column(String(20), comment="股票简称")
    exchange = Column(String(4), comment="交易所: sh/sz/bj")
    list_date = Column(String(10), comment="上市日期 YYYY-MM-DD")
    updated_at = Column(DateTime, default=datetime.now, comment="最后刷新时间")

    __table_args__ = (
        Index("idx_stock_info_code", "code", unique=True),
    )


class StockDiagnosis(Base):
    """个股 AI 诊断结果（决策仪表盘 JSON）"""
    __tablename__ = "stock_diagnosis"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(10), nullable=False, comment="股票代码（6位）")
    name = Column(String(20), comment="股票名称")
    trade_date = Column(String(10), comment="诊断所用行情日期")
    action = Column(String(10), comment="操作建议 buy/add/hold/watch/reduce/sell/avoid")
    score = Column(Float, comment="评分 0-100")
    result_json = Column(Text, comment="完整诊断结果 JSON")
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index("idx_stock_diagnosis_code", "code", "created_at"),
    )


class TradeCalendar(Base):
    """A股交易日历（来自新浪交易日历）"""
    __tablename__ = "trade_calendar"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_date = Column(String(10), nullable=False, comment="交易日 YYYY-MM-DD")
    updated_at = Column(DateTime, default=datetime.now, comment="最后刷新时间")

    __table_args__ = (
        Index("idx_trade_calendar_date", "trade_date", unique=True),
    )


class DragonTigerBoard(Base):
    """龙虎榜数据"""
    __tablename__ = "dragon_tiger_board"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(10), nullable=False, comment="股票代码")
    name = Column(String(20), comment="股票名称")
    trade_date = Column(String(10), nullable=False, comment="交易日")
    reason = Column(String(200), comment="上榜原因")
    buy_seat = Column(Text, comment="买入席位JSON")
    sell_seat = Column(Text, comment="卖出席位JSON")
    buy_total = Column(Float, comment="买入总额")
    sell_total = Column(Float, comment="卖出总额")
    net_amount = Column(Float, comment="净买入额")
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index("idx_dragon_code_date", "code", "trade_date"),
    )


class NorthboundFlow(Base):
    """北向资金流向"""
    __tablename__ = "northbound_flow"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_date = Column(String(10), nullable=False, comment="交易日")
    sh_net_inflow = Column(Float, comment="沪股通净流入（亿元）")
    sz_net_inflow = Column(Float, comment="深股通净流入（亿元）")
    total_net_inflow = Column(Float, comment="合计净流入（亿元）")
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index("idx_northbound_date", "trade_date", unique=True),
    )


# ============================================================
# 数据采集层 - 国际数据
# ============================================================

class USStockEarnings(Base):
    """美股头部公司财报数据"""
    __tablename__ = "us_stock_earnings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(10), nullable=False, comment="美股代码: NVDA/AAPL等")
    company_name = Column(String(50), comment="公司名称")
    report_date = Column(String(10), nullable=False, comment="财报发布日期")
    quarter = Column(String(10), comment="季度: Q1/Q2/Q3/Q4")
    revenue = Column(Float, comment="营收（亿美元）")
    revenue_estimate = Column(Float, comment="营收预期")
    revenue_surprise_pct = Column(Float, comment="营收超预期 %")
    eps = Column(Float, comment="每股收益")
    eps_estimate = Column(Float, comment="EPS预期")
    eps_surprise_pct = Column(Float, comment="EPS超预期 %")
    guidance = Column(Text, comment="业绩指引摘要")
    after_hours_change_pct = Column(Float, comment="盘后涨跌幅 %")
    key_highlights = Column(Text, comment="财报电话会关键要点")
    a_share_impact = Column(Text, comment="对A股影响分析JSON")
    collected_at = Column(DateTime, default=datetime.now)
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index("idx_earnings_symbol_date", "symbol", "report_date"),
    )


class GlobalNews(Base):
    """国际重大新闻/事件"""
    __tablename__ = "global_news"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(String(30), nullable=False, comment="来源: cailianshe_global/akshare")
    title = Column(String(500), nullable=False, comment="标题")
    content = Column(Text, comment="内容摘要")
    category = Column(String(50), comment="分类: 美联储/经济数据/地缘政治/大宗商品")
    news_time = Column(DateTime, comment="新闻时间")
    importance = Column(Integer, default=5, comment="重要性 1-10")
    url = Column(String(1000), comment="新闻链接")
    a_share_impact_direction = Column(String(10), comment="对A股影响方向: 利好/利空/中性")
    a_share_impact_sectors = Column(Text, comment="影响板块JSON")
    collected_at = Column(DateTime, default=datetime.now)
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index("idx_globalnews_time", "news_time"),
        Index("idx_globalnews_category", "category"),
        Index("idx_globalnews_collected_at", "collected_at"),
    )


class USMarketDaily(Base):
    """隔夜美股表现"""
    __tablename__ = "us_market_daily"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_date = Column(String(10), nullable=False, comment="交易日")
    dow_jones_close = Column(Float, comment="道琼斯收盘")
    dow_jones_change_pct = Column(Float, comment="道琼斯涨跌幅 %")
    nasdaq_close = Column(Float, comment="纳斯达克收盘")
    nasdaq_change_pct = Column(Float, comment="纳斯达克涨跌幅 %")
    sp500_close = Column(Float, comment="标普500收盘")
    sp500_change_pct = Column(Float, comment="标普500涨跌幅 %")
    china_concept_index = Column(Float, comment="中概股指数")
    china_concept_change_pct = Column(Float, comment="中概股涨跌幅 %")
    vix = Column(Float, comment="恐慌指数VIX")
    oil_price = Column(Float, comment="WTI原油价格")
    gold_price = Column(Float, comment="黄金价格")
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index("idx_usmarket_date", "trade_date", unique=True),
    )


# ============================================================
# AI 分析结果
# ============================================================

class SentimentAnalysis(Base):
    """舆情分析结果"""
    __tablename__ = "sentiment_analysis"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_type = Column(String(20), nullable=False, comment="来源类型: hot_search/finance_news/global_news")
    source_id = Column(Integer, comment="来源记录ID")
    original_text = Column(Text, comment="原始文本")
    related_stock_code = Column(String(10), comment="关联股票代码")
    related_stock_name = Column(String(20), comment="关联股票名称")
    related_sector = Column(String(100), comment="关联板块")
    sentiment = Column(String(10), comment="情绪方向: bullish/bearish/neutral")
    impact_score = Column(Float, comment="影响力评分 1-10")
    duration_estimate = Column(String(20), comment="持续时间预估: 短期/中期/长期")
    analysis_reason = Column(Text, comment="分析理由")
    analyzed_at = Column(DateTime, default=datetime.now)
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index("idx_sentiment_stock", "related_stock_code"),
        Index("idx_sentiment_time", "analyzed_at"),
        Index("idx_sentiment_source_time", "source_type", "analyzed_at"),
    )


class GlobalImpactAnalysis(Base):
    """国际事件对A股影响分析结果"""
    __tablename__ = "global_impact_analysis"

    id = Column(Integer, primary_key=True, autoincrement=True)
    analysis_date = Column(String(10), nullable=False, comment="分析日期")
    us_market_summary = Column(Text, comment="隔夜美股表现摘要")
    earnings_summary = Column(Text, comment="财报影响摘要")
    global_events_summary = Column(Text, comment="国际事件摘要")
    overall_direction = Column(String(10), comment="总体影响方向: bullish/bearish/neutral")
    overall_impact_score = Column(Float, comment="总体影响程度 1-10")
    affected_sectors = Column(Text, comment="受影响板块JSON")
    benefited_stocks = Column(Text, comment="受益个股JSON")
    hurt_stocks = Column(Text, comment="受损个股JSON")
    analysis_detail = Column(Text, comment="详细分析")
    analyzed_at = Column(DateTime, default=datetime.now)
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index("idx_globalimpact_date", "analysis_date", unique=True),
    )


# ============================================================
# 策略层 - 评分与信号
# ============================================================

class StockScore(Base):
    """个股综合评分"""
    __tablename__ = "stock_score"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(10), nullable=False, comment="股票代码")
    name = Column(String(20), comment="股票名称")
    score_date = Column(String(10), nullable=False, comment="评分日期")
    sentiment_score = Column(Float, default=0, comment="舆情评分")
    limit_up_score = Column(Float, default=0, comment="涨停连板评分")
    seal_strength_score = Column(Float, default=0, comment="封单强度评分")
    sector_effect_score = Column(Float, default=0, comment="板块效应评分")
    capital_flow_score = Column(Float, default=0, comment="资金流向评分")
    technical_score = Column(Float, default=0, comment="技术面评分")
    market_emotion_score = Column(Float, default=0, comment="市场情绪评分")
    global_score = Column(Float, default=0, comment="国际因子评分")
    composite_score = Column(Float, default=0, comment="综合评分")
    rank = Column(Integer, comment="排名")
    recommendation = Column(String(20), comment="建议: strong_buy/buy/hold/avoid")
    reason = Column(Text, comment="推荐理由")
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index("idx_score_date_rank", "score_date", "rank"),
        Index("idx_score_code_date", "code", "score_date", unique=True),
    )


class TradeSignal(Base):
    """交易信号"""
    __tablename__ = "trade_signal"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(10), nullable=False, comment="股票代码")
    name = Column(String(20), comment="股票名称")
    signal_date = Column(String(10), nullable=False, comment="信号日期")
    signal_type = Column(String(10), nullable=False, comment="信号类型: buy/sell")
    signal_strength = Column(Float, comment="信号强度 0-1")
    entry_price = Column(Float, comment="建议买入价")
    target_price = Column(Float, comment="目标价")
    stop_loss_price = Column(Float, comment="止损价")
    composite_score = Column(Float, comment="综合评分")
    reason = Column(Text, comment="信号理由")
    ai_verdict = Column(String(10), comment="AI研判: 买入/卖出/观望")
    ai_advice = Column(Text, comment="AI综合研判建议(含操作策略)")
    is_executed = Column(Boolean, default=False, comment="是否已执行")
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index("idx_signal_date", "signal_date"),
        Index("idx_signal_code", "code"),
        Index("idx_signal_type_date", "signal_type", "signal_date"),
    )


# ============================================================
# 自学习层 - 反馈与参数快照
# ============================================================

class SignalOutcome(Base):
    """交易信号回放结果（用于自学习评估）。"""
    __tablename__ = "signal_outcome"

    id = Column(Integer, primary_key=True, autoincrement=True)
    signal_id = Column(Integer, comment="关联的 trade_signal.id（可为空）")
    signal_date = Column(String(10), nullable=False, comment="信号日期")
    code = Column(String(10), nullable=False, comment="股票代码")
    signal_type = Column(String(20), nullable=False, comment="信号类型: premarket/buy/sell/hold")
    source = Column(String(20), comment="信号来源: 热点驱动/涨停板/全市场/综合评分")
    predict_type = Column(String(60), comment="走势类型/题材")
    confidence = Column(Float, comment="信心分 1-10")
    realized_change_pct = Column(Float, comment="实际涨跌幅（%）")
    hit_limit_up = Column(Boolean, default=False, comment="是否涨停")
    outcome_score = Column(Float, comment="回放评分 0-100")
    evaluated_at = Column(DateTime, default=datetime.now, comment="评估时间")
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index("idx_outcome_signal_id", "signal_id", unique=True),
        Index("idx_outcome_date_type", "signal_date", "signal_type"),
        Index("idx_outcome_source", "source"),
    )


class LearningSnapshot(Base):
    """每日自学习结果快照（权重/来源置信度/统计）。"""
    __tablename__ = "learning_snapshot"

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_date = Column(String(10), nullable=False, comment="快照日期")
    lookback_days = Column(Integer, default=20, comment="回看天数")
    sample_count = Column(Integer, default=0, comment="样本数量")
    adaptive_weights = Column(Text, comment="自适应权重 JSON")
    source_confidence = Column(Text, comment="来源置信度 JSON")
    factor_stats = Column(Text, comment="因子统计 JSON")
    notes = Column(String(300), comment="备注")
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index("idx_learning_snapshot_date", "snapshot_date", unique=True),
    )


# ============================================================
# 交易执行层 - 订单/成交/持仓/事件
# ============================================================

class TradeOrder(Base):
    """标准化订单（交易执行层）。"""
    __tablename__ = "trade_order"

    id = Column(String(40), primary_key=True, comment="订单ID(UUID)")
    signal_id = Column(Integer, comment="关联 trade_signal.id")
    signal_date = Column(String(10), comment="信号日期 YYYY-MM-DD")
    code = Column(String(10), nullable=False, comment="股票代码")
    name = Column(String(30), comment="股票名称")
    side = Column(String(10), nullable=False, comment="方向: buy/sell")
    order_type = Column(String(20), default="limit", comment="订单类型: limit/market")
    price = Column(Float, nullable=False, default=0, comment="委托价格")
    quantity = Column(Integer, nullable=False, default=0, comment="委托数量(股)")
    filled_quantity = Column(Integer, default=0, comment="已成交数量(股)")
    avg_fill_price = Column(Float, default=0, comment="成交均价")
    amount = Column(Float, default=0, comment="委托金额")
    status = Column(
        String(30),
        nullable=False,
        default="PENDING_CONFIRM",
        comment="订单状态: PENDING_CONFIRM/SUBMITTED/PARTIAL/FILLED/CANCELED/REJECTED/FAILED",
    )
    broker = Column(String(30), default="paper", comment="执行通道")
    broker_order_id = Column(String(80), comment="券商订单ID")
    strategy_tag = Column(String(50), default="", comment="策略来源标记")
    idempotency_key = Column(String(120), nullable=False, comment="幂等键")
    risk_note = Column(String(500), default="", comment="风控说明")
    error_code = Column(String(50), default="", comment="错误码")
    error_msg = Column(String(500), default="", comment="错误信息")
    confirmed_by = Column(String(50), default="", comment="确认下单操作人")
    confirmed_at = Column(DateTime, comment="确认时间")
    submitted_at = Column(DateTime, comment="提交到券商时间")
    finished_at = Column(DateTime, comment="终态时间")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index("idx_trade_order_status", "status"),
        Index("idx_trade_order_code", "code"),
        Index("idx_trade_order_signal_date", "signal_date"),
        Index("idx_trade_order_idem", "idempotency_key", unique=True),
    )


class TradeFill(Base):
    """成交明细。"""
    __tablename__ = "trade_fill"

    id = Column(Integer, primary_key=True, autoincrement=True)
    order_id = Column(String(40), nullable=False, comment="关联 trade_order.id")
    broker_fill_id = Column(String(80), default="", comment="券商成交ID")
    code = Column(String(10), nullable=False, comment="股票代码")
    side = Column(String(10), nullable=False, comment="buy/sell")
    price = Column(Float, nullable=False, default=0, comment="成交价")
    quantity = Column(Integer, nullable=False, default=0, comment="成交数量")
    amount = Column(Float, default=0, comment="成交金额")
    filled_at = Column(DateTime, default=datetime.now, comment="成交时间")
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index("idx_trade_fill_order", "order_id"),
        Index("idx_trade_fill_code_time", "code", "filled_at"),
        Index("idx_trade_fill_broker_fill_id", "broker_fill_id"),
    )


class PositionSnapshot(Base):
    """持仓快照。"""
    __tablename__ = "position_snapshot"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(10), nullable=False, comment="股票代码")
    name = Column(String(30), default="", comment="股票名称")
    quantity = Column(Integer, default=0, comment="总持仓")
    available_quantity = Column(Integer, default=0, comment="可卖数量")
    avg_cost = Column(Float, default=0, comment="持仓成本")
    market_price = Column(Float, default=0, comment="最新价")
    market_value = Column(Float, default=0, comment="市值")
    unrealized_pnl = Column(Float, default=0, comment="浮动盈亏")
    source = Column(String(30), default="paper", comment="来源通道")
    snapshot_at = Column(DateTime, default=datetime.now, comment="快照时间")
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index("idx_position_snapshot_code_time", "code", "snapshot_at"),
        Index("idx_position_snapshot_source_time", "source", "snapshot_at"),
    )


class ExecutionEvent(Base):
    """交易执行审计事件。"""
    __tablename__ = "execution_event"

    id = Column(Integer, primary_key=True, autoincrement=True)
    order_id = Column(String(40), nullable=False, comment="关联 trade_order.id")
    event_type = Column(String(40), nullable=False, comment="事件类型")
    payload_json = Column(Text, default="{}", comment="事件负载JSON")
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index("idx_execution_event_order_time", "order_id", "created_at"),
        Index("idx_execution_event_type", "event_type"),
    )
