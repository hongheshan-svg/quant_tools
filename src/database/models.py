""""
鏁版嵁搴撴ā鍨嬪畾涔?- SQLAlchemy ORM
瑕嗙洊锛氱儹鎼溿€佽储缁忔柊闂汇€佽鎯呮暟鎹€佹定鍋滄澘銆丄I鍒嗘瀽缁撴灉銆佷氦鏄撲俊鍙枫€佸浗闄呮暟鎹瓑
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
    """ORM 鍩虹被"""
    pass


# ============================================================
# 鏁版嵁閲囬泦灞?- 鑸嗘儏鏁版嵁
# ============================================================

class HotSearch(Base):
    """鐑悳鏁版嵁锛堝井鍗?鎶栭煶/澶存潯缁熶竴瀛樺偍锛?"""
    __tablename__ = "hot_search"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(String(20), nullable=False, comment="鏉ユ簮: weibo/douyin/toutiao")
    title = Column(String(500), nullable=False, comment="鐑悳鏍囬")
    rank = Column(Integer, comment="鎺掑悕")
    hot_value = Column(Integer, comment="鐑害鍊?")
    category = Column(String(50), comment="鍒嗙被")
    url = Column(String(1000), comment="閾炬帴")
    collected_at = Column(DateTime, default=datetime.now, comment="閲囬泦鏃堕棿")
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index("idx_hotsearch_source_time", "source", "collected_at"),
        Index("idx_hotsearch_title", "title"),
    )


class FinanceNews(Base):
    """璐㈢粡鏂伴椈锛堣储鑱旂ぞ绛夛級"""
    __tablename__ = "finance_news"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(String(20), nullable=False, comment="鏉ユ簮: cailianshe/xueqiu/jiuyan")
    title = Column(String(500), nullable=False, comment="鏍囬")
    content = Column(Text, comment="鍐呭鎽樿")
    news_time = Column(DateTime, comment="鏂伴椈鍙戝竷鏃堕棿")
    category = Column(String(50), comment="鍒嗙被: 蹇/鐮旀姤/澶嶇洏绛?")
    tags = Column(String(500), comment="鏍囩锛岄€楀彿鍒嗛殧")
    url = Column(String(1000), comment="閾炬帴")
    collected_at = Column(DateTime, default=datetime.now)
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index("idx_news_source_time", "source", "news_time"),
        Index("idx_news_collected_at", "collected_at"),
        Index("idx_news_source_collected", "source", "collected_at"),
    )


# ============================================================
# 鏁版嵁閲囬泦灞?- 琛屾儏鏁版嵁
# ============================================================

class StockDaily(Base):
    """涓偂鏃ョ嚎琛屾儏"""
    __tablename__ = "stock_daily"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(10), nullable=False, comment="鑲＄エ浠ｇ爜")
    name = Column(String(20), comment="鑲＄エ鍚嶇О")
    trade_date = Column(String(10), nullable=False, comment="浜ゆ槗鏃?YYYY-MM-DD")
    open = Column(Float, comment="寮€鐩樹环")
    close = Column(Float, comment="鏀剁洏浠?")
    high = Column(Float, comment="鏈€楂樹环")
    low = Column(Float, comment="鏈€浣庝环")
    volume = Column(Float, comment="鎴愪氦閲忥紙鎵嬶級")
    amount = Column(Float, comment="鎴愪氦棰濓紙鍏冿級")
    change_pct = Column(Float, comment="娑ㄨ穼骞?%")
    turnover = Column(Float, comment="鎹㈡墜鐜?%")
    total_mv = Column(Float, comment="鎬诲競鍊硷紙鍏冿級")
    circ_mv = Column(Float, comment="娴侀€氬競鍊硷紙鍏冿級")
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment="鏈€鍚庢洿鏂版椂闂?")

    __table_args__ = (
        Index("idx_daily_code_date", "code", "trade_date", unique=True),
    )


class LimitUpStock(Base):
    """娑ㄥ仠鏉挎暟鎹?"""
    __tablename__ = "limit_up_stock"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(10), nullable=False, comment="鑲＄エ浠ｇ爜")
    name = Column(String(20), comment="鑲＄エ鍚嶇О")
    trade_date = Column(String(10), nullable=False, comment="浜ゆ槗鏃?")
    close = Column(Float, comment="鏀剁洏浠?")
    change_pct = Column(Float, comment="娑ㄨ穼骞?%")
    limit_up_type = Column(String(20), comment="娑ㄥ仠绫诲瀷: 棣栨澘/杩炴澘")
    continuous_days = Column(Integer, default=1, comment="杩炴澘澶╂暟")
    seal_amount = Column(Float, comment="灏佸崟閲戦锛堝厓锛?")
    seal_ratio = Column(Float, comment="灏佸崟鍗犳瘮锛堝皝鍗?娴侀€氬競鍊硷級")
    first_limit_time = Column(String(10), comment="棣栨娑ㄥ仠鏃堕棿 HH:MM")
    last_limit_time = Column(String(10), comment="鏈€鍚庢定鍋滄椂闂?HH:MM")
    open_count = Column(Integer, default=0, comment="鎵撳紑娑ㄥ仠娆℃暟")
    sector = Column(String(200), comment="鎵€灞炴澘鍧?")
    reason = Column(String(500), comment="娑ㄥ仠鍘熷洜/棰樻潗")
    circ_mv = Column(Float, comment="娴侀€氬競鍊硷紙鍏冿級")
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index("idx_limitup_code_date", "code", "trade_date", unique=True),
        Index("idx_limitup_date", "trade_date"),
    )


class DragonTigerBoard(Base):
    """榫欒檸姒滄暟鎹?"""
    __tablename__ = "dragon_tiger_board"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(10), nullable=False, comment="鑲＄エ浠ｇ爜")
    name = Column(String(20), comment="鑲＄エ鍚嶇О")
    trade_date = Column(String(10), nullable=False, comment="浜ゆ槗鏃?")
    reason = Column(String(200), comment="涓婃鍘熷洜")
    buy_seat = Column(Text, comment="涔板叆甯綅JSON")
    sell_seat = Column(Text, comment="鍗栧嚭甯綅JSON")
    buy_total = Column(Float, comment="涔板叆鎬婚")
    sell_total = Column(Float, comment="鍗栧嚭鎬婚")
    net_amount = Column(Float, comment="鍑€涔板叆棰?")
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index("idx_dragon_code_date", "code", "trade_date"),
    )


class NorthboundFlow(Base):
    """鍖楀悜璧勯噾娴佸悜"""
    __tablename__ = "northbound_flow"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_date = Column(String(10), nullable=False, comment="浜ゆ槗鏃?")
    sh_net_inflow = Column(Float, comment="娌偂閫氬噣娴佸叆锛堜嚎鍏冿級")
    sz_net_inflow = Column(Float, comment="娣辫偂閫氬噣娴佸叆锛堜嚎鍏冿級")
    total_net_inflow = Column(Float, comment="鍚堣鍑€娴佸叆锛堜嚎鍏冿級")
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index("idx_northbound_date", "trade_date", unique=True),
    )


# ============================================================
# 鏁版嵁閲囬泦灞?- 鍥介檯鏁版嵁
# ============================================================

class USStockEarnings(Base):
    """缇庤偂澶撮儴鍏徃璐㈡姤鏁版嵁"""
    __tablename__ = "us_stock_earnings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(10), nullable=False, comment="缇庤偂浠ｇ爜: NVDA/AAPL绛?")
    company_name = Column(String(50), comment="鍏徃鍚嶇О")
    report_date = Column(String(10), nullable=False, comment="璐㈡姤鍙戝竷鏃ユ湡")
    quarter = Column(String(10), comment="瀛ｅ害: Q1/Q2/Q3/Q4")
    revenue = Column(Float, comment="钀ユ敹锛堜嚎缇庡厓锛?")
    revenue_estimate = Column(Float, comment="钀ユ敹棰勬湡")
    revenue_surprise_pct = Column(Float, comment="钀ユ敹瓒呴鏈?%")
    eps = Column(Float, comment="姣忚偂鏀剁泭")
    eps_estimate = Column(Float, comment="EPS棰勬湡")
    eps_surprise_pct = Column(Float, comment="EPS瓒呴鏈?%")
    guidance = Column(Text, comment="涓氱哗鎸囧紩鎽樿")
    after_hours_change_pct = Column(Float, comment="鐩樺悗娑ㄨ穼骞?%")
    key_highlights = Column(Text, comment="璐㈡姤鐢佃瘽浼氬叧閿鐐?")
    a_share_impact = Column(Text, comment="瀵笰鑲″奖鍝嶅垎鏋怞SON")
    collected_at = Column(DateTime, default=datetime.now)
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index("idx_earnings_symbol_date", "symbol", "report_date"),
    )


class GlobalNews(Base):
    """鍥介檯閲嶅ぇ鏂伴椈/浜嬩欢"""
    __tablename__ = "global_news"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(String(30), nullable=False, comment="鏉ユ簮: cailianshe_global/akshare")
    title = Column(String(500), nullable=False, comment="鏍囬")
    content = Column(Text, comment="鍐呭鎽樿")
    category = Column(String(50), comment="鍒嗙被: 缇庤仈鍌?缁忔祹鏁版嵁/鍦扮紭鏀挎不/澶у畻鍟嗗搧")
    news_time = Column(DateTime, comment="鏂伴椈鏃堕棿")
    importance = Column(Integer, default=5, comment="閲嶈鎬?1-10")
    url = Column(String(1000), comment="鏂伴椈閾炬帴")
    a_share_impact_direction = Column(String(10), comment="瀵笰鑲″奖鍝嶆柟鍚? 鍒╁ソ/鍒╃┖/涓€?")
    a_share_impact_sectors = Column(Text, comment="褰卞搷鏉垮潡JSON")
    collected_at = Column(DateTime, default=datetime.now)
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index("idx_globalnews_time", "news_time"),
        Index("idx_globalnews_category", "category"),
        Index("idx_globalnews_collected_at", "collected_at"),
    )


class USMarketDaily(Base):
    """闅斿缇庤偂琛ㄧ幇"""
    __tablename__ = "us_market_daily"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_date = Column(String(10), nullable=False, comment="浜ゆ槗鏃?")
    dow_jones_close = Column(Float, comment="閬撶惣鏂敹鐩?")
    dow_jones_change_pct = Column(Float, comment="閬撶惣鏂定璺屽箙 %")
    nasdaq_close = Column(Float, comment="绾虫柉杈惧厠鏀剁洏")
    nasdaq_change_pct = Column(Float, comment="绾虫柉杈惧厠娑ㄨ穼骞?%")
    sp500_close = Column(Float, comment="鏍囨櫘500鏀剁洏")
    sp500_change_pct = Column(Float, comment="鏍囨櫘500娑ㄨ穼骞?%")
    china_concept_index = Column(Float, comment="涓鑲℃寚鏁?")
    china_concept_change_pct = Column(Float, comment="涓鑲℃定璺屽箙 %")
    vix = Column(Float, comment="鎭愭厡鎸囨暟VIX")
    oil_price = Column(Float, comment="WTI鍘熸补浠锋牸")
    gold_price = Column(Float, comment="榛勯噾浠锋牸")
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index("idx_usmarket_date", "trade_date", unique=True),
    )


# ============================================================
# AI 鍒嗘瀽缁撴灉
# ============================================================

class SentimentAnalysis(Base):
    """鑸嗘儏鍒嗘瀽缁撴灉"""
    __tablename__ = "sentiment_analysis"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_type = Column(String(20), nullable=False, comment="鏉ユ簮绫诲瀷: hot_search/finance_news/global_news")
    source_id = Column(Integer, comment="鏉ユ簮璁板綍ID")
    original_text = Column(Text, comment="鍘熷鏂囨湰")
    related_stock_code = Column(String(10), comment="鍏宠仈鑲＄エ浠ｇ爜")
    related_stock_name = Column(String(20), comment="鍏宠仈鑲＄エ鍚嶇О")
    related_sector = Column(String(100), comment="鍏宠仈鏉垮潡")
    sentiment = Column(String(10), comment="鎯呯华鏂瑰悜: bullish/bearish/neutral")
    impact_score = Column(Float, comment="褰卞搷鍔涜瘎鍒?1-10")
    duration_estimate = Column(String(20), comment="鎸佺画鏃堕棿棰勪及: 鐭湡/涓湡/闀挎湡")
    analysis_reason = Column(Text, comment="鍒嗘瀽鐞嗙敱")
    analyzed_at = Column(DateTime, default=datetime.now)
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index("idx_sentiment_stock", "related_stock_code"),
        Index("idx_sentiment_time", "analyzed_at"),
        Index("idx_sentiment_source_time", "source_type", "analyzed_at"),
    )


class GlobalImpactAnalysis(Base):
    """鍥介檯浜嬩欢瀵笰鑲″奖鍝嶅垎鏋愮粨鏋?"""
    __tablename__ = "global_impact_analysis"

    id = Column(Integer, primary_key=True, autoincrement=True)
    analysis_date = Column(String(10), nullable=False, comment="鍒嗘瀽鏃ユ湡")
    us_market_summary = Column(Text, comment="闅斿缇庤偂琛ㄧ幇鎽樿")
    earnings_summary = Column(Text, comment="璐㈡姤褰卞搷鎽樿")
    global_events_summary = Column(Text, comment="鍥介檯浜嬩欢鎽樿")
    overall_direction = Column(String(10), comment="鎬讳綋褰卞搷鏂瑰悜: bullish/bearish/neutral")
    overall_impact_score = Column(Float, comment="鎬讳綋褰卞搷绋嬪害 1-10")
    affected_sectors = Column(Text, comment="鍙楀奖鍝嶆澘鍧桱SON")
    benefited_stocks = Column(Text, comment="鍙楃泭涓偂JSON")
    hurt_stocks = Column(Text, comment="鍙楁崯涓偂JSON")
    analysis_detail = Column(Text, comment="璇︾粏鍒嗘瀽")
    analyzed_at = Column(DateTime, default=datetime.now)
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index("idx_globalimpact_date", "analysis_date", unique=True),
    )


# ============================================================
# 绛栫暐灞?- 璇勫垎涓庝俊鍙?
# ============================================================

class StockScore(Base):
    """涓偂缁煎悎璇勫垎"""
    __tablename__ = "stock_score"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(10), nullable=False, comment="鑲＄エ浠ｇ爜")
    name = Column(String(20), comment="鑲＄エ鍚嶇О")
    score_date = Column(String(10), nullable=False, comment="璇勫垎鏃ユ湡")
    sentiment_score = Column(Float, default=0, comment="鑸嗘儏璇勫垎")
    limit_up_score = Column(Float, default=0, comment="娑ㄥ仠杩炴澘璇勫垎")
    seal_strength_score = Column(Float, default=0, comment="灏佸崟寮哄害璇勫垎")
    sector_effect_score = Column(Float, default=0, comment="鏉垮潡鏁堝簲璇勫垎")
    capital_flow_score = Column(Float, default=0, comment="璧勯噾娴佸悜璇勫垎")
    technical_score = Column(Float, default=0, comment="鎶€鏈潰璇勫垎")
    market_emotion_score = Column(Float, default=0, comment="甯傚満鎯呯华璇勫垎")
    global_score = Column(Float, default=0, comment="鍥介檯鍥犲瓙璇勫垎")
    composite_score = Column(Float, default=0, comment="缁煎悎璇勫垎")
    rank = Column(Integer, comment="鎺掑悕")
    recommendation = Column(String(20), comment="寤鸿: strong_buy/buy/hold/avoid")
    reason = Column(Text, comment="鎺ㄨ崘鐞嗙敱")
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index("idx_score_date_rank", "score_date", "rank"),
        Index("idx_score_code_date", "code", "score_date", unique=True),
    )


class TradeSignal(Base):
    """浜ゆ槗淇″彿"""
    __tablename__ = "trade_signal"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(10), nullable=False, comment="鑲＄エ浠ｇ爜")
    name = Column(String(20), comment="鑲＄エ鍚嶇О")
    signal_date = Column(String(10), nullable=False, comment="淇″彿鏃ユ湡")
    signal_type = Column(String(10), nullable=False, comment="淇″彿绫诲瀷: buy/sell")
    signal_strength = Column(Float, comment="淇″彿寮哄害 0-1")
    target_price = Column(Float, comment="鐩爣浠?")
    stop_loss_price = Column(Float, comment="姝㈡崯浠?")
    composite_score = Column(Float, comment="缁煎悎璇勫垎")
    reason = Column(Text, comment="淇″彿鐞嗙敱")
    ai_verdict = Column(String(10), comment="AI鐮斿垽: 涔板叆/鍗栧嚭/瑙傛湜")
    ai_advice = Column(Text, comment="AI缁煎悎鐮斿垽寤鸿(鍚搷浣滅瓥鐣?")
    is_executed = Column(Boolean, default=False, comment="鏄惁宸叉墽琛?")
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index("idx_signal_date", "signal_date"),
        Index("idx_signal_code", "code"),
        Index("idx_signal_type_date", "signal_type", "signal_date"),
    )


# ============================================================
# 鑷涔犲眰 - 鍙嶉涓庡弬鏁板揩鐓?
# ============================================================

class SignalOutcome(Base):
    """浜ゆ槗淇″彿鍥炴斁缁撴灉锛堢敤浜庤嚜瀛︿範璇勪及锛夈€?"""
    __tablename__ = "signal_outcome"

    id = Column(Integer, primary_key=True, autoincrement=True)
    signal_id = Column(Integer, comment="鍏宠仈鐨?trade_signal.id锛堝彲涓虹┖锛?")
    signal_date = Column(String(10), nullable=False, comment="淇″彿鏃ユ湡")
    code = Column(String(10), nullable=False, comment="鑲＄エ浠ｇ爜")
    signal_type = Column(String(20), nullable=False, comment="淇″彿绫诲瀷: premarket/buy/sell/hold")
    source = Column(String(20), comment="淇″彿鏉ユ簮: 鐑偣椹卞姩/娑ㄥ仠鏉?鍏ㄥ競鍦?缁煎悎璇勫垎")
    predict_type = Column(String(60), comment="璧板娍绫诲瀷/棰樻潗")
    confidence = Column(Float, comment="淇″績鍒?1-10")
    realized_change_pct = Column(Float, comment="瀹為檯娑ㄨ穼骞咃紙%锛?")
    hit_limit_up = Column(Boolean, default=False, comment="鏄惁娑ㄥ仠")
    outcome_score = Column(Float, comment="鍥炴斁璇勫垎 0-100")
    evaluated_at = Column(DateTime, default=datetime.now, comment="璇勪及鏃堕棿")
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index("idx_outcome_signal_id", "signal_id", unique=True),
        Index("idx_outcome_date_type", "signal_date", "signal_type"),
        Index("idx_outcome_source", "source"),
    )


class LearningSnapshot(Base):
    """姣忔棩鑷涔犵粨鏋滃揩鐓э紙鏉冮噸/鏉ユ簮缃俊搴?缁熻锛夈€?"""
    __tablename__ = "learning_snapshot"

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_date = Column(String(10), nullable=False, comment="蹇収鏃ユ湡")
    lookback_days = Column(Integer, default=20, comment="鍥炵湅澶╂暟")
    sample_count = Column(Integer, default=0, comment="鏍锋湰鏁伴噺")
    adaptive_weights = Column(Text, comment="鑷€傚簲鏉冮噸 JSON")
    source_confidence = Column(Text, comment="鏉ユ簮缃俊搴?JSON")
    factor_stats = Column(Text, comment="鍥犲瓙缁熻 JSON")
    notes = Column(String(300), comment="澶囨敞")
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index("idx_learning_snapshot_date", "snapshot_date", unique=True),
    )


# ============================================================
# 浜ゆ槗鎵ц灞?- 璁㈠崟/鎴愪氦/鎸佷粨/浜嬩欢
# ============================================================

class TradeOrder(Base):
    """鏍囧噯鍖栬鍗曪紙浜ゆ槗鎵ц灞傦級銆?"""
    __tablename__ = "trade_order"

    id = Column(String(40), primary_key=True, comment="璁㈠崟ID(UUID)")
    signal_id = Column(Integer, comment="鍏宠仈 trade_signal.id")
    signal_date = Column(String(10), comment="淇″彿鏃ユ湡 YYYY-MM-DD")
    code = Column(String(10), nullable=False, comment="鑲＄エ浠ｇ爜")
    name = Column(String(30), comment="鑲＄エ鍚嶇О")
    side = Column(String(10), nullable=False, comment="鏂瑰悜: buy/sell")
    order_type = Column(String(20), default="limit", comment="璁㈠崟绫诲瀷: limit/market")
    price = Column(Float, nullable=False, default=0, comment="濮旀墭浠锋牸")
    quantity = Column(Integer, nullable=False, default=0, comment="濮旀墭鏁伴噺(鑲?")
    filled_quantity = Column(Integer, default=0, comment="宸叉垚浜ゆ暟閲?鑲?")
    avg_fill_price = Column(Float, default=0, comment="鎴愪氦鍧囦环")
    amount = Column(Float, default=0, comment="濮旀墭閲戦")
    status = Column(
        String(30),
        nullable=False,
        default="PENDING_CONFIRM",
        comment="璁㈠崟鐘舵€? PENDING_CONFIRM/SUBMITTED/PARTIAL/FILLED/CANCELED/REJECTED/FAILED",
    )
    broker = Column(String(30), default="paper", comment="鎵ц閫氶亾")
    broker_order_id = Column(String(80), comment="鍒稿晢璁㈠崟ID")
    strategy_tag = Column(String(50), default="", comment="绛栫暐鏉ユ簮鏍囪")
    idempotency_key = Column(String(120), nullable=False, comment="骞傜瓑閿?")
    risk_note = Column(String(500), default="", comment="椋庢帶璇存槑")
    error_code = Column(String(50), default="", comment="閿欒鐮?")
    error_msg = Column(String(500), default="", comment="閿欒淇℃伅")
    confirmed_by = Column(String(50), default="", comment="纭涓嬪崟鎿嶄綔浜?")
    confirmed_at = Column(DateTime, comment="纭鏃堕棿")
    submitted_at = Column(DateTime, comment="鎻愪氦鍒板埜鍟嗘椂闂?")
    finished_at = Column(DateTime, comment="缁堟€佹椂闂?")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index("idx_trade_order_status", "status"),
        Index("idx_trade_order_code", "code"),
        Index("idx_trade_order_signal_date", "signal_date"),
        Index("idx_trade_order_idem", "idempotency_key", unique=True),
    )


class TradeFill(Base):
    """鎴愪氦鏄庣粏銆?"""
    __tablename__ = "trade_fill"

    id = Column(Integer, primary_key=True, autoincrement=True)
    order_id = Column(String(40), nullable=False, comment="鍏宠仈 trade_order.id")
    broker_fill_id = Column(String(80), default="", comment="鍒稿晢鎴愪氦ID")
    code = Column(String(10), nullable=False, comment="鑲＄エ浠ｇ爜")
    side = Column(String(10), nullable=False, comment="buy/sell")
    price = Column(Float, nullable=False, default=0, comment="鎴愪氦浠?")
    quantity = Column(Integer, nullable=False, default=0, comment="鎴愪氦鏁伴噺")
    amount = Column(Float, default=0, comment="鎴愪氦閲戦")
    filled_at = Column(DateTime, default=datetime.now, comment="鎴愪氦鏃堕棿")
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index("idx_trade_fill_order", "order_id"),
        Index("idx_trade_fill_code_time", "code", "filled_at"),
        Index("idx_trade_fill_broker_fill_id", "broker_fill_id"),
    )


class PositionSnapshot(Base):
    """鎸佷粨蹇収銆?"""
    __tablename__ = "position_snapshot"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(10), nullable=False, comment="鑲＄エ浠ｇ爜")
    name = Column(String(30), default="", comment="鑲＄エ鍚嶇О")
    quantity = Column(Integer, default=0, comment="鎬绘寔浠?")
    available_quantity = Column(Integer, default=0, comment="鍙崠鏁伴噺")
    avg_cost = Column(Float, default=0, comment="鎸佷粨鎴愭湰")
    market_price = Column(Float, default=0, comment="鏈€鏂颁环")
    market_value = Column(Float, default=0, comment="甯傚€?")
    unrealized_pnl = Column(Float, default=0, comment="娴姩鐩堜簭")
    source = Column(String(30), default="paper", comment="鏉ユ簮閫氶亾")
    snapshot_at = Column(DateTime, default=datetime.now, comment="蹇収鏃堕棿")
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index("idx_position_snapshot_code_time", "code", "snapshot_at"),
        Index("idx_position_snapshot_source_time", "source", "snapshot_at"),
    )


class ExecutionEvent(Base):
    """浜ゆ槗鎵ц瀹¤浜嬩欢銆?"""
    __tablename__ = "execution_event"

    id = Column(Integer, primary_key=True, autoincrement=True)
    order_id = Column(String(40), nullable=False, comment="鍏宠仈 trade_order.id")
    event_type = Column(String(40), nullable=False, comment="浜嬩欢绫诲瀷")
    payload_json = Column(Text, default="{}", comment="浜嬩欢璐熻浇JSON")
    created_at = Column(DateTime, default=datetime.now)

    __table_args__ = (
        Index("idx_execution_event_order_time", "order_id", "created_at"),
        Index("idx_execution_event_type", "event_type"),
    )
