"""
Web 接口和脚本通用的数据查询服务。
将原 FastAPI dashboard 的查询辅助函数抽离到此处。
"""

from datetime import date, datetime, timedelta

from loguru import logger

from src.config_loader import load_config
from src.database.db import get_db_session
from src.database.models import (
    FinanceNews,
    GlobalNews,
    LimitUpStock,
    SentimentAnalysis,
    StockDaily,
    StockDiagnosis,
    StockInfo,
    StockScore,
    TradeSignal,
)
from src.utils.stock_code import bare_code, diagnosis_code, prefixed_code

HIGH_IMPORTANCE_THRESHOLD = 7
MEDIUM_IMPORTANCE_THRESHOLD = 4
MAJOR_IMPORTANCE_THRESHOLD = 8
TRADE_FOCUS_MAX_ROWS = 10
PREFIXED_STOCK_CODE_LENGTH = 8
STOCK_CODE_LENGTH = 6


class DataQueryService:
    """统一查询数据库中的仪表盘数据。"""

    def __init__(self, db_path: str | None = None):
        config = load_config()
        self.db_path = db_path or config.get("database", {}).get("sqlite_path", "data/quant.db")

    def diagnosis_history(self, code: str, limit: int = 5) -> list[dict]:
        """某只股票最近几次 AI 诊断（新的在前）：时间、行情日、操作建议、评分、一句话结论。"""
        import json

        from src.analyzers.decision import normalize_action

        with get_db_session(self.db_path) as session:
            rows = (
                session.query(StockDiagnosis).filter(StockDiagnosis.code == diagnosis_code(code))
                .order_by(StockDiagnosis.created_at.desc(), StockDiagnosis.id.desc()).limit(limit).all()
            )
            result = []
            for r in rows:
                try:
                    detail = json.loads(r.result_json or "{}")
                except ValueError:
                    detail = {}
                result.append({
                    "created_at": r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else "",
                    "trade_date": r.trade_date or "",
                    "action": normalize_action(r.action) or normalize_action(detail.get("action_label")) or "",
                    "score": r.score,
                    "summary": str(detail.get("one_sentence") or ""),
                })
        return result

    @staticmethod
    def _diagnosis_detail(row: StockDiagnosis) -> dict:
        """解析诊断记录里的 result_json，坏数据按空字典处理。"""
        import json

        try:
            detail = json.loads(row.result_json or "{}")
        except ValueError:
            detail = {}
        return detail if isinstance(detail, dict) else {}

    def list_diagnoses(self, code: str | None = None, action: str | None = None, days: int = 30,
                       limit: int = 50, offset: int = 0) -> dict:
        """诊断历史分页列表（新的在前）；days<=0 不限时间，action 按归一化后的操作建议过滤。"""
        from src.analyzers.decision import normalize_action

        with get_db_session(self.db_path) as session:
            query = session.query(StockDiagnosis)
            if code:
                query = query.filter(StockDiagnosis.code == diagnosis_code(code))
            if action:
                query = query.filter(StockDiagnosis.action == (normalize_action(action) or action))
            if days and days > 0:
                query = query.filter(StockDiagnosis.created_at >= datetime.now() - timedelta(days=days))
            total = query.count()
            rows = (
                query.order_by(StockDiagnosis.created_at.desc(), StockDiagnosis.id.desc())
                .offset(max(offset, 0)).limit(max(limit, 0)).all()
            )
            items = []
            for r in rows:
                detail = self._diagnosis_detail(r)
                items.append({
                    "id": r.id,
                    "code": r.code,
                    "name": r.name or "",
                    "trade_date": r.trade_date or "",
                    "action": normalize_action(r.action) or normalize_action(detail.get("action_label")) or "",
                    "score": r.score,
                    "summary": str(detail.get("one_sentence") or ""),
                    "created_at": r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else "",
                })
        return {"total": total, "items": items}

    def get_diagnosis(self, diagnosis_id: int) -> dict | None:
        """单条诊断详情（含完整结果）；不存在返回 None。"""
        from src.analyzers.decision import normalize_action

        with get_db_session(self.db_path) as session:
            r = session.get(StockDiagnosis, diagnosis_id)
            if r is None:
                return None
            from src.utils.redaction import redact
            detail = redact(self._diagnosis_detail(r))
            if detail:
                from src.services.research_artifact import build_research_artifact
                detail["structured_report"] = build_research_artifact({"code": r.code, "name": r.name, **detail}, r.id)
            return {
                "id": r.id,
                "code": r.code,
                "name": r.name or "",
                "trade_date": r.trade_date or "",
                "action": normalize_action(r.action) or normalize_action(detail.get("action_label")) or "",
                "score": r.score,
                "created_at": r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else "",
                "result": {**detail, "diagnosis_id": r.id} if detail else detail,
                "run_log": self._parse_run_log(r.run_log) or detail.get("run_log") or None,
            }

    @staticmethod
    def _parse_run_log(text: str | None) -> dict | None:
        import json

        try:
            data = json.loads(text) if text else None
        except ValueError:
            return None
        from src.utils.redaction import redact
        return redact(data) if isinstance(data, dict) else None

    def diagnosis_trend(self, code: str, days: int = 180) -> list[dict]:
        """某代码近 days 天的诊断评分走势（时间正序），附诊断行情日的收盘价（取不到为 None）。"""
        from src.analyzers.decision import normalize_action
        from src.database.models import FundDaily, StockDaily

        key = diagnosis_code(code)
        since = datetime.now() - timedelta(days=max(days, 1))
        with get_db_session(self.db_path) as session:
            rows = (
                session.query(StockDiagnosis)
                .filter(StockDiagnosis.code == key, StockDiagnosis.created_at >= since)
                .order_by(StockDiagnosis.created_at.asc(), StockDiagnosis.id.asc()).all()
            )
            dates = {r.trade_date for r in rows if r.trade_date}
            closes: dict[str, float | None] = {}
            if dates:
                table, cands = (FundDaily, [key]) if len(key) != 6 else (StockDaily, None)
                if cands is None:
                    from src.utils.stock_code import code_candidates

                    cands = code_candidates(key)
                found = session.query(table.trade_date, table.close).filter(table.code.in_(cands), table.trade_date.in_(dates)).all()
                closes = {d: c for d, c in found}
                if table is StockDaily and not closes:
                    # 6 位代码也可能是 ETF（存在 fund_daily）
                    found = session.query(FundDaily.trade_date, FundDaily.close).filter(FundDaily.code == key, FundDaily.trade_date.in_(dates)).all()
                    closes = {d: c for d, c in found}
            return [
                {
                    "id": r.id,
                    "created_at": r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else "",
                    "trade_date": r.trade_date or "",
                    "score": r.score,
                    "action": normalize_action(r.action) or "",
                    "close": closes.get(r.trade_date),
                }
                for r in rows
            ]

    def delete_diagnosis(self, diagnosis_id: int) -> bool:
        """删除一条诊断记录；不存在返回 False。"""
        with get_db_session(self.db_path) as session:
            r = session.get(StockDiagnosis, diagnosis_id)
            if r is None:
                return False
            session.delete(r)
        return True

    def delete_diagnoses(self, ids: list[int] | None = None, code: str | None = None) -> int:
        """只能按显式 ID 集合或单一标的清理；禁止空条件删除全部历史。"""
        if bool(ids) == bool(code):
            raise ValueError("必须且只能指定诊断 ID 列表或股票代码")
        with get_db_session(self.db_path) as session:
            query = session.query(StockDiagnosis)
            query = query.filter(StockDiagnosis.id.in_(set(ids))) if ids else query.filter(StockDiagnosis.code == diagnosis_code(code))
            return query.delete(synchronize_session=False)

    def get_dashboard_snapshot(self, for_date: str | None = None) -> dict:
        """首页（交易决策）所需的数据快照；资讯流单独由 get_unified_news() 提供。"""
        target_date = for_date or date.today().strftime("%Y-%m-%d")

        # 智能回退：如果今天没有评分数据，回退到最近有数据的日期
        score_date = self._resolve_score_date(target_date)

        # 涨停数据：优先用今日，无则回退到最近有数据的日期
        limit_up_stocks = self.get_limit_up_list(target_date)
        limit_up_date = target_date
        if not limit_up_stocks:
            limit_up_date = self._resolve_limit_up_date(target_date)
            if limit_up_date != target_date:
                limit_up_stocks = self.get_limit_up_list(limit_up_date)

        return {
            "today": target_date,
            "score_date": score_date,
            "limit_up_date": limit_up_date,
            "top_stocks": self.get_top_stocks(score_date),
            "limit_up_count": len(limit_up_stocks),
            "limit_up_stocks": limit_up_stocks,
            "signals": self.get_signals(score_date),
            "trade_focus": self.get_trade_focus_rows(score_date, target_date),
            "premarket_predictions": self.get_premarket_predictions(),
            "market_overview": self.get_market_overview(),
        }

    def _resolve_limit_up_date(self, preferred_date: str) -> str:
        """如果指定日期无涨停数据，回退到最近有数据的日期。"""
        try:
            with get_db_session(self.db_path) as session:
                latest = (
                    session.query(LimitUpStock.trade_date)
                    .order_by(LimitUpStock.trade_date.desc())
                    .first()
                )
                if latest:
                    return latest[0]
        except Exception:
            pass
        return preferred_date

    def get_market_overview(self) -> dict:
        """获取市场全局概况缓存数据。"""
        overview = {}
        try:
            from src.collectors.stock_data import StockDataCollector
            cache = StockDataCollector._market_overview_cache
            if cache:
                overview = dict(cache)
        except Exception:
            pass
        # 附加 AI 大盘点评
        try:
            from src.services.trade_advisor import TradeAdvisor
            comment = TradeAdvisor._last_market_comment
            if comment:
                overview["ai_market_comment"] = comment
        except Exception:
            pass
        return overview

    def _resolve_score_date(self, preferred_date: str) -> str:
        """
        如果 preferred_date 有评分数据就直接用；
        否则回退到最近一天有评分数据的日期。
        """
        try:
            with get_db_session(self.db_path) as session:
                cnt = session.query(StockScore).filter(
                    StockScore.score_date == preferred_date
                ).count()
                if cnt > 0:
                    return preferred_date
                # 回退到最近有数据的日期
                latest = (
                    session.query(StockScore.score_date)
                    .order_by(StockScore.score_date.desc())
                    .first()
                )
                if latest:
                    logger.info(f"今日({preferred_date})无评分数据，回退到 {latest[0]}")
                    return latest[0]
        except Exception as e:
            logger.error(f"回退评分日期失败: {e}")
        return preferred_date

    def get_top_stocks(self, score_date: str) -> list[dict]:
        try:
            with get_db_session(self.db_path) as session:
                records = (
                    session.query(StockScore)
                    .filter(StockScore.score_date == score_date)
                    .order_by(StockScore.rank.asc())
                    .limit(30)  # 多取一些，展示层一字板过滤后截取 Top10
                    .all()
                )
                return [
                    {
                        "rank": r.rank,
                        "code": r.code,
                        "name": r.name,
                        "composite_score": r.composite_score,
                        "sentiment_score": r.sentiment_score,
                        "limit_up_score": r.limit_up_score,
                        "capital_score": r.capital_flow_score,
                        "tech_score": r.technical_score,
                        "global_score": r.global_score,
                        "recommendation": r.recommendation,
                    }
                    for r in records
                ]
        except Exception as e:
            logger.error(f"获取Top选股失败: {e}")
            return []

    def get_limit_up_list(self, trade_date: str) -> list[dict]:
        try:
            with get_db_session(self.db_path) as session:
                records = (
                    session.query(LimitUpStock)
                    .filter(LimitUpStock.trade_date == trade_date)
                    .order_by(LimitUpStock.continuous_days.desc())
                    .all()
                )
                return [
                    {
                        "code": r.code,
                        "name": r.name,
                        "continuous_days": r.continuous_days,
                        "limit_up_type": r.limit_up_type or "",
                        "sector": r.sector or "",
                        "reason": r.reason or "",
                        "seal_amount": r.seal_amount,
                        "seal_ratio": r.seal_ratio,
                        "first_limit_time": r.first_limit_time or "",
                        "last_limit_time": r.last_limit_time or "",
                        "open_count": r.open_count or 0,
                        "close": r.close,
                        "change_pct": r.change_pct,
                        "circ_mv": r.circ_mv,
                    }
                    for r in records
                ]
        except Exception as e:
            logger.error(f"获取涨停列表失败: {e}")
            return []

    def get_signals(self, signal_date: str) -> list[dict]:
        try:
            with get_db_session(self.db_path) as session:
                records = (
                    session.query(TradeSignal)
                    .filter(TradeSignal.signal_date == signal_date)
                    .order_by(TradeSignal.composite_score.desc())
                    .all()
                )
                return [
                    {
                        "code": r.code,
                        "name": r.name,
                        "signal_type": r.signal_type,
                        "signal_strength": r.signal_strength,
                        "composite_score": r.composite_score,
                        "reason": r.reason,
                        "ai_verdict": getattr(r, "ai_verdict", None) or "",
                        "ai_advice": getattr(r, "ai_advice", None) or "",
                    }
                    for r in records
                ]
        except Exception as e:
            logger.error(f"获取交易信号失败: {e}")
            return []

    # 美股头部公司关键词（用于判断是否为头部企业财报/研报）
    _US_HEAD_KEYWORDS = {
        "英伟达", "NVDA", "苹果", "AAPL", "特斯拉", "TSLA", "微软", "MSFT",
        "谷歌", "GOOGL", "亚马逊", "AMZN", "Meta", "META", "AMD",
        "台积电", "TSM", "博通", "AVGO", "ASML", "高通", "QCOM",
        "美光", "ARM", "理想汽车", "蔚来", "小鹏",
    }
    _EARNINGS_KEYWORDS = {
        "财报", "业绩", "营收", "净利", "超预期", "不及预期", "上调", "下调",
        "指引", "研报", "评级", "目标价", "回购", "分红", "拆股", "并购",
        "EPS", "earnings", "revenue",
    }

    def get_unified_news(self) -> list[dict]:
        """
        合并财联社/韭研公社/国际新闻/美股财报研报为统一资讯流。
        置顶规则（从高到低）：
          1. 头部企业财报/研报（红色置顶）
          2. 财联社红色/重要新闻（红色置顶）
          3. 高重要性国际新闻（importance>=7）
          4. 其余按时间倒序
        AI决策标签直接从 SentimentAnalysis 表关联补全。
        """
        rows: list[dict] = []
        try:
            with get_db_session(self.db_path) as session:
                cutoff = datetime.now() - timedelta(hours=24)
                # ---- 1) 采集各来源 ----
                records: list[FinanceNews] = []
                source_limits = {
                    "cailianshe": 150,
                    "rss": 100,  # 用户订阅的 RSS 资讯源（level 字段为源名称）
                    # jiuyan 已移到「研报热点」Tab 独立显示
                }
                for src, limit_n in source_limits.items():
                    src_rows = (
                        session.query(FinanceNews)
                        .filter(
                            FinanceNews.source == src,
                            FinanceNews.collected_at >= cutoff,
                        )
                        .order_by(FinanceNews.news_time.desc().nullslast(), FinanceNews.collected_at.desc())
                        .limit(limit_n)
                        .all()
                    )
                    records.extend(src_rows)

                # ---- 2) 国际新闻 + 美股财报研报 (来自 GlobalNews 表) ----
                global_records = (
                    session.query(GlobalNews)
                    .filter(GlobalNews.collected_at >= cutoff)
                    .order_by(GlobalNews.importance.desc(), GlobalNews.collected_at.desc())
                    .limit(120)
                    .all()
                )

                # ---- 3) 直接从 SentimentAnalysis 加载实时AI决策结果 ----
                ai_map_exact: dict[str, str] = {}
                ai_map_fuzzy: dict[str, str] = {}
                try:
                    ai_records = (
                        session.query(SentimentAnalysis)
                        .filter(
                            SentimentAnalysis.source_type == "cailianshe_realtime",
                            SentimentAnalysis.analyzed_at >= cutoff,
                        )
                        .all()
                    )
                    # 同一标题可能有多条（多行业），按标题聚合
                    from collections import defaultdict
                    title_ais: dict[str, list] = defaultdict(list)
                    for sa in ai_records:
                        raw = (sa.original_text or "").strip()
                        if raw:
                            title_ais[raw].append(sa)

                    for raw_title, sa_list in title_ais.items():
                        tag_parts = []
                        for sa in sa_list[:3]:  # 最多3个行业
                            sentiment_cn = {"bullish": "利好", "bearish": "利空", "neutral": "中性"}.get(
                                sa.sentiment or "", ""
                            )
                            sector = sa.related_sector or ""
                            stock = sa.related_stock_name or ""
                            reason = sa.analysis_reason or ""
                            parts = []
                            if sentiment_cn:
                                parts.append(sentiment_cn)
                            if sector:
                                parts.append(f"行业:{sector}")
                            if stock:
                                # stock 可能已经是 "个股:xxx" 格式（新版写入）
                                if "个股:" not in stock and "(" not in stock:
                                    parts.append(f"个股:{stock}")
                                else:
                                    parts.append(stock)
                            if reason:
                                parts.append(f"→{reason}")
                            if parts:
                                tag_parts.append(" ".join(parts))
                        if tag_parts:
                            label = "AI: " + " | ".join(tag_parts)
                            ai_map_exact[raw_title[:120]] = label
                            ai_map_fuzzy[raw_title[:30]] = label
                except Exception as e:
                    logger.debug(f"加载实时AI标签失败: {e}")

                # ---- 4) 统一为内部行结构 ----
                # 每行: {time, source, level, title, url, tags, _pin_priority}
                #   _pin_priority: 0=普通, 1=高重要性国际, 2=财联社红色重要, 3=头部企业财报研报
                all_items: list[dict] = []

                # 4a) FinanceNews (财联社/韭研)
                for r in records:
                    title = (r.title or "").strip()
                    if not title:
                        continue
                    src_cn = {"cailianshe": "财联社", "jiuyan": "韭研公社"}.get(r.source, r.source)
                    if r.source == "rss":  # 来源显示为「RSS·源名称」
                        src_cn = f"RSS·{r.category}" if r.category else "RSS"
                    level = r.category or ""
                    pin = 0
                    if r.source == "cailianshe":
                        if level == "red":
                            level = "红色重大"
                            pin = 2
                        elif level == "important":
                            level = "重要快讯"
                            pin = 2
                        elif level == "normal":
                            level = "普通快讯"

                    # AI标签
                    tags = r.tags or ""
                    ai_label = ai_map_exact.get(title[:120]) or ai_map_fuzzy.get(title[:30]) or ""
                    if ai_label:
                        base_tags = tags.split(" | AI:")[0].strip() if "AI:" in tags else tags
                        tags = f"{ai_label} | {base_tags}" if base_tags else ai_label

                    news_time = r.news_time or r.collected_at
                    all_items.append({
                        "time": news_time.strftime("%H:%M") if news_time else "",
                        "source": src_cn,
                        "level": level,
                        "title": f"[{src_cn}] {title}",
                        "url": r.url or "",
                        "tags": tags,
                        "_pin": pin,
                        "_sort_time": news_time or datetime.min,
                        "_dedupe": (r.source, title[:80]),
                    })

                # 4b) GlobalNews (国际新闻/美股财报)
                _global_source_map = {
                    "wallstreetcn": "华尔街见闻",
                    "wallstreetcn_us": "华尔街见闻",
                    "jin10": "金十数据",
                    "eastmoney_global": "东方财富",
                    "eastmoney_us_earnings": "东财美股",
                    "eastmoney": "东方财富",
                    "cailianshe_global": "财联社国际",
                    "akshare": "AKShare",
                }
                for g in global_records:
                    title = (g.title or "").strip()
                    if not title:
                        continue
                    src_cn = _global_source_map.get(g.source, g.source)
                    category = g.category or ""
                    importance = g.importance or 5
                    pin = 0

                    # 判断是否为头部企业财报/研报 → 最高置顶优先级
                    is_head_earnings = self._is_head_company_earnings(title)
                    if is_head_earnings:
                        pin = 3
                        level = "头部财报"
                    elif importance >= MAJOR_IMPORTANCE_THRESHOLD:
                        pin = 2
                        level = "重大国际"
                    elif importance >= HIGH_IMPORTANCE_THRESHOLD:
                        pin = 1
                        level = "重要国际"
                    else:
                        level = category  # 分类: 美联储/经济数据/大宗商品等

                    # 标签: 分类 + 重要性
                    tags_parts = []
                    if category:
                        tags_parts.append(category)
                    if is_head_earnings:
                        tags_parts.append("头部企业")

                    # URL: 优先用采集时保存的，否则按来源构造默认链接
                    news_url = g.url or ""
                    if not news_url:
                        news_url = self._fallback_global_url(g.source)

                    news_time = g.news_time or g.collected_at
                    all_items.append({
                        "time": news_time.strftime("%H:%M") if news_time else "",
                        "source": src_cn,
                        "level": level,
                        "title": f"[{src_cn}] {title}",
                        "url": news_url,
                        "tags": " | ".join(tags_parts) if tags_parts else "",
                        "_pin": pin,
                        "_sort_time": news_time or datetime.min,
                        "_dedupe": (g.source, title[:80]),
                    })

                # ---- 5) 去重 ----
                seen = set()
                unique_items = []
                for item in all_items:
                    key = item["_dedupe"]
                    # 额外用标题前60字跨源去重
                    title_key = item["title"][:60]
                    if key in seen or title_key in seen:
                        continue
                    seen.add(key)
                    seen.add(title_key)
                    unique_items.append(item)

                # ---- 6) 排序: 置顶优先 → 时间倒序 ----
                unique_items.sort(
                    key=lambda x: (x["_pin"], x["_sort_time"]),
                    reverse=True,
                )

                # 截取前 250 条
                rows.extend({
                        "time": item["time"],
                        "source": item["source"],
                        "level": item["level"],
                        "title": item["title"],
                        "url": item["url"],
                        "tags": item["tags"],
                    } for item in unique_items[:250])
        except Exception as e:
            logger.error(f"获取统一资讯流失败: {e}")
        return rows

    @staticmethod
    def _fallback_global_url(source: str) -> str:
        """为没有具体URL的国际新闻条目提供来源首页链接。"""
        _source_home = {
            "wallstreetcn": "https://wallstreetcn.com/live",
            "wallstreetcn_us": "https://wallstreetcn.com/markets/us",
            "jin10": "https://www.jin10.com/",
            "eastmoney_global": "https://finance.eastmoney.com/a/cywjh.html",
            "eastmoney_us_earnings": "https://stock.eastmoney.com/us.html",
            "cailianshe_global": "https://www.cls.cn/telegraph",
        }
        return _source_home.get(source, "")

    def _is_head_company_earnings(self, title: str) -> bool:
        """判断标题是否涉及头部公司财报/研报。"""
        has_company = any(kw in title for kw in self._US_HEAD_KEYWORDS)
        has_earnings = any(kw in title for kw in self._EARNINGS_KEYWORDS)
        return has_company and has_earnings

    def get_premarket_predictions(self, target_date: str | None = None) -> list[dict]:
        """
        获取涨停预测数据（signal_type='premarket'）。
        智能查找：优先查最新的预测日期（盘后预测存为下一交易日）。
        """
        rows = []
        try:
            with get_db_session(self.db_path) as session:
                if target_date is None:
                    # 智能查找：取最新的 premarket signal_date
                    from sqlalchemy import func
                    latest = (
                        session.query(func.max(TradeSignal.signal_date))
                        .filter(TradeSignal.signal_type == "premarket")
                        .scalar()
                    )
                    target_date = latest or date.today().strftime("%Y-%m-%d")
                signals = (
                    session.query(TradeSignal)
                    .filter(
                        TradeSignal.signal_date == target_date,
                        TradeSignal.signal_type == "premarket",
                    )
                    .order_by(TradeSignal.composite_score.desc())
                    .limit(10)
                    .all()
                )

                # 批量查询所有预测股票的最新涨跌幅
                # StockDaily.code 带市场前缀(sz002272/sh603019)，预测code是纯数字(002272)
                bare_codes = [sig.code for sig in signals if sig.code]
                change_map: dict[str, float] = {}
                price_map: dict[str, float] = {}
                if bare_codes:
                    from sqlalchemy import func as sa_func
                    # 构建两种前缀的查询 codes
                    prefixed = []
                    prefix_to_bare: dict[str, str] = {}
                    for c in bare_codes:
                        pc = prefixed_code(c)
                        prefixed.append(pc)
                        prefix_to_bare[pc] = c
                    # 同时尝试原始 code 和带前缀的 code（兼容不同存储格式）
                    all_codes = list(set(bare_codes + prefixed))
                    sub = (
                        session.query(
                            StockDaily.code,
                            sa_func.max(StockDaily.trade_date).label("max_date"),
                        )
                        .filter(StockDaily.code.in_(all_codes))
                        .group_by(StockDaily.code)
                        .subquery()
                    )
                    latest_rows = (
                        session.query(StockDaily.code, StockDaily.change_pct, StockDaily.close)
                        .join(sub, (StockDaily.code == sub.c.code) & (StockDaily.trade_date == sub.c.max_date))
                        .all()
                    )
                    for r in latest_rows:
                        # 映射回原始 bare code
                        bare = prefix_to_bare.get(r.code, r.code)
                        if r.change_pct is not None:
                            change_map[bare] = r.change_pct
                        if r.close is not None:
                            price_map[bare] = r.close

                for i, sig in enumerate(signals):
                    raw_reason = sig.reason or ""
                    # 解析结构化 reason: "##TYPE:xxx##TIME:xxx##SRC:xxx##原因"
                    predict_type = ""
                    target_time = ""
                    source = "涨停板"
                    display_reason = raw_reason
                    if raw_reason.startswith("##TYPE:"):
                        try:
                            _, rest = raw_reason.split("##TYPE:", 1)
                            type_part, rest = rest.split("##TIME:", 1)
                            # 兼容新旧格式
                            if "##SRC:" in rest:
                                time_part, rest = rest.split("##SRC:", 1)
                                src_part, reason_text = rest.split("##", 1)
                                source = src_part.strip() or "涨停板"
                            else:
                                time_part, reason_text = rest.split("##", 1)
                            predict_type = type_part.strip()
                            target_time = time_part.strip()
                            display_reason = reason_text.strip()
                        except ValueError:
                            display_reason = raw_reason

                    rows.append({
                        "rank": i + 1,
                        "code": sig.code,
                        "name": sig.name or "",
                        "change_pct": change_map.get(sig.code),
                        "close": price_map.get(sig.code),
                        "ai_verdict": sig.ai_verdict or "",
                        "confidence": int((sig.signal_strength or 0) * 10),
                        "composite_score": sig.composite_score or 0,
                        "predict_type": predict_type,
                        "target_time": target_time,
                        "source": source,
                        "reason": display_reason,
                        "ai_advice": sig.ai_advice or "",
                    })
        except Exception as e:
            logger.error(f"获取盘前预测失败: {e}")
        return rows

    def get_trade_focus_rows(self, score_date: str, market_date: str | None = None) -> list[dict]:
        """
        合并 十大选股 + 交易信号 + 涨停原因 为一个决策表。
        展示层一字板过滤：即使旧评分包含一字板也会被剔除。
        优化：使用单个数据库会话完成所有查询（减少 5→1 次会话创建）。
        """
        from src.strategy.scorer import _is_yizi_ban

        if market_date is None:
            market_date = date.today().strftime("%Y-%m-%d")

        try:
            with get_db_session(self.db_path) as session:
                # ── 单会话内一次性查询所有需要的数据 ──

                # 1. 十大选股
                top_records = (
                    session.query(StockScore)
                    .filter(StockScore.score_date == score_date)
                    .order_by(StockScore.composite_score.desc())
                    .limit(20)
                    .all()
                )
                top = [
                    {
                        "code": r.code,
                        "name": r.name,
                        "composite_score": r.composite_score,
                        "recommendation": r.recommendation,
                    }
                    for r in top_records
                ]

                # 2. 交易信号
                sig_records = (
                    session.query(TradeSignal)
                    .filter(TradeSignal.signal_date == score_date)
                    .all()
                )
                signal_map = {
                    r.code: {
                        "signal_type": r.signal_type,
                        "signal_strength": r.signal_strength,
                        "reason": r.reason or "",
                        "ai_verdict": r.ai_verdict or "",
                        "ai_advice": r.ai_advice or "",
                    }
                    for r in sig_records
                }

                # 3. 涨停池（优先今天，其次评分日）
                lu_records = (
                    session.query(LimitUpStock)
                    .filter(LimitUpStock.trade_date == market_date)
                    .all()
                )
                if not lu_records and market_date != score_date:
                    lu_records = (
                        session.query(LimitUpStock)
                        .filter(LimitUpStock.trade_date == score_date)
                        .all()
                    )
                limit_map = {
                    r.code: {
                        "continuous_days": r.continuous_days,
                        "sector": r.sector or "",
                        "reason": r.reason or "",
                    }
                    for r in lu_records
                }
                lu_map_orm = {r.code: r for r in lu_records}

                # 4. 一字板判定（仅查询出现在 top 中的股票日线数据）
                # StockDaily.code 可能带市场前缀(sz/sh/bj)，需要兼容两种格式
                top_codes = [item["code"] for item in top]
                top_codes_prefixed = []
                _prefix_bare_map: dict[str, str] = {}
                for c in top_codes:
                    pc = prefixed_code(c)
                    top_codes_prefixed.append(pc)
                    _prefix_bare_map[pc] = c
                all_query_codes = list(set(top_codes + top_codes_prefixed))
                sd_records = (
                    session.query(StockDaily)
                    .filter(
                        StockDaily.trade_date == score_date,
                        StockDaily.code.in_(all_query_codes),
                    )
                    .all()
                ) if top_codes else []
                # 映射回 bare code
                sd_map_orm: dict[str, StockDaily] = {}
                for r in sd_records:
                    bare = _prefix_bare_map.get(r.code, r.code)
                    sd_map_orm[bare] = r

                yizi_codes: set[str] = set()
                for code, lu in lu_map_orm.items():
                    daily = sd_map_orm.get(code)
                    if _is_yizi_ban(lu, daily):
                        yizi_codes.add(code)
                # 会话退出时提交会让 ORM 对象过期，之后再读属性会报 DetachedInstanceError，在会话内取出涨幅
                change_map = {code: r.change_pct for code, r in sd_map_orm.items()}

                if yizi_codes:
                    logger.info(f"展示层一字板过滤: 剔除 {len(yizi_codes)} 只一字板")

        except Exception as e:
            logger.error(f"交易决策数据查询异常: {e}")
            return []

        rows: list[dict] = []
        rank_counter = 0
        for item in top:
            code = item.get("code")
            if code in yizi_codes:
                continue
            rank_counter += 1
            sig = signal_map.get(code, {})
            lim = limit_map.get(code, {})
            rows.append(
                {
                    "rank": rank_counter,
                    "code": code,
                    "name": item.get("name"),
                    "change_pct": change_map.get(code),
                    "recommendation": item.get("recommendation"),
                    "signal_type": sig.get("signal_type", ""),
                    "signal_strength": sig.get("signal_strength"),
                    "composite_score": item.get("composite_score"),
                    "continuous_days": lim.get("continuous_days"),
                    "sector": lim.get("sector", ""),
                    "limit_reason": lim.get("reason", ""),
                    "signal_reason": sig.get("reason", ""),
                    "ai_verdict": sig.get("ai_verdict", ""),
                    "ai_advice": sig.get("ai_advice", ""),
                }
            )
            if rank_counter >= TRADE_FOCUS_MAX_ROWS:
                break
        return rows

    @staticmethod
    def _stock_code_variants(code: str) -> tuple[str, list[str]]:
        """
        返回:
        - bare_code: 6位纯数字代码
        - variants: 可用于查询数据库的代码候选（含前缀与裸码）
        """
        raw = (code or "").strip().lower()
        if not raw:
            return "", []

        if len(raw) == PREFIXED_STOCK_CODE_LENGTH and raw[:2] in {"sh", "sz", "bj"} and raw[2:].isdigit():
            bare = raw[2:]
        elif raw.isdigit():
            bare = raw
        else:
            bare = "".join(ch for ch in raw if ch.isdigit())

        if len(bare) != STOCK_CODE_LENGTH:
            return "", []

        return bare, [bare, prefixed_code(bare)]

    @staticmethod
    def _code_match_priority(record_code: str, bare_code: str) -> int:
        code = (record_code or "").strip().lower()
        if code == bare_code:
            return 3
        if code.endswith(bare_code):
            return 2
        return 1

    def get_stock_daily_history(self, code: str, limit: int | None = None) -> list[dict]:
        """
        按股票代码查询日线历史（兼容裸码与带市场前缀存储）。
        """
        bare_code, candidates = self._stock_code_variants(code)
        if not bare_code or not candidates:
            return []

        try:
            with get_db_session(self.db_path) as session:
                records = (
                    session.query(StockDaily)
                    .filter(StockDaily.code.in_(candidates))
                    .order_by(StockDaily.trade_date.asc(), StockDaily.id.asc())
                    .all()
                )

                # 同一天若存在裸码/前缀重复记录，优先使用更高匹配优先级
                by_date: dict[str, dict] = {}
                for r in records:
                    if not r.trade_date:
                        continue
                    row = {
                        "code": bare_code,
                        "name": r.name or "",
                        "trade_date": r.trade_date,
                        "open": r.open,
                        "high": r.high,
                        "low": r.low,
                        "close": r.close,
                        "change_pct": r.change_pct,
                        "volume": r.volume,
                        "amount": r.amount,
                        "turnover": r.turnover,
                        "total_mv": r.total_mv,
                        "circ_mv": r.circ_mv,
                        "_priority": self._code_match_priority(r.code, bare_code),
                        "source": r.source,
                        "price_adjustment": r.price_adjustment,
                        "updated_at": r.updated_at.isoformat(timespec="seconds") if r.updated_at else None,
                    }
                    existing = by_date.get(r.trade_date)
                    if (existing is None) or (row["_priority"] > existing["_priority"]):
                        by_date[r.trade_date] = row

                rows = [by_date[k] for k in sorted(by_date.keys())]
                for row in rows:
                    row.pop("_priority", None)
                # 历史回补、按需补齐的日线没有名称（个股页标题会只显示代码），用股票列表或最近一条带名称的行补上
                if any(not row["name"] for row in rows):
                    info = session.query(StockInfo.name).filter(StockInfo.code == bare_code).first()
                    fallback = (info[0] if info and info[0] else "") or next((r["name"] for r in reversed(rows) if r["name"]), "")
                    for row in rows:
                        row["name"] = row["name"] or fallback

                if limit and limit > 0 and len(rows) > limit:
                    rows = rows[-limit:]
                return rows
        except Exception as e:
            logger.error(f"查询股票历史失败 code={code}: {e}")
            return []
