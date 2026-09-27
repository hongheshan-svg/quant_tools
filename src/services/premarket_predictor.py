"""
实时涨停预测服务。
根据当前时间和所有可用数据，持续预测最可能涨停的10只股票。
- 盘前(9:00前)：基于昨日涨停板 + 隔夜新闻 + 热点
- 早盘(9:30-11:30)：基于今日实时涨停 + 盘中资金 + 实时新闻
- 午盘(13:00-15:00)：基于今日盘中走势 + 涨停封板情况 + 尾盘预判
- 盘后(15:00后)：基于今日涨停复盘 + 预测次日延续
"""

from __future__ import annotations

from contextlib import suppress
from datetime import date, datetime, timedelta

from loguru import logger

from src.analyzers.llm_client import LLMClient
from src.config_loader import load_config
from src.database.db import get_db_session
from src.database.models import (
    FinanceNews,
    LimitUpStock,
    SentimentAnalysis,
    StockDaily,
    StockScore,
    TradeSignal,
)

# ---- 根据时段切换的 Prompt ----

PREDICT_PROMPT = """你是A股短线实战专家，擅长从新闻热点、市场数据中挖掘交易机会。{predict_target}

当前时段: {session_desc}

**选股思路**（核心原则：热点驱动 + 数据验证）：
- **第一维度：热点新闻驱动**（最重要！）
  政策利好、行业重大事件、财联社红色新闻 → 找到受益行业的龙头个股
  例如："国务院发文推动算力" → AI算力板块 → 中科曙光/浪潮信息
- **第二维度：多平台共振热点**
  同花顺热股 + 东方财富热门概念 + 财联社红色新闻同时指向同一主题 → 该主题大概率持续
  跨平台共振的主题优先推荐！
- **第三维度：涨停板数据验证**
  涨停板作为市场已确认的强势方向（非唯一选股来源），验证热点是否已有资金进场
  连板股代表市场公认的主线方向，可作为参考
- **第四维度：全市场强势股补充**
  涨幅较高但未涨停的个股，可能{next_day}冲板或继续走强

分析维度：
1. **热点新闻+AI舆情**：财联社红色新闻中的利好个股、AI舆情看多标的（impact>=7优先）
2. **跨平台共振**：同花顺热股+东方财富热门概念+财联社同时关注的主题，找其龙头股
3. **技术面分析（重要！）**：
   - ★ 优先选择**上升通道**的股票（多头排列：价格>MA5>MA10>MA20）
   - 同板块中优先选技术形态好的（上升通道>震荡偏强>震荡整理）
   - 回避**下降通道**的股票（空头排列），即使有新闻利好也要谨慎
   - 关注"突破MA20"、"20日新高"、"放量上攻"等技术信号
   - 量价配合好的标的优先（放量上攻 > 缩量上涨）
4. **涨停板参考**：连板股高度印证主线方向、首板质量验证资金态度
5. **全市场强势股**：涨幅较高未涨停的股票（{next_day}可能冲板）
6. **板块联动**：同板块涨停家数、龙头地位、板块资金流入
7. **市场环境**：大盘成交量、涨跌家数、市场情绪
8. **隔夜美股/国际因子**：三大指数、VIX恐慌指数、中概股表现、头部美股财报（英伟达/苹果/特斯拉等），注意美股AI/半导体强势→A股算力映射
9. **大宗商品/汇率**：原油、黄金、人民币汇率异动对相关板块的传导
{extra_rules}

**predict_type 字段要求**（精准描述走势驱动因素）：
- 必须体现具体题材/板块/事件，不要笼统模板词
- 示例: "算力政策利好龙头" / "AI大模型概念首板" / "贵金属涨价受益" / "多平台共振+文化传媒" / "新闻联播政策+航天军工"

**source 字段要求**：
- "热点驱动" = 来自新闻/政策/多平台热点驱动的标的（应占比最多！）
- "涨停板" = 来自涨停板数据中的标的
- "全市场" = 来自全市场强势股扫描的标的

选股规则：
- 剔除一字板（开盘即涨停无法买入）
- 剔除ST/*ST/退市风险股
- ★ **必须参考技术面数据**：同板块同题材中，优先选上升通道的股票，回避下降通道的
- 热点驱动的标的优先推荐板块龙头和市值适中的个股
- 涨停板标的优先低位首板和2-3连板（高位追涨风险大）
- 全市场标的优先涨幅7-9%且换手率合理的（{next_day}冲板概率高）
- 如果热点里有多只相关个股，选技术形态最好的那只（多头排列优先）

价格计划（buy_price / stop_loss / target_price）：
- 必须以数据中给出的最新价/收盘价为基准，买入价不能超出{next_day}涨跌停范围
- 止损价低于买入价（一般下方 3%~8%，参考关键均线或涨停价位），目标价高于买入价（一般上方 5%~20%）
- 数据中没有该股价格或无法判断时填 null，不要编造

返回 JSON 格式：
{{
  "market_outlook": "{predict_horizon}大盘研判(30字以内)",
  "main_theme": "{predict_horizon}主线题材(20字以内)",
  "predictions": [
    {{
      "code": "股票代码(6位)",
      "name": "股票名称",
      "source": "热点驱动/涨停板/全市场",
      "predict_type": "具体走势驱动类型(15字以内)",
      "confidence": 1到10的整数(10=最高信心),
      "target_time": "{next_day}操作建议(如：明日集合竞价低吸/明日回封确认/明日尾盘潜伏)",
      "reason": "{next_day}核心逻辑(40字以内)",
      "risk": "主要风险(20字以内)",
      "buy_price": 建议买入价(数字或null),
      "stop_loss": 止损价(数字或null),
      "target_price": 目标价(数字或null)
    }}
  ]
}}
按信心从高到低排序，最多10只。来源分布：热点驱动至少4只、涨停板至少2只、全市场至少1只。仅返回 JSON。"""

SESSION_RULES = {
    "premarket": (
        "盘前预测 → 预测今日机会",
        """
- 【核心目标】基于隔夜重大新闻 + 多平台热点共振 + 前一交易日数据，预测今日最值得关注的股票
- 你的所有预测和建议都是针对今天开盘后的操作！
- ★ 优先从热点新闻中寻找今日受益行业龙头（政策利好、行业突发等）
- ★ 多平台共振主题（同花顺+东财+财联社同时关注）优先级最高
- 前一交易日涨停板验证市场方向：连板晋级概率（2→3连板最高）
- 隔夜美股联动→今日受益产业链标的（不限于涨停板）
- 盘前无实时行情，新闻和热点驱动是核心信号"""
    ),
    "morning": (
        "早盘实时（9:30-11:30）",
        """
- ★ 结合盘中最新快讯和热点异动，发现新闻驱动的个股机会
- 重点关注今日已涨停的封板质量（是否回封、封单大小）
- **全市场强势股冲板**：关注涨幅7%+强势股
- 板块联动中尚未涨停但跟风强势的标的
- 盘中突发新闻可能催化的新方向"""
    ),
    "noon": (
        "午间休市",
        """
- 复盘上午走势，分析封板质量
- ★ 结合午间新闻热点，寻找下午可能异动的潜伏标的
- 预判下午可能涨停的补涨标的
- 关注午间新闻可能带来的题材异动"""
    ),
    "afternoon": (
        "午盘实时（13:00-15:00）",
        """
- 重点关注尾盘封板确认的标的
- **全市场冲板机会**：涨幅8%+的强势股可能尾盘封板
- 今日涨停板中封板最稳的标的次日预判
- ★ 结合下午新闻，判断板块持续性（是否明日还能延续）"""
    ),
    "aftermarket": (
        "盘后复盘 → 预测明日机会",
        """
- 【核心目标】基于今日热点新闻 + 多平台共振 + 收盘数据，预测明日最值得关注的股票
- 你的所有预测和建议都是针对明天的操作，不是今天！
- ★ 优先从当日重大新闻中寻找明日延续或新发酵的行业龙头
- ★ 跨平台共振主题（同花顺+东财+财联社+AI舆情同时看好）优先级最高
- 涨停板复盘验证今日市场主线，判断明日是否延续
- **明日全市场潜伏**：今日涨幅5-8%、尾盘放量的标的可能明日冲板
- 结合晚间新闻判断题材明日持续性
- 不要只从涨停板里选股！新闻驱动和热点共振才是前瞻性信号"""
    ),
}

FAKE_LIMIT_UP_MIN_CHANGE = 5.0
MARKET_CRASH_THRESHOLD = -5.0
LIMIT_UP_LIKE_CHANGE_THRESHOLD = 9.5
MONEY_UNIT_CONVERT_THRESHOLD = 10000
SENTIMENT_MIN_IMPACT_SCORE = 6
MIN_THEME_NAME_LENGTH = 2
CONVERGENT_THEME_CANDIDATE_LIMIT = 20
CONVERGENT_THEME_MIN_COUNT = 2
PREFIXED_STOCK_CODE_LENGTH = 8
STOCK_CODE_LENGTH = 6
STRONG_STOCK_CHANGE_THRESHOLD = 8
STRONG_STOCK_TURNOVER_THRESHOLD = 15
STRONG_STOCK_AMOUNT_THRESHOLD = 10
VIX_PANIC_LEVEL = 30
VIX_WARNING_LEVEL = 25
VIX_ELEVATED_LEVEL = 20
TECH_MA20_PERIOD = 20
TECH_MIN_KLINE_POINTS = 10
TECH_SHORT_KLINE_POINTS = 6
TECH_MIN_BREAKOUT_POINTS = 2
TECH_STABLE_MA_DAYS = 3
VOL_EXPANSION_RATIO = 1.5
VOL_CONTRACTION_RATIO = 0.7
KLINE_PARTS_MIN_LENGTH = 6
HIGH_CONFIDENCE_THRESHOLD = 7
MEDIUM_CONFIDENCE_THRESHOLD = 4


class LimitUpPredictor:
    """实时涨停预测。根据当前时间自动选择数据和策略。"""

    def __init__(self, config: dict | None = None):
        self.config = config or load_config()
        self.llm = LLMClient(self.config.get("llm", {}))
        self.db_path = self.config.get("database", {}).get("sqlite_path", "data/quant.db")
        self.source_confidence = (
            self.config.get("strategy", {}).get("source_confidence", {}) or {}
        )

    def predict(self) -> list[dict]:
        """
        根据当前时间和所有可用数据预测涨停股。
        自动判断时段，选择合适的数据源和分析策略。
        盘后/盘前: 预测明日（下一个交易日）的机会
        盘中: 预测今日的机会
        """
        from src import trading_calendar

        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        trading_calendar.load(self.db_path)
        is_trade_day = trading_calendar.is_trade_day(now)
        # 非交易日按盘前模式：以最近交易日数据为主，预测下一个交易日
        session = self._get_session(now) if is_trade_day else "premarket"

        logger.info(f"涨停预测启动 | 时段: {session} | {now.strftime('%H:%M')}")

        # 1) 收集所有可用数据
        limit_up_info = self._get_limit_up_data(session)
        self._attach_technical(limit_up_info)
        market_strong_stocks = self._get_market_strong_stocks(session)
        news_context = self._get_recent_news()
        hot_context = self._get_jiuyan_hot()
        multi_hot_context = self._get_multi_source_hot_topics()
        news_driven_context = self._get_news_driven_candidates()  # 新增：热点驱动候选股
        market_context = self._get_market_overview_context()
        sentiment_context = self._get_sentiment_context()
        score_context = self._get_score_context()

        # 放宽数据检查：只要有新闻/热点/涨停板任一数据就可以预测
        has_limit_up = bool(limit_up_info["stocks"] or limit_up_info.get("today_stocks"))
        has_market = bool(market_strong_stocks)
        has_news = bool(news_context.strip() and news_context != "暂无重要新闻")
        has_hot = bool(news_driven_context.strip() or multi_hot_context.strip())
        if not has_limit_up and not has_market and not has_news and not has_hot:
            logger.warning("涨停预测: 数据不足，跳过")
            return []

        # 1.5) 对候选股做批量技术分析（MA均线+趋势判断）
        candidate_codes = self._collect_candidate_codes(
            limit_up_info, market_strong_stocks, news_driven_context,
        )
        logger.info(f"技术分析: 对 {len(candidate_codes)} 只候选股计算技术指标...")
        tech_context = self._get_technical_context(candidate_codes)

        # 2) 构建时段自适应 prompt
        #    盘前：基于前一天数据 → 预测今日
        #    盘中：基于实时数据   → 预测今日
        #    盘后：基于今天数据   → 预测明日
        session_desc, extra_rules = SESSION_RULES.get(session, SESSION_RULES["premarket"])
        if session == "aftermarket":
            predict_target = "根据今日收盘完整数据，预测明日（下一个交易日）最可能涨停的股票"
            next_day = "明日"
            predict_horizon = "明日"
        elif session == "premarket":
            predict_target = "根据前一交易日收盘数据和隔夜消息，预测今日最可能涨停的股票"
            next_day = "今日"
            predict_horizon = "今日"
        else:
            predict_target = "根据当前实时数据，预测今日最可能涨停的股票"
            next_day = "今日"
            predict_horizon = "今日"
        system_prompt = PREDICT_PROMPT.format(
            session_desc=session_desc,
            extra_rules=extra_rules,
            predict_target=predict_target,
            next_day=next_day,
            predict_horizon=predict_horizon,
        )

        # 3) 构建用户消息 —— 根据时段区分主/次数据集
        primary_section = ""
        reference_section = ""

        if limit_up_info.get("stocks"):
            stock_lines = self._format_stock_lines(limit_up_info["stocks"])
            # 主数据集标签
            if session == "aftermarket":
                label = "【重点分析】今日收盘涨停板（用于预测明日机会）"
            elif session == "premarket":
                label = "【重点分析】前一交易日涨停板（用于预测今日机会）"
            else:
                label = "最近涨停板"
            primary_section = (
                f"\n===== {label}({limit_up_info['date']}) "
                f"共{limit_up_info['total']}只 =====\n"
                f"连板股({limit_up_info['lianban_count']}只):\n"
                + "\n".join(line for line in stock_lines if "连板=1 " not in line)
                + f"\n首板股({limit_up_info['shouban_count']}只):\n"
                + "\n".join(line for line in stock_lines if "连板=1 " in line)[:1500]
            )

        # 盘中时段额外显示今日实时涨停
        if limit_up_info.get("today_stocks"):
            today_lines = self._format_stock_lines(limit_up_info["today_stocks"])
            reference_section += (
                f"\n===== 今日盘中涨停({limit_up_info.get('today_date','')}) "
                f"共{len(limit_up_info['today_stocks'])}只 =====\n"
                + "\n".join(today_lines)
            )

        # 盘后/盘前时段显示昨日数据作为参考
        if limit_up_info.get("prev_stocks"):
            prev_lines = self._format_stock_lines(limit_up_info["prev_stocks"])
            prev_lianban = [line for line in prev_lines if "连板=1 " not in line]
            reference_section += (
                f"\n===== 【参考】上一交易日涨停板({limit_up_info['prev_date']}) "
                f"共{limit_up_info['prev_total']}只 =====\n"
                f"连板股({limit_up_info['prev_lianban_count']}只):\n"
                + "\n".join(prev_lianban[:20])
                + f"\n首板股({limit_up_info['prev_shouban_count']}只): [略]"
            )

        # 4) 获取隔夜美股/国际因子
        us_context = self._get_us_market_context()
        global_impact_context = self._get_global_impact_context()

        user_message = (
            f"当前时间: {now.strftime('%Y-%m-%d %H:%M')} | 时段: {session_desc}\n\n"
            f"===== 大盘环境 =====\n{market_context}\n"
        )
        if us_context:
            user_message += f"\n===== 隔夜美股/全球市场 =====\n{us_context}\n"
        if global_impact_context:
            user_message += f"\n===== 国际因子AI分析 =====\n{global_impact_context[:600]}\n"

        # ★ 热点驱动候选股（最重要的选股信号，放在最前面）
        if news_driven_context:
            user_message += (
                f"\n\n========== ★ 热点驱动候选股（重点关注！）==========\n"
                f"以下是从新闻热点、AI舆情、多平台共振中提取的候选个股，"
                f"这些是{next_day}最值得关注的热点方向和标的：\n"
                f"{news_driven_context[:3000]}"
            )

        user_message += f"\n\n===== 财联社最新重要新闻 =====\n{news_context[:1500]}"
        if multi_hot_context:
            user_message += f"\n\n===== 多源热点综合（财联社红色+同花顺+东方财富） =====\n{multi_hot_context[:2000]}"

        # 涨停板数据（作为市场强势方向的验证参考）
        if primary_section:
            user_message += "\n\n===== 涨停板数据（验证市场方向） =====" + primary_section
        if reference_section:
            user_message += reference_section

        # 全市场强势股扫描数据
        if market_strong_stocks:
            strong_lines = self._format_strong_stock_lines(market_strong_stocks)
            user_message += (
                f"\n\n===== 全市场强势股（涨幅靠前、尚未涨停）共{len(market_strong_stocks)}只 =====\n"
                + "\n".join(strong_lines[:40])
            )

        user_message += f"\n\n===== 韭研公社/雪球热点 =====\n{hot_context[:800]}"
        if sentiment_context:
            user_message += f"\n\n===== AI舆情分析 =====\n{sentiment_context[:800]}"
        if score_context:
            user_message += f"\n\n===== 综合评分Top =====\n{score_context[:600]}"

        # ★ 技术面分析（候选股的均线趋势，帮助AI筛选上升通道标的）
        if tech_context:
            user_message += (
                f"\n\n========== ★ 技术面分析（选股必看！）==========\n"
                f"以下是候选股的技术指标，按趋势分类。"
                f"选股时必须参考技术面，同板块同题材优先选上升通道的股票：\n"
                f"{tech_context[:3000]}"
            )

        if session == "aftermarket":
            user_message += (
                "\n\n请基于以上数据，预测明日（下一个交易日）最值得关注的10只股票（剔除一字板和ST），按信心排序。"
                "\n★ 核心要求：你预测的是明天的机会！"
                "\n★ 技术面要求：同板块同题材中，优先选上升通道（多头排列）的股票，回避下降通道的！"
                "\n选股优先级：1) 热点新闻+多平台共振驱动的行业龙头股（至少4只）"
                "  2) 涨停板中明日连板晋级/溢价标的（至少2只）"
                "  3) 全市场强势股中明日冲板机会（至少1只）"
                "\ntarget_time字段请写明日的具体操作建议，如'明日集合竞价低吸'、'明日回封确认'、'明日尾盘潜伏'等。"
                "\nsource字段：热点驱动的写'热点驱动'，涨停板的写'涨停板'，全市场的写'全市场'。"
            )
        elif session == "premarket":
            user_message += (
                "\n\n请基于以上数据，预测今日最值得关注的10只股票（剔除一字板和ST），按信心排序。"
                "\n★ 核心要求：你预测的是今天的机会！"
                "\n★ 技术面要求：同板块同题材中，优先选上升通道（多头排列）的股票，回避下降通道的！"
                "\n选股优先级：1) 热点新闻+多平台共振驱动的行业龙头股（至少4只）"
                "  2) 涨停板中今日连板晋级/溢价标的（至少2只）"
                "  3) 全市场强势股中今日冲板机会（至少1只）"
                "\ntarget_time字段请写今日操作建议，如'集合竞价低吸'、'开盘回封确认'等。"
                "\nsource字段：热点驱动的写'热点驱动'，涨停板的写'涨停板'，全市场的写'全市场'。"
            )
        else:
            user_message += (
                "\n\n请预测今日最值得关注的10只股票（剔除一字板和ST），按信心排序。"
                "\n★ 选股不要局限于涨停板！优先从热点新闻和多平台共振中挖掘标的。"
                "\n★ 技术面要求：同板块中优先选上升通道的股票！"
                "\nsource字段：热点驱动的写'热点驱动'，涨停板的写'涨停板'，全市场的写'全市场'。"
            )

        logger.info(f"涨停预测: 发送数据到 DeepSeek（{session_desc}）...")
        result = self.llm.chat_json(
            user_message=user_message,
            system_message=system_prompt,
            max_tokens=4096,
        )

        predictions = result.get("predictions", [])
        market_outlook = result.get("market_outlook", "")
        main_theme = result.get("main_theme", "")

        if market_outlook:
            logger.info(f"AI预判: {market_outlook} | 主线: {main_theme}")

        if not predictions:
            logger.warning("涨停预测: DeepSeek 未返回有效结果")
            return []

        predictions = self._apply_source_confidence(predictions)

        # 4) 写入数据库
        #    盘后或非交易日：预测下一个交易日，signal_date 存为该交易日
        #    交易日盘前/盘中：预测今日，signal_date 存为今天
        target_date = today if is_trade_day and session != "aftermarket" else self._next_trade_date(now)
        self._save_predictions(target_date, predictions, market_outlook, main_theme)

        logger.info(f"涨停预测完成: {len(predictions)} 只标的 ({session_desc})")
        return predictions

    def _apply_source_confidence(self, predictions: list[dict]) -> list[dict]:
        """
        使用自学习来源置信度对 AI confidence 做轻量校准。
        source_confidence 例如: {"热点驱动": 1.08, "涨停板": 0.95}
        """
        if not predictions or not self.source_confidence:
            return predictions
        adjusted = []
        for p in predictions:
            source = (p.get("source") or "涨停板").strip()
            factor = float(self.source_confidence.get(source, 1.0))
            old_conf = int(p.get("confidence", 5) or 5)
            new_conf = int(round(old_conf * factor))
            new_conf = max(1, min(10, new_conf))
            p["confidence"] = new_conf
            adjusted.append(p)
            if new_conf != old_conf:
                logger.debug(
                    f"预测信心校准: {p.get('code','')} {source} {old_conf}->{new_conf} (x{factor:.3f})"
                )
        return adjusted

    # ---- 时段判断 ----

    @staticmethod
    def _get_session(now: datetime) -> str:
        h, m = now.hour, now.minute
        t = h * 60 + m
        if t < 9 * 60 + 25:
            return "premarket"
        if t <= 11 * 60 + 30:
            return "morning"
        if t < 13 * 60:
            return "noon"
        if t <= 15 * 60:
            return "afternoon"
        return "aftermarket"

    @staticmethod
    def _next_trade_date(now: datetime) -> str:
        """
        计算下一个交易日日期（按交易日历，跳过周末和法定节假日）。
        盘前(9:25前)且当天是交易日时返回今天，否则返回之后第一个交易日。
        """
        from src import trading_calendar

        t = now.hour * 60 + now.minute
        return trading_calendar.next_trade_day(now.date(), include_self=t < 9 * 60 + 25).strftime("%Y-%m-%d")

    # ---- 数据采集 ----

    def _get_limit_up_data(self, session: str) -> dict:
        """
        获取涨停板数据。
        盘后/盘前: 今日数据放入 stocks（主数据集），昨日放入 prev_stocks（参考）
        盘中: 今日数据放入 today_stocks，昨日放入 stocks
        """

        result = {
            "date": "", "total": 0, "lianban_count": 0, "shouban_count": 0,
            "stocks": [], "today_stocks": [], "today_date": "",
            "prev_stocks": [], "prev_date": "",
            "prev_lianban_count": 0, "prev_shouban_count": 0, "prev_total": 0,
        }
        today = date.today().strftime("%Y-%m-%d")
        try:
            with get_db_session(self.db_path) as session_db:
                # 找所有有涨停数据的日期
                dates = (
                    session_db.query(LimitUpStock.trade_date)
                    .distinct()
                    .order_by(LimitUpStock.trade_date.desc())
                    .limit(3)
                    .all()
                )
                date_list = [d[0] for d in dates]

                # ---------- 今日涨停数据（含交叉验证） ----------
                today_lu_list = []
                today_dicts = []
                today_lianban = 0
                today_shouban = 0
                if today in date_list:
                    today_lu_list = (
                        session_db.query(LimitUpStock)
                        .filter(LimitUpStock.trade_date == today)
                        .order_by(LimitUpStock.continuous_days.desc(),
                                  LimitUpStock.first_limit_time.asc())
                        .all()
                    )
                    # 构建 StockDaily 查找表（支持多种 code 格式匹配）
                    sd_rows = session_db.query(StockDaily).filter(
                        StockDaily.trade_date == today
                    ).all()
                    sd_today = {}
                    for r in sd_rows:
                        sd_today[r.code] = r  # e.g. "sh603598"
                        # 也存纯数字 code 作为备选 key
                        bare = r.code.lstrip("shzSHZbBjJ")
                        if bare:
                            sd_today[bare] = r
                    skipped = 0
                    for lu in today_lu_list:
                        daily = sd_today.get(lu.code)
                        # 交叉验证：用 StockDaily 实际涨跌幅排除假涨停
                        # 如果 StockDaily 显示实际涨幅 < 5%，说明涨停数据已过时
                        if daily and daily.change_pct is not None and daily.change_pct < FAKE_LIMIT_UP_MIN_CHANGE:
                            skipped += 1
                            logger.debug(
                                f"排除假涨停: {lu.name}({lu.code}) "
                                f"实际涨幅={daily.change_pct:.2f}%"
                            )
                            continue
                        d = self._lu_to_dict(lu, daily)
                        if d["continuous_days"] > 1:
                            today_lianban += 1
                        else:
                            today_shouban += 1
                        today_dicts.append(d)
                    if skipped:
                        logger.info(
                            f"涨停板交叉验证: 排除 {skipped} 只实际未涨停的股票 "
                            f"(保留 {len(today_dicts)} 只)"
                        )

                # ---------- 昨日/上一交易日涨停数据（含交叉验证） ----------
                prev_dates = [d for d in date_list if d != today]
                prev_dicts = []
                prev_date = ""
                prev_lianban = 0
                prev_shouban = 0
                if prev_dates:
                    prev_date = prev_dates[0]
                    lu_all = (
                        session_db.query(LimitUpStock)
                        .filter(LimitUpStock.trade_date == prev_date)
                        .order_by(LimitUpStock.continuous_days.desc(),
                                  LimitUpStock.first_limit_time.asc())
                        .all()
                    )
                    sd_prev_rows = session_db.query(StockDaily).filter(
                        StockDaily.trade_date == prev_date
                    ).all()
                    sd_prev = {}
                    for r in sd_prev_rows:
                        sd_prev[r.code] = r
                        bare = r.code.lstrip("shzSHZbBjJ")
                        if bare:
                            sd_prev[bare] = r
                    for lu in lu_all:
                        daily = sd_prev.get(lu.code)
                        # 交叉验证（同上逻辑）
                        if daily and daily.change_pct is not None and daily.change_pct < FAKE_LIMIT_UP_MIN_CHANGE:
                            continue
                        d = self._lu_to_dict(lu, daily)
                        if d["continuous_days"] > 1:
                            prev_lianban += 1
                        else:
                            prev_shouban += 1
                        prev_dicts.append(d)

                # ---------- 根据时段决定主/次数据集 ----------
                if session == "aftermarket" and today_lu_list:
                    # 盘后：今日收盘数据是主要分析对象 → 预测明日
                    result["date"] = today
                    result["total"] = len(today_dicts)
                    result["lianban_count"] = today_lianban
                    result["shouban_count"] = today_shouban
                    result["stocks"] = today_dicts
                    # 昨日作为参考
                    result["prev_date"] = prev_date
                    result["prev_total"] = len(prev_dicts)
                    result["prev_lianban_count"] = prev_lianban
                    result["prev_shouban_count"] = prev_shouban
                    result["prev_stocks"] = prev_dicts
                elif session == "premarket":
                    # 盘前：前一交易日数据是主要分析对象 → 预测今日
                    # 盘前时今日尚无交易数据，前一交易日就是 prev_dicts
                    result["date"] = prev_date
                    result["total"] = len(prev_dicts)
                    result["lianban_count"] = prev_lianban
                    result["shouban_count"] = prev_shouban
                    result["stocks"] = prev_dicts
                    # 更早的数据不再需要
                else:
                    # 盘中：昨日涨停为主数据集，今日实时为补充
                    result["today_date"] = today
                    result["today_stocks"] = today_dicts if today_lu_list else []
                    result["date"] = prev_date
                    result["total"] = len(prev_dicts)
                    result["lianban_count"] = prev_lianban
                    result["shouban_count"] = prev_shouban
                    result["stocks"] = prev_dicts

                if not result["stocks"] and not result.get("today_stocks"):
                    logger.debug("涨停预测: 无涨停数据可用")
        except Exception as e:
            logger.error(f"获取涨停数据失败: {e}")
        return result

    @staticmethod
    def _lu_to_dict(lu, daily=None) -> dict:
        from src.strategy.scorer import _is_yizi_ban
        circ_mv = lu.circ_mv or 0
        seal_amount = lu.seal_amount or 0
        return {
            "code": lu.code,
            "name": lu.name or "",
            "continuous_days": lu.continuous_days or 1,
            "sector": lu.sector or "",
            "reason": lu.reason or "",
            "first_time": lu.first_limit_time or "",
            "open_count": lu.open_count or 0,
            "seal_amount_yi": seal_amount / 1e8 if seal_amount > MONEY_UNIT_CONVERT_THRESHOLD else seal_amount,
            "circ_mv_yi": circ_mv / 1e8 if circ_mv > MONEY_UNIT_CONVERT_THRESHOLD else circ_mv,
            "is_yizi": _is_yizi_ban(lu, daily),
            "close": lu.close or 0,
        }

    def _attach_technical(self, limit_up_info: dict, max_stocks: int = 80) -> None:
        """给主要涨停数据集的个股附上一行技术面摘要（均线/MACD/RSI/乖离率/风险）。"""
        from src.strategy.tech_score import analyze_technical

        for key in ("stocks", "today_stocks"):
            for s in limit_up_info.get(key, [])[:max_stocks]:
                with suppress(Exception):
                    s["tech"] = analyze_technical(s["code"], self.db_path).brief()

    @staticmethod
    def _format_stock_lines(stocks: list[dict]) -> list[str]:
        lines = []
        for s in stocks:
            line = (
                f"{s['name']}({s['code']}) "
                f"连板={s['continuous_days']} 板块={s['sector']} "
                f"涨停原因={s['reason']} "
                f"封板时间={s['first_time']} 炸板={s['open_count']}次 "
                f"封单={s['seal_amount_yi']:.1f}亿 "
                f"流通市值={s['circ_mv_yi']:.1f}亿 "
                f"{'[一字板]' if s['is_yizi'] else ''}"
            )
            if s.get("close"):
                line += f" 收盘价={s['close']:.2f}"
            if s.get("tech"):
                line += f" 技术面={s['tech']}"
            lines.append(line)
        return lines

    def _get_recent_news(self) -> str:
        """获取最近的财联社重要/红色新闻。"""
        lines = []
        try:
            with get_db_session(self.db_path) as session:
                cutoff = datetime.now() - timedelta(hours=18)
                news = (
                    session.query(FinanceNews)
                    .filter(
                        FinanceNews.source == "cailianshe",
                        FinanceNews.collected_at >= cutoff,
                    )
                    .order_by(
                        # 红色/重要新闻优先
                        FinanceNews.category.desc(),
                        FinanceNews.collected_at.desc(),
                    )
                    .limit(30)
                    .all()
                )
                seen = set()
                for n in news:
                    title = (n.title or "").strip()[:120]
                    if title and title not in seen:
                        seen.add(title)
                        cat = n.category or ""
                        level = "★" if cat in ("red", "important") else "●"
                        lines.append(f"{level} {title}")
        except Exception as e:
            logger.debug(f"获取新闻失败: {e}")
        return "\n".join(lines) if lines else "暂无重要新闻"

    def _get_jiuyan_hot(self) -> str:
        """获取韭研公社最新热点。"""
        lines = []
        try:
            with get_db_session(self.db_path) as session:
                cutoff = datetime.now() - timedelta(hours=24)
                news = (
                    session.query(FinanceNews)
                    .filter(
                        FinanceNews.source.in_(["jiuyan", "xueqiu"]),
                        FinanceNews.collected_at >= cutoff,
                    )
                    .order_by(FinanceNews.collected_at.desc())
                    .limit(20)
                    .all()
                )
                seen = set()
                for n in news:
                    title = (n.title or "").strip()[:120]
                    if title and title not in seen:
                        seen.add(title)
                        lines.append(f"[{n.source}] {title}")
        except Exception as e:
            logger.debug(f"获取热点失败: {e}")
        return "\n".join(lines) if lines else "暂无热点数据"

    def _get_multi_source_hot_topics(self) -> str:
        """
        获取多源热点综合信息：
        - 财联社红色/重要新闻
        - 同花顺热股排行 (source=ths_hot)
        - 东方财富热门概念/涨停复盘 (source=eastmoney_hot)
        """
        sections: list[str] = []
        try:
            with get_db_session(self.db_path) as session:
                cutoff = datetime.now() - timedelta(hours=12)
                seen = set()

                # 1) 财联社红色/重要新闻
                cls_red = (
                    session.query(FinanceNews)
                    .filter(
                        FinanceNews.source == "cailianshe",
                        FinanceNews.category.in_(["red", "important"]),
                        FinanceNews.collected_at >= cutoff,
                    )
                    .order_by(FinanceNews.collected_at.desc())
                    .limit(10)
                    .all()
                )
                if cls_red:
                    lines = []
                    for n in cls_red:
                        title = (n.title or "").strip()[:120]
                        if title and title not in seen:
                            seen.add(title)
                            lines.append(f"★ {title}")
                    if lines:
                        sections.append("【财联社红色新闻】\n" + "\n".join(lines))

                # 2) 同花顺热股
                ths = (
                    session.query(FinanceNews)
                    .filter(
                        FinanceNews.source == "ths_hot",
                        FinanceNews.collected_at >= cutoff,
                    )
                    .order_by(FinanceNews.collected_at.desc())
                    .limit(15)
                    .all()
                )
                if ths:
                    lines = []
                    for n in ths:
                        title = (n.title or "").strip()[:100]
                        if title and title not in seen:
                            seen.add(title)
                            lines.append(f"● {title}")
                    if lines:
                        sections.append("【同花顺热股排行】\n" + "\n".join(lines))

                # 3) 东方财富热门概念 + 涨停复盘
                em = (
                    session.query(FinanceNews)
                    .filter(
                        FinanceNews.source == "eastmoney_hot",
                        FinanceNews.collected_at >= cutoff,
                    )
                    .order_by(FinanceNews.collected_at.desc())
                    .limit(20)
                    .all()
                )
                if em:
                    lines = []
                    for n in em:
                        title = (n.title or "").strip()[:100]
                        if title and title not in seen:
                            seen.add(title)
                            lines.append(f"● {title}")
                    if lines:
                        sections.append("【东方财富热门概念/涨停复盘】\n" + "\n".join(lines))

        except Exception as e:
            logger.debug(f"获取多源热点失败: {e}")
        return "\n\n".join(sections) if sections else ""

    def _get_news_driven_candidates(self) -> str:
        """
        从多源数据中提取【热点驱动候选股】——不依赖涨停板，纯粹从新闻/热点出发推荐个股。
        数据来源：
        1. AI舆情分析（SentimentAnalysis）中 bullish + impact>=6 的具体个股
        2. 同花顺热股排行中的个股（含概念标签）
        3. 财联社红色新闻中提到的个股和板块
        4. 跨平台共同热点（多平台同时出现的概念/板块 → 聚焦其龙头股）

        ★ 交叉验证：所有候选股都会与今日 StockDaily 交叉验证，
          今日大跌（< -5%）的股票会被标记或排除。
        """
        import re
        from collections import Counter
        from datetime import date as _date

        sections: list[str] = []
        # 统计跨平台热点主题（用于发现共振）
        theme_counter: Counter = Counter()
        theme_stocks: dict[str, list[str]] = {}  # theme -> [stock descriptions]

        try:
            with get_db_session(self.db_path) as session:
                cutoff = datetime.now() - timedelta(hours=18)

                # ========== 构建今日涨跌幅查找表（交叉验证用）==========
                today_str = _date.today().strftime("%Y-%m-%d")
                sd_rows = session.query(StockDaily).filter(
                    StockDaily.trade_date == today_str
                ).all()
                # 用纯数字代码和带前缀代码同时做key
                daily_map: dict[str, float] = {}  # code -> change_pct
                for r in sd_rows:
                    if r.change_pct is not None:
                        daily_map[r.code] = r.change_pct
                        bare = r.code.lstrip("shzSHZbBjJ")
                        if bare:
                            daily_map[bare] = r.change_pct

                skipped_today = 0  # 今日大跌被排除的计数

                def _is_today_crashed(code_str: str) -> bool:
                    """检查该股票今日是否大跌（< -5%），是则应排除。"""
                    nonlocal skipped_today
                    bare = code_str.lstrip("shzSHZbBjJ")
                    chg = daily_map.get(bare) or daily_map.get(code_str)
                    if chg is not None and chg < MARKET_CRASH_THRESHOLD:
                        skipped_today += 1
                        return True
                    return False

                def _today_change_label(code_str: str) -> str:
                    """获取今日涨跌幅标签。"""
                    bare = code_str.lstrip("shzSHZbBjJ")
                    chg = daily_map.get(bare) or daily_map.get(code_str)
                    if chg is not None:
                        return f"今日{chg:+.1f}%"
                    return ""

                def _get_today_change(code_str: str) -> float | None:
                    """获取今日涨跌幅数值。"""
                    bare = code_str.lstrip("shzSHZbBjJ")
                    return daily_map.get(bare) or daily_map.get(code_str)

                def _clean_stale_reason(reason: str, today_chg: float | None) -> str:
                    """
                    清理AI分析理由中的过时涨停描述。
                    如果今日涨幅 < 9.5% 但理由中提到封单/涨停/未开板等，
                    说明这是昨日涨停的分析，需要标注。
                    """
                    if today_chg is None or today_chg >= LIMIT_UP_LIKE_CHANGE_THRESHOLD:
                        return reason  # 今天确实涨停了，理由有效
                    # 检查是否包含涨停相关描述
                    _stale_keywords = ("封单", "涨停", "未开板", "封板", "回封", "首板",
                                       "连板", "炸板", "开板")
                    has_stale = any(kw in reason for kw in _stale_keywords)
                    if has_stale:
                        return f"[注意:以下为昨日涨停分析,今日未涨停] {reason}"
                    return reason

                # ---- 1) AI舆情中看多的具体个股 ----
                ai_bullish = (
                    session.query(SentimentAnalysis)
                    .filter(
                        SentimentAnalysis.sentiment == "bullish",
                        SentimentAnalysis.impact_score >= SENTIMENT_MIN_IMPACT_SCORE,
                        SentimentAnalysis.analyzed_at >= cutoff,
                    )
                    .order_by(SentimentAnalysis.impact_score.desc())
                    .limit(20)
                    .all()
                )
                ai_lines = []
                for sa in ai_bullish:
                    stock = sa.related_stock_name or ""
                    code = sa.related_stock_code or ""
                    sector = sa.related_sector or ""
                    score = sa.impact_score or 0
                    reason = (sa.analysis_reason or "")[:80]
                    if stock and stock.lower() != "null" and stock.lower() != "none":
                        # ★ 交叉验证：今日大跌的排除
                        if _is_today_crashed(code):
                            logger.debug(f"热点候选排除(今日大跌): {stock}({code})")
                            continue
                        chg_label = _today_change_label(code)
                        today_chg = _get_today_change(code)
                        # ★ 清理过时的涨停描述
                        reason = _clean_stale_reason(reason, today_chg)
                        ai_lines.append(
                            f"  {stock}({code}) 行业:{sector} 影响力={score:.0f} "
                            f"{chg_label} {reason}"
                        )
                        # 记录板块主题
                        for s in re.split(r"[/、,，]", sector):
                            s = s.strip()
                            if s and len(s) >= MIN_THEME_NAME_LENGTH:
                                theme_counter[s] += 1
                                theme_stocks.setdefault(s, []).append(f"{stock}({code})")
                if ai_lines:
                    # 去重
                    seen = set()
                    unique = []
                    for line in ai_lines:
                        key = line[:20]
                        if key not in seen:
                            seen.add(key)
                            unique.append(line)
                    sections.append(
                        f"【AI舆情看多个股】共{len(unique)}只\n" + "\n".join(unique[:15])
                    )

                # ---- 2) 同花顺热股（提取概念标签 + 个股） ----
                ths = (
                    session.query(FinanceNews)
                    .filter(
                        FinanceNews.source == "ths_hot",
                        FinanceNews.collected_at >= cutoff,
                    )
                    .order_by(FinanceNews.collected_at.desc())
                    .limit(50)
                    .all()
                )
                ths_lines = []
                for n in ths:
                    title = n.title or ""
                    tags = n.tags or ""
                    # 解析 tags: "金风科技,002202"
                    tag_parts = [t.strip() for t in tags.split(",")]
                    stock_name = tag_parts[0] if tag_parts else ""
                    stock_code = tag_parts[1] if len(tag_parts) > 1 else ""
                    # ★ 交叉验证：今日大跌的排除
                    if stock_code and _is_today_crashed(stock_code):
                        logger.debug(f"同花顺热股排除(今日大跌): {stock_name}({stock_code})")
                        continue
                    # 解析概念标签: title 中的 ['商业航天', '海工装备']
                    raw_concepts = re.findall(r"'([^']+)'", title)
                    # 过滤掉 JSON key 名和非概念词
                    _skip_words = {
                        "concept_tag", "popularity_tag", "stock_tag",
                        "首板涨停", "连板涨停", "新高",
                    }
                    concepts = [
                        c for c in raw_concepts
                        if c not in _skip_words
                        and not c.endswith("_tag")
                        and not re.match(r"^\d+天\d+板$", c)  # 过滤 "2天1板"
                    ]
                    concept_str = "/".join(concepts[:3]) if concepts else ""
                    if stock_name and stock_code:
                        chg_label = _today_change_label(stock_code)
                        ths_lines.append(
                            f"  {stock_name}({stock_code}) 概念:{concept_str} {chg_label}"
                        )
                        for c in concepts[:3]:
                            c = c.strip()
                            if len(c) >= MIN_THEME_NAME_LENGTH:
                                theme_counter[c] += 1
                                theme_stocks.setdefault(c, []).append(f"{stock_name}({stock_code})")
                if ths_lines:
                    sections.append(
                        f"【同花顺热股候选】Top{min(len(ths_lines), 20)}只\n"
                        + "\n".join(ths_lines[:20])
                    )

                # ---- 3) 财联社新闻中提及的个股（从AI标签提取） ----
                cls_news = (
                    session.query(FinanceNews)
                    .filter(
                        FinanceNews.source == "cailianshe",
                        FinanceNews.category.in_(["red", "important"]),
                        FinanceNews.collected_at >= cutoff,
                    )
                    .order_by(FinanceNews.collected_at.desc())
                    .limit(20)
                    .all()
                )
                news_stock_lines = []
                for n in cls_news:
                    tags_str = n.tags or ""
                    title = (n.title or "")[:60]
                    # 从AI标签提取: "行业:xxx 个股:yyy(code)"
                    sectors = re.findall(r"行业:([^\s|]+)", tags_str)
                    stocks = re.findall(r"个股:([^\s→|]+)", tags_str)
                    sentiment = ""
                    if "利好" in tags_str:
                        sentiment = "利好"
                    elif "利空" in tags_str:
                        sentiment = "利空"
                    if stocks and sentiment == "利好":
                        sector_str = "/".join(sectors[:2]) if sectors else ""
                        news_stock_lines.append(
                            f"  {sentiment} {sector_str} → {', '.join(stocks[:3])} [{title}]"
                        )
                        for s in sectors:
                            for sub in re.split(r"[/、,，]", s):
                                sub = sub.strip()
                                if sub and len(sub) >= MIN_THEME_NAME_LENGTH:
                                    theme_counter[sub] += 1
                if news_stock_lines:
                    sections.append(
                        "【财联社重大新闻驱动个股】\n" + "\n".join(news_stock_lines[:10])
                    )

                # ---- 4) 东方财富热门概念 → 作为主题计数 ----
                em_concepts = (
                    session.query(FinanceNews)
                    .filter(
                        FinanceNews.source == "eastmoney_hot",
                        FinanceNews.category == "热门概念",
                        FinanceNews.collected_at >= cutoff,
                    )
                    .order_by(FinanceNews.collected_at.desc())
                    .limit(20)
                    .all()
                )
                for n in em_concepts:
                    concept = (n.tags or "").strip()
                    if concept and len(concept) >= MIN_THEME_NAME_LENGTH:
                        theme_counter[concept] += 1

                # ---- 5) 跨平台共振主题（>=2个来源都出现的） ----
                # 从 theme_stocks 中移除今日大跌的股票
                for theme_key in list(theme_stocks.keys()):
                    filtered = []
                    for s_desc in theme_stocks[theme_key]:
                        # 提取括号中的代码
                        code_match = re.search(r"\((\d{6})\)", s_desc)
                        if code_match and _is_today_crashed(code_match.group(1)):
                            continue
                        filtered.append(s_desc)
                    theme_stocks[theme_key] = filtered

                convergent = [
                    (theme, cnt)
                    for theme, cnt in theme_counter.most_common(CONVERGENT_THEME_CANDIDATE_LIMIT)
                    if cnt >= CONVERGENT_THEME_MIN_COUNT
                ]
                if convergent:
                    conv_lines = []
                    for theme, cnt in convergent[:10]:
                        related = list(dict.fromkeys(theme_stocks.get(theme, [])))[:5]
                        stock_str = "、".join(related) if related else "（需结合行情筛选龙头）"
                        conv_lines.append(f"  ★ {theme}（{cnt}源共振）→ {stock_str}")
                    sections.append(
                        "【跨平台共振热点主题】（多个平台同时关注，机会较大）\n"
                        + "\n".join(conv_lines)
                    )

                if skipped_today:
                    logger.info(
                        f"热点候选交叉验证: 排除 {skipped_today} 只今日大跌(< -5%)的股票"
                    )

        except Exception as e:
            logger.debug(f"获取热点驱动候选失败: {e}")
        return "\n\n".join(sections) if sections else ""

    def _get_market_overview_context(self) -> str:
        """获取最新市场概况。"""
        try:
            from src.collectors.stock_data import StockDataCollector
            ov = StockDataCollector._market_overview_cache
            if ov and (ov.get("total_amount_yi") or ov.get("up_count")):
                amount = ov.get("total_amount_yi", 0)
                amount_wan_yi = amount / 10000 if amount else 0
                parts = []
                if amount_wan_yi:
                    parts.append(f"两市成交额: {amount_wan_yi:.2f}万亿")
                parts.append(
                    f"涨/跌/平: {ov.get('up_count', 0)}/{ov.get('down_count', 0)}/{ov.get('flat_count', 0)}"
                )
                if ov.get("limit_up_count"):
                    parts.append(f"涨停{ov.get('limit_up_count', 0)}家/跌停{ov.get('limit_down_count', 0)}家")
                if ov.get("market_emotion"):
                    parts.append(f"市场情绪: {ov['market_emotion']}")
                if ov.get("sh_index"):
                    parts.append(f"上证: {ov['sh_index']} ({ov.get('sh_change_pct', 0):+.2f}%)")
                if ov.get("northbound_net_yi") is not None:
                    parts.append(f"北向资金: {ov['northbound_net_yi']}亿")
                return "\n".join(parts)
        except Exception:
            pass
        return "市场数据暂不可用"

    def _get_sentiment_context(self) -> str:
        """获取最新AI舆情分析（bullish个股）。排除今日大跌的。"""
        from datetime import date as _date
        lines = []
        try:
            with get_db_session(self.db_path) as session:
                cutoff = datetime.now() - timedelta(hours=12)

                # 构建今日涨跌幅查找表
                today_str = _date.today().strftime("%Y-%m-%d")
                sd_rows = session.query(StockDaily).filter(
                    StockDaily.trade_date == today_str
                ).all()
                daily_chg: dict[str, float] = {}
                for r in sd_rows:
                    if r.change_pct is not None:
                        bare = r.code.lstrip("shzSHZbBjJ")
                        daily_chg[bare] = r.change_pct
                        daily_chg[r.code] = r.change_pct

                sentiments = (
                    session.query(SentimentAnalysis)
                    .filter(
                        SentimentAnalysis.sentiment == "bullish",
                        SentimentAnalysis.impact_score >= SENTIMENT_MIN_IMPACT_SCORE,
                        SentimentAnalysis.analyzed_at >= cutoff,
                    )
                    .order_by(SentimentAnalysis.impact_score.desc())
                    .limit(15)
                    .all()
                )
                for s in sentiments:
                    stock = s.related_stock_name or s.related_stock_code or ""
                    code = s.related_stock_code or ""
                    sector = s.related_sector or ""
                    score = s.impact_score or 0
                    reason = (s.analysis_reason or "")[:60]
                    # ★ 交叉验证：排除今日大跌的
                    bare_code = code.lstrip("shzSHZbBjJ")
                    chg = daily_chg.get(bare_code) or daily_chg.get(code)
                    if chg is not None and chg < MARKET_CRASH_THRESHOLD:
                        continue  # 今日大跌，排除
                    # ★ 标注过时涨停描述
                    _stale_kw = ("封单", "涨停", "未开板", "封板", "首板", "连板")
                    if chg is not None and chg < LIMIT_UP_LIKE_CHANGE_THRESHOLD and any(k in reason for k in _stale_kw):
                        reason = f"[昨日涨停分析,今日{chg:+.1f}%] {reason}"
                    if stock or sector:
                        chg_str = f" 今日{chg:+.1f}%" if chg is not None else ""
                        lines.append(f"看多 {stock}({sector}) 影响={score:.0f}{chg_str} {reason}")
        except Exception:
            pass
        return "\n".join(lines) if lines else ""

    def _get_score_context(self) -> str:
        """获取最新综合评分Top股票。"""
        lines = []
        try:
            with get_db_session(self.db_path) as session:
                from sqlalchemy import func
                latest_date = session.query(func.max(StockScore.score_date)).scalar()
                if latest_date:
                    scores = (
                        session.query(StockScore)
                        .filter(StockScore.score_date == latest_date)
                        .order_by(StockScore.composite_score.desc())
                        .limit(10)
                        .all()
                    )
                    lines.extend(
                        f"{s.name}({s.code}) 综合={s.composite_score:.0f} "
                        f"舆情={s.sentiment_score:.0f} 涨停={s.limit_up_score:.0f} "
                        f"板块={s.sector_effect_score:.0f}"
                        for s in scores
                    )
        except Exception:
            pass
        return "\n".join(lines) if lines else ""

    # ---- 全市场强势股扫描 ----

    def _get_market_strong_stocks(self, session: str) -> list[dict]:
        """
        扫描全市场强势股（高涨幅、大换手、放量）。
        使用数据库中最新日期的数据（盘后=今日收盘，盘中=今日实时）。
        筛选条件：
        - 涨幅 >= 5%（盘中）或 >= 3%（盘前/盘后，关注次日潜伏）
        - 非ST、非涨停（涨停在 limit_up 里已有）
        - 按涨幅降序、成交额降序排列
        """
        stocks = []
        today = date.today().strftime("%Y-%m-%d")
        try:
            with get_db_session(self.db_path) as db_session:
                # 确定使用哪个日期的数据
                if session in ("morning", "noon", "afternoon"):
                    # 盘中：用今日数据，涨幅>=5% 但 <涨停(约9.8%)
                    target_date = today
                    min_change = 5.0
                    max_change = 9.8  # 排除已涨停的
                else:
                    # 盘前/盘后：用最近有数据的日期，涨幅>=3%
                    from sqlalchemy import func
                    latest = db_session.query(func.max(StockDaily.trade_date)).scalar()
                    target_date = latest or today
                    min_change = 3.0
                    max_change = 9.8

                # 查询强势股（排除已在涨停板中的）
                lu_codes = set()
                lu_records = (
                    db_session.query(LimitUpStock.code)
                    .filter(LimitUpStock.trade_date == target_date)
                    .all()
                )
                lu_codes = {r[0] for r in lu_records}

                # 也取昨日涨停的codes（盘前时段这些不算"全市场新发现"）
                if session == "premarket":
                    prev_lu = (
                        db_session.query(LimitUpStock.code)
                        .filter(LimitUpStock.trade_date != today)
                        .order_by(LimitUpStock.trade_date.desc())
                        .limit(200)
                        .all()
                    )
                    lu_codes.update(r[0] for r in prev_lu)

                query = (
                    db_session.query(StockDaily)
                    .filter(
                        StockDaily.trade_date == target_date,
                        StockDaily.change_pct >= min_change,
                        StockDaily.change_pct < max_change,
                    )
                    .order_by(StockDaily.change_pct.desc())
                    .limit(100)
                    .all()
                )

                for sd in query:
                    code_raw = (sd.code or "").strip().lower()
                    code = code_raw[2:] if len(code_raw) == PREFIXED_STOCK_CODE_LENGTH and code_raw[:2] in {"sh", "sz", "bj"} else code_raw
                    if not (len(code) == STOCK_CODE_LENGTH and code.isdigit()):
                        continue
                    name = sd.name or ""
                    # 排除ST和已涨停
                    if "ST" in name or "st" in name or "*ST" in name:
                        continue
                    if code in lu_codes:
                        continue
                    amount_yi = (sd.amount or 0) / 1e8
                    circ_mv_yi = (sd.circ_mv or 0) / 1e8
                    stocks.append({
                        "code": code,
                        "name": name,
                        "change_pct": sd.change_pct or 0,
                        "turnover": sd.turnover or 0,
                        "amount_yi": amount_yi,
                        "circ_mv_yi": circ_mv_yi,
                        "open": sd.open or 0,
                        "close": sd.close or 0,
                        "high": sd.high or 0,
                        "low": sd.low or 0,
                    })

                logger.info(
                    f"全市场强势股扫描({target_date}): "
                    f"涨幅>={min_change}% 共{len(stocks)}只"
                )
        except Exception as e:
            logger.error(f"全市场强势股扫描失败: {e}")
        return stocks

    @staticmethod
    def _format_strong_stock_lines(stocks: list[dict]) -> list[str]:
        """格式化全市场强势股为文本行。"""
        lines = []
        for s in stocks:
            # 判断走势特征
            features = []
            if s["change_pct"] >= STRONG_STOCK_CHANGE_THRESHOLD:
                features.append("冲板")
            if s["turnover"] >= STRONG_STOCK_TURNOVER_THRESHOLD:
                features.append("高换手")
            if s["amount_yi"] >= STRONG_STOCK_AMOUNT_THRESHOLD:
                features.append("放量")
            if s["close"] >= s["high"] * 0.98:
                features.append("强封")

            line = (
                f"{s['name']}({s['code']}) "
                f"最新价={s['close']:.2f} "
                f"涨幅={s['change_pct']:+.2f}% "
                f"换手={s['turnover']:.1f}% "
                f"成交={s['amount_yi']:.1f}亿 "
                f"流通市值={s['circ_mv_yi']:.0f}亿"
            )
            if features:
                line += f" [{'/'.join(features)}]"
            lines.append(line)
        return lines

    def _get_us_market_context(self) -> str:
        """获取隔夜美股表现（三大指数+VIX+中概+重点个股涨跌）。"""
        parts = []
        try:
            from src.database.models import USMarketDaily, USStockEarnings
            with get_db_session(self.db_path) as session:
                us = session.query(USMarketDaily).order_by(USMarketDaily.trade_date.desc()).first()
                if us:
                    if us.nasdaq_change_pct is not None:
                        parts.append(f"纳斯达克: {us.nasdaq_close or ''} ({us.nasdaq_change_pct:+.2f}%)")
                    if us.sp500_change_pct is not None:
                        parts.append(f"标普500: {us.sp500_close or ''} ({us.sp500_change_pct:+.2f}%)")
                    if us.dow_jones_change_pct is not None:
                        parts.append(f"道琼斯: {us.dow_jones_close or ''} ({us.dow_jones_change_pct:+.2f}%)")
                    if us.vix is not None:
                        if us.vix > VIX_PANIC_LEVEL:
                            level = "恐慌"
                        elif us.vix > VIX_WARNING_LEVEL:
                            level = "警戒"
                        elif us.vix > VIX_ELEVATED_LEVEL:
                            level = "偏高"
                        else:
                            level = "正常"
                        parts.append(f"VIX: {us.vix:.1f}({level})")
                    if us.china_concept_change_pct is not None:
                        parts.append(f"中概股: {us.china_concept_change_pct:+.2f}%")
                    if us.oil_price is not None:
                        parts.append(f"原油${us.oil_price:.1f} 黄金${us.gold_price or 0:.0f}")

                # 重点美股涨跌
                from datetime import timedelta as _td
                cutoff = (date.today() - _td(days=2)).strftime("%Y-%m-%d")
                earnings = (
                    session.query(USStockEarnings)
                    .filter(USStockEarnings.report_date >= cutoff)
                    .order_by(USStockEarnings.report_date.desc())
                    .limit(20)
                    .all()
                )
                if earnings:
                    # 去重，每个 symbol 只取最新一条
                    seen = set()
                    lines = []
                    for e in earnings:
                        if e.symbol in seen:
                            continue
                        seen.add(e.symbol)
                        if e.after_hours_change_pct is not None:
                            lines.append(f"{e.company_name}({e.symbol}) {e.after_hours_change_pct:+.2f}%")
                    if lines:
                        parts.append("重点个股: " + " | ".join(lines[:8]))
        except Exception as e:
            logger.debug(f"获取美股数据失败: {e}")
        return "\n".join(parts) if parts else ""

    def _get_global_impact_context(self) -> str:
        """获取最新的国际因子AI分析结论。"""
        try:
            import json as _json

            from src.database.models import GlobalImpactAnalysis
            with get_db_session(self.db_path) as session:
                record = (
                    session.query(GlobalImpactAnalysis)
                    .order_by(GlobalImpactAnalysis.analysis_date.desc())
                    .first()
                )
                if record and record.analysis_detail:
                    detail = _json.loads(record.analysis_detail)
                    direction = detail.get("overall_direction", "neutral")
                    summary = detail.get("summary", "")
                    events = detail.get("key_events", [])
                    parts = [f"总体: {direction} | {summary}"]
                    parts.extend(
                        f"  → {ev.get('event','')} [{ev.get('impact_direction','')}] "
                        f"板块:{','.join(ev.get('affected_sectors', [])[:3])}"
                        for ev in events[:3]
                    )
                    return "\n".join(parts)
        except Exception:
            pass
        return ""

    # ---- 技术分析 ----

    def _get_technical_context(self, candidate_codes: list[str]) -> str:
        """
        对候选股做批量技术分析（MA均线 + 趋势判断），返回文本上下文。
        优先使用数据库中的历史数据，不足时通过 AKShare 补充。
        """
        if not candidate_codes:
            return ""

        from concurrent.futures import ThreadPoolExecutor, as_completed

        # 去重并限制数量（最多分析30只）
        codes = list(dict.fromkeys(candidate_codes))[:30]
        results: dict[str, dict] = {}

        def _analyze_one(code: str) -> tuple[str, dict | None]:
            try:
                return code, self._compute_technical_indicators(code)
            except Exception as e:
                logger.debug(f"技术分析失败 {code}: {e}")
                return code, None

        with ThreadPoolExecutor(max_workers=5, thread_name_prefix="ta") as executor:
            futures = {executor.submit(_analyze_one, c): c for c in codes}
            for future in as_completed(futures):
                code, ta = future.result()
                if ta:
                    results[code] = ta

        if not results:
            return ""

        # 按趋势分组
        uptrend = []    # 上升通道
        sideways = []   # 震荡
        downtrend = []  # 下降通道

        for code, ta in results.items():
            trend = ta.get("trend", "")
            line = (
                f"  {ta['name']}({code}) "
                f"趋势:{trend} "
                f"MA排列:{ta['ma_status']} "
                f"收盘={ta['close']:.2f} "
                f"MA5={ta['ma5']:.2f} MA10={ta['ma10']:.2f} MA20={ta['ma20']:.2f} "
                f"5日涨幅={ta['pct_5d']:+.1f}% "
                f"量比={ta['vol_ratio']:.1f} "
                f"{ta['extra']}"
            )
            if "上升" in trend:
                uptrend.append(line)
            elif "下降" in trend:
                downtrend.append(line)
            else:
                sideways.append(line)

        sections = []
        if uptrend:
            sections.append(f"★ 上升通道（优先选择！共{len(uptrend)}只）:\n" + "\n".join(uptrend))
        if sideways:
            sections.append(f"● 震荡整理（共{len(sideways)}只）:\n" + "\n".join(sideways))
        if downtrend:
            sections.append(f"▼ 下降通道（谨慎，共{len(downtrend)}只）:\n" + "\n".join(downtrend))

        return "\n".join(sections)

    # 股票名称缓存（避免重复查库）
    _name_cache: dict[str, str] = {}

    def _resolve_stock_name(self, code: str) -> str:
        """从数据库获取股票名称（带缓存）。"""
        bare = code.lstrip("shzSHZbBjJ")
        if bare in self._name_cache:
            return self._name_cache[bare]
        try:
            with get_db_session(self.db_path) as sess:
                sd = sess.query(StockDaily.name).filter(
                    StockDaily.code.contains(bare)
                ).first()
                name = (sd[0] if sd and sd[0] else bare) or bare
                self._name_cache[bare] = name
                return name
        except Exception:
            return bare

    def _compute_technical_indicators(self, code: str) -> dict | None:
        """
        对单只股票计算技术指标。
        先查数据库历史，不足20日则从 AKShare 补充30日K线（含重试）。
        返回 dict: {name, close, ma5, ma10, ma20, trend, ma_status, pct_5d, vol_ratio, extra}
        """
        import time as _time

        closes: list[float] = []
        volumes: list[float] = []
        stock_name = ""

        # 标准化代码
        bare_code = code.lstrip("shzSHZbBjJ")
        if not bare_code or not bare_code.isdigit():
            return None

        # 1) 先查数据库
        try:
            with get_db_session(self.db_path) as session:
                records = (
                    session.query(StockDaily)
                    .filter(StockDaily.code.contains(bare_code))
                    .order_by(StockDaily.trade_date.desc())
                    .limit(30)
                    .all()
                )
                if records and len(records) >= TECH_MA20_PERIOD:
                    records = list(reversed(records))
                    closes = [r.close for r in records if r.close]
                    volumes = [r.volume for r in records if r.volume]
                    stock_name = records[-1].name or ""
        except Exception:
            pass

        # 2) 数据不足时从外部数据源补充
        if len(closes) < TECH_MA20_PERIOD:
            if len(bare_code) != STOCK_CODE_LENGTH:
                return None
            # 先尝试 AKShare（含重试）
            end_date = date.today().strftime("%Y%m%d")
            start_date = (date.today() - timedelta(days=60)).strftime("%Y%m%d")
            for attempt in range(2):
                try:
                    import akshare as ak
                    df = ak.stock_zh_a_hist(
                        symbol=bare_code, period="daily",
                        start_date=start_date, end_date=end_date,
                        adjust="qfq",
                    )
                    if df is not None and not df.empty and len(df) >= TECH_MIN_KLINE_POINTS:
                        closes = df["收盘"].tolist()
                        volumes = df["成交量"].tolist()
                        break
                except Exception:
                    if attempt < 1:
                        _time.sleep(0.3)

            # AKShare 失败时尝试 East Money 直连 API
            if len(closes) < TECH_MIN_KLINE_POINTS:
                with suppress(Exception):
                    closes, volumes = self._fetch_kline_eastmoney(bare_code, 30)

            if len(closes) < TECH_MIN_KLINE_POINTS:
                return None

            if not stock_name:
                stock_name = self._resolve_stock_name(bare_code)

        if len(closes) < TECH_MIN_KLINE_POINTS:
            return None

        # 3) 计算技术指标
        current = closes[-1]
        ma5 = sum(closes[-5:]) / 5
        ma10 = sum(closes[-10:]) / 10
        ma20 = (
            sum(closes[-TECH_MA20_PERIOD:]) / TECH_MA20_PERIOD
            if len(closes) >= TECH_MA20_PERIOD
            else sum(closes) / len(closes)
        )

        # 均线排列判断
        if current > ma5 > ma10 > ma20:
            ma_status = "多头排列(价>MA5>MA10>MA20)"
            trend = "上升通道"
        elif current > ma5 > ma10:
            ma_status = "偏多(价>MA5>MA10)"
            trend = "上升通道（短期）"
        elif current > ma20 and ma5 > ma20:
            ma_status = "MA20上方"
            trend = "震荡偏强"
        elif current < ma5 < ma10 < ma20:
            ma_status = "空头排列(价<MA5<MA10<MA20)"
            trend = "下降通道"
        elif current < ma5 < ma10:
            ma_status = "偏空(价<MA5<MA10)"
            trend = "下降通道（短期）"
        else:
            ma_status = "交叉震荡"
            trend = "震荡整理"

        # 5日涨幅
        pct_5d = (
            ((current / closes[-TECH_SHORT_KLINE_POINTS]) - 1) * 100
            if len(closes) >= TECH_SHORT_KLINE_POINTS
            else 0
        )

        # 量比（近5日均量/前5日均量）
        if len(volumes) >= TECH_MIN_KLINE_POINTS:
            recent_vol = sum(volumes[-5:]) / 5
            prev_vol = sum(volumes[-10:-5]) / 5
            vol_ratio = recent_vol / prev_vol if prev_vol > 0 else 1.0
        else:
            vol_ratio = 1.0

        # 额外特征
        extra_parts = []
        if vol_ratio > VOL_EXPANSION_RATIO and pct_5d > 0:
            extra_parts.append("[放量上攻]")
        elif vol_ratio > VOL_EXPANSION_RATIO and pct_5d < 0:
            extra_parts.append("[放量下跌]")
        elif vol_ratio < VOL_CONTRACTION_RATIO and pct_5d > 0:
            extra_parts.append("[缩量上涨]")

        # 突破MA20判断
        if len(closes) >= TECH_MIN_BREAKOUT_POINTS and closes[-2] <= ma20 < current:
            extra_parts.append("[突破MA20]")
        # 创20日新高
        if len(closes) >= TECH_MA20_PERIOD and current >= max(closes[-TECH_MA20_PERIOD:]):
            extra_parts.append("[20日新高]")
        # 站稳MA5
        if len(closes) >= TECH_STABLE_MA_DAYS and all(c > sum(closes[max(0, i - 4):i + 1]) / (min(5, i + 1)) for i, c in enumerate(closes[-TECH_STABLE_MA_DAYS:], len(closes) - TECH_STABLE_MA_DAYS)):
            extra_parts.append("[连续站稳MA5]")

        return {
            "name": stock_name,
            "close": current,
            "ma5": ma5,
            "ma10": ma10,
            "ma20": ma20,
            "trend": trend,
            "ma_status": ma_status,
            "pct_5d": pct_5d,
            "vol_ratio": vol_ratio,
            "extra": " ".join(extra_parts),
        }

    @staticmethod
    def _fetch_kline_eastmoney(bare_code: str, count: int = 30) -> tuple[list[float], list[float]]:
        """
        从东方财富直连API获取日K线数据（AKShare备用方案）。
        返回 (closes, volumes)。
        """
        from src.collectors.em_client import get_em_client
        # secid: SH=1, SZ=0, BJ=0
        secid = f"1.{bare_code}" if bare_code.startswith("6") else f"0.{bare_code}"
        url = "http://push2his.eastmoney.com/api/qt/stock/kline/get"
        params = {
            "secid": secid,
            "fields1": "f1,f2,f3,f4,f5,f6,f7,f8",
            "fields2": "f51,f52,f53,f54,f55,f56,f57",
            "klt": 101,  # daily
            "fqt": 1,    # qfq
            "lmt": count,
            "end": "20500101",
        }
        data = get_em_client().request_json(
            url,
            params=params,
            timeout=8000,
            referer="https://quote.eastmoney.com/",
        ) or {}
        klines = data.get("data", {}).get("klines", [])
        closes = []
        volumes = []
        for line in klines:
            parts = line.split(",")
            # date,open,close,high,low,volume,amount
            if len(parts) >= KLINE_PARTS_MIN_LENGTH:
                closes.append(float(parts[2]))
                volumes.append(float(parts[5]))
        return closes, volumes

    def _collect_candidate_codes(
        self,
        limit_up_info: dict,
        market_strong_stocks: list[dict],
        news_driven_context: str,
    ) -> list[str]:
        """
        从各数据源收集候选股代码，用于批量技术分析。
        """
        import re
        codes = []
        # 涨停板
        for s in limit_up_info.get("stocks", []):
            c = s.get("code", "")
            if c:
                codes.append(c)
        for s in limit_up_info.get("today_stocks", []):
            c = s.get("code", "")
            if c:
                codes.append(c)
        # 全市场强势股
        for s in market_strong_stocks:
            c = s.get("code", "")
            if c:
                codes.append(c)
        # 从热点驱动文本中提取 6位数字代码
        if news_driven_context:
            found = re.findall(r"\((\d{6})\)", news_driven_context)
            codes.extend(found)
        # 去重
        return list(dict.fromkeys(codes))

    # ---- 保存 ----

    @staticmethod
    def _latest_close(session, code: str) -> float | None:
        row = (
            session.query(StockDaily.close)
            .filter(StockDaily.code.in_([code, f"sh{code}", f"sz{code}", f"bj{code}"]), StockDaily.close > 0)
            .order_by(StockDaily.trade_date.desc())
            .first()
        )
        return float(row[0]) if row else None

    def _save_predictions(self, today: str, predictions: list[dict],
                          market_outlook: str, main_theme: str):
        """将预测结果写入 TradeSignal 表（价格计划经过校验，不合理的价格丢弃）。"""
        from src.trading.price_plan import sanitize_price_plan

        try:
            with get_db_session(self.db_path) as session:
                # 清除今日旧预测
                session.query(TradeSignal).filter(
                    TradeSignal.signal_date == today,
                    TradeSignal.signal_type == "premarket",
                ).delete()

                for p in predictions:
                    code = str(p.get("code", "")).strip()
                    if not code:
                        continue
                    code = code.zfill(6)

                    confidence = int(p.get("confidence", 5))
                    predict_type = p.get("predict_type", "")
                    reason = p.get("reason", "")
                    risk = p.get("risk", "")
                    target_time = p.get("target_time", "")
                    source = p.get("source", "涨停板")  # 新增来源字段

                    structured_reason = f"##TYPE:{predict_type}##TIME:{target_time}##SRC:{source}##{reason}"

                    advice_parts = [f"[{predict_type}]"]
                    if source and source != "涨停板":
                        advice_parts.append(f"来源:{source}")
                    if target_time:
                        advice_parts.append(f"时机:{target_time}")
                    advice_parts.append(f"逻辑:{reason}")
                    if risk:
                        advice_parts.append(f"风险:{risk}")
                    if market_outlook:
                        advice_parts.append(f"大盘:{market_outlook}")

                    plan = sanitize_price_plan(
                        code, p.get("name", ""), self._latest_close(session, code),
                        p.get("buy_price"), p.get("stop_loss"), p.get("target_price"),
                    )
                    plan_text = " ".join(
                        f"{label}{value:.2f}"
                        for label, value in (("买入", plan.entry_price), ("止损", plan.stop_loss), ("目标", plan.target_price))
                        if value
                    )
                    if plan_text:
                        advice_parts.append(f"计划:{plan_text}")

                    signal = TradeSignal(
                        code=code,
                        name=p.get("name", ""),
                        signal_date=today,
                        signal_type="premarket",
                        signal_strength=confidence / 10.0,
                        composite_score=confidence * 10.0,
                        reason=structured_reason,
                        ai_verdict="买入" if confidence >= HIGH_CONFIDENCE_THRESHOLD else "观望" if confidence >= MEDIUM_CONFIDENCE_THRESHOLD else "回避",
                        ai_advice=" | ".join(advice_parts),
                        entry_price=plan.entry_price,
                        stop_loss_price=plan.stop_loss,
                        target_price=plan.target_price,
                        is_executed=False,
                    )
                    session.add(signal)
                logger.info(f"涨停预测已存储: {len(predictions)} 条")
        except Exception as e:
            logger.error(f"保存涨停预测失败: {e}")


# ---- 兼容旧名称 ----
PreMarketPredictor = LimitUpPredictor
