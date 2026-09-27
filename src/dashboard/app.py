"""
Web 仪表盘 - FastAPI 应用
展示每日选股结果、涨停分析、舆情信息等
"""

from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from loguru import logger

from src.config_loader import load_config
from src.database.db import get_db_session
from src.database.models import (
    FinanceNews,
    GlobalImpactAnalysis,
    GlobalNews,
    LimitUpStock,
    StockScore,
    TradeSignal,
)

app = FastAPI(title="A股量化交易系统", version="0.1.0")

templates_dir = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(templates_dir))

CONFIG = load_config()
DB_PATH = CONFIG.get("database", {}).get("sqlite_path", "data/quant.db")
HIGH_IMPORTANCE_THRESHOLD = 7
MEDIUM_IMPORTANCE_THRESHOLD = 4


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """首页 - 仪表盘概览"""
    today = date.today().strftime("%Y-%m-%d")
    cailianshe = _get_cailianshe_data()
    limit_up_stocks = _get_limit_up_list(today)
    data = {
        "request": request,
        "today": today,
        "top_stocks": _get_top_stocks(today),
        "limit_up_count": _get_limit_up_count(today),
        "limit_up_stocks": limit_up_stocks,
        "global_impact": _get_global_impact(),
        "signals": _get_signals(today),
        "cailianshe_red": cailianshe["red"],
        "cailianshe_important": cailianshe["important"],
        "cailianshe_normal": cailianshe["normal"],
        "cailianshe_count": cailianshe["total"],
        "xueqiu_data": _get_xueqiu_data(),
        "jiuyan_data": _get_jiuyan_data(),
        "global_news": _get_global_news(),
    }
    return templates.TemplateResponse("index.html", data)


@app.get("/api/top-stocks")
async def api_top_stocks(score_date: str = None):
    """API - 获取 Top 选股列表"""
    if not score_date:
        score_date = date.today().strftime("%Y-%m-%d")
    return {"date": score_date, "stocks": _get_top_stocks(score_date)}


@app.get("/api/limit-up")
async def api_limit_up(trade_date: str = None):
    """API - 获取涨停板数据"""
    if not trade_date:
        trade_date = date.today().strftime("%Y-%m-%d")
    return {"date": trade_date, "stocks": _get_limit_up_list(trade_date)}


@app.get("/api/signals")
async def api_signals(signal_date: str = None):
    """API - 获取交易信号"""
    if not signal_date:
        signal_date = date.today().strftime("%Y-%m-%d")
    return {"date": signal_date, "signals": _get_signals(signal_date)}


@app.get("/api/global-impact")
async def api_global_impact():
    """API - 获取国际因子分析"""
    return _get_global_impact()


@app.get("/api/xueqiu")
async def api_xueqiu():
    """API - 获取雪球数据"""
    return {"data": _get_xueqiu_data()}


@app.get("/api/jiuyan")
async def api_jiuyan():
    """API - 获取韭研公社数据"""
    return {"data": _get_jiuyan_data()}


@app.get("/api/cailianshe")
async def api_cailianshe():
    """API - 获取财联社数据"""
    return _get_cailianshe_data()


@app.get("/api/global-news")
async def api_global_news():
    """API - 获取国际新闻"""
    return {"data": _get_global_news()}


@app.post("/api/refresh-all")
async def api_refresh_all():
    """API - 手动刷新所有数据源（财联社、雪球、韭研公社）"""
    results = {"cailianshe": 0, "xueqiu": 0, "jiuyan": 0}
    try:
        from src.collectors.cailianshe import CailiansheCollector
        from src.collectors.jiuyan import JiuyanCollector
        from src.collectors.xueqiu import XueqiuCollector
        from src.database.db import init_db

        init_db(DB_PATH)

        # 采集财联社
        try:
            cls_collector = CailiansheCollector()
            cls_items = cls_collector.collect()
            with get_db_session(DB_PATH) as session:
                for item in cls_items:
                    record = FinanceNews(
                        source="cailianshe",
                        title=item.get("title", ""),
                        content=item.get("content", ""),
                        news_time=item.get("news_time"),
                        category=item.get("importance", "normal"),  # red/important/normal
                        tags=item.get("tags", ""),
                        url=item.get("url", ""),
                    )
                    session.add(record)
                session.commit()
            results["cailianshe"] = len(cls_items)
        except Exception as e:
            logger.error(f"刷新财联社数据失败: {e}")

        # 采集雪球
        try:
            xueqiu = XueqiuCollector()
            xueqiu_items = xueqiu.collect()
            with get_db_session(DB_PATH) as session:
                for item in xueqiu_items:
                    record = FinanceNews(
                        source="xueqiu",
                        title=item.get("title", ""),
                        content=item.get("content", ""),
                        category=item.get("category", ""),
                        url=item.get("url", ""),
                        tags=item.get("stock_code", ""),
                    )
                    session.add(record)
                session.commit()
            results["xueqiu"] = len(xueqiu_items)
        except Exception as e:
            logger.error(f"刷新雪球数据失败: {e}")

        # 采集韭研公社
        try:
            jiuyan = JiuyanCollector()
            jiuyan_items = jiuyan.collect()
            with get_db_session(DB_PATH) as session:
                for item in jiuyan_items:
                    record = FinanceNews(
                        source="jiuyan",
                        title=item.get("title", ""),
                        content=item.get("content", ""),
                        category=item.get("category", ""),
                        url=item.get("url", ""),
                    )
                    session.add(record)
                session.commit()
            results["jiuyan"] = len(jiuyan_items)
        except Exception as e:
            logger.error(f"刷新韭研数据失败: {e}")

    except Exception as e:
        logger.error(f"刷新数据失败: {e}")
        return {"status": "error", "message": str(e)}
    return {"status": "ok", "collected": results}


# ---- 数据查询辅助函数 ----

def _get_top_stocks(score_date: str) -> list[dict]:
    try:
        with get_db_session(DB_PATH) as session:
            records = (
                session.query(StockScore)
                .filter(StockScore.score_date == score_date)
                .order_by(StockScore.rank.asc())
                .limit(10)
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


def _get_limit_up_count(trade_date: str) -> int:
    try:
        with get_db_session(DB_PATH) as session:
            return session.query(LimitUpStock).filter(
                LimitUpStock.trade_date == trade_date
            ).count()
    except Exception:
        return 0


def _get_limit_up_list(trade_date: str) -> list[dict]:
    try:
        with get_db_session(DB_PATH) as session:
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
    except Exception:
        return []


def _get_cailianshe_data() -> dict:
    """获取财联社快讯数据，按重要性分组（red > important > normal）"""
    result = {"red": [], "important": [], "normal": [], "total": 0}
    try:
        with get_db_session(DB_PATH) as session:
            cutoff = datetime.now() - timedelta(hours=24)
            records = (
                session.query(FinanceNews)
                .filter(
                    FinanceNews.source == "cailianshe",
                    FinanceNews.collected_at >= cutoff,
                )
                .order_by(FinanceNews.news_time.desc().nullslast(),
                          FinanceNews.collected_at.desc())
                .limit(100)
                .all()
            )
            seen = set()
            for r in records:
                key = r.title[:80]
                if key in seen:
                    continue
                seen.add(key)
                item = {
                    "title": r.title,
                    "content": r.content or "",
                    "importance": r.category or "normal",  # red/important/normal
                    "tags": r.tags or "",
                    "url": r.url or "",
                    "time": r.news_time.strftime("%H:%M") if r.news_time else (
                        r.collected_at.strftime("%H:%M") if r.collected_at else ""
                    ),
                }
                imp = r.category or "normal"
                if imp == "red":
                    result["red"].append(item)
                elif imp == "important":
                    result["important"].append(item)
                else:
                    result["normal"].append(item)
            result["total"] = len(result["red"]) + len(result["important"]) + len(result["normal"])
    except Exception as e:
        logger.error(f"获取财联社数据失败: {e}")
    return result


def _get_signals(signal_date: str) -> list[dict]:
    try:
        with get_db_session(DB_PATH) as session:
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
                }
                for r in records
            ]
    except Exception:
        return []


def _get_global_impact() -> dict:
    try:
        with get_db_session(DB_PATH) as session:
            record = (
                session.query(GlobalImpactAnalysis)
                .order_by(GlobalImpactAnalysis.analysis_date.desc())
                .first()
            )
            if record:
                return {
                    "date": record.analysis_date,
                    "direction": record.overall_direction,
                    "impact_score": record.overall_impact_score,
                    "detail": record.analysis_detail,
                }
    except Exception:
        pass
    return {}


def _get_xueqiu_data() -> list[dict]:
    """获取雪球热门讨论数据"""
    try:
        with get_db_session(DB_PATH) as session:
            cutoff = datetime.now() - timedelta(hours=24)
            records = (
                session.query(FinanceNews)
                .filter(
                    FinanceNews.source == "xueqiu",
                    FinanceNews.collected_at >= cutoff,
                )
                .order_by(FinanceNews.collected_at.desc())
                .limit(30)
                .all()
            )
            seen = set()
            results = []
            for r in records:
                if r.title not in seen:
                    seen.add(r.title)
                    results.append({
                        "title": r.title,
                        "content": r.content or "",
                        "category": r.category or "",
                        "stock_code": r.tags or "",
                        "url": r.url or "",
                        "time": r.collected_at.strftime("%H:%M") if r.collected_at else "",
                    })
            return results
    except Exception as e:
        logger.error(f"获取雪球数据失败: {e}")
        return []


def _get_jiuyan_data() -> list[dict]:
    """获取韭研公社数据"""
    try:
        with get_db_session(DB_PATH) as session:
            cutoff = datetime.now() - timedelta(hours=24)
            records = (
                session.query(FinanceNews)
                .filter(
                    FinanceNews.source == "jiuyan",
                    FinanceNews.collected_at >= cutoff,
                )
                .order_by(FinanceNews.collected_at.desc())
                .limit(30)
                .all()
            )
            seen = set()
            results = []
            for r in records:
                if r.title not in seen:
                    seen.add(r.title)
                    results.append({
                        "title": r.title,
                        "content": r.content or "",
                        "category": r.category or "",
                        "url": r.url or "",
                        "time": r.collected_at.strftime("%H:%M") if r.collected_at else "",
                    })
            return results
    except Exception as e:
        logger.error(f"获取韭研公社数据失败: {e}")
        return []


def _get_global_news() -> list[dict]:
    """获取国际重大新闻"""
    try:
        with get_db_session(DB_PATH) as session:
            cutoff = datetime.now() - timedelta(hours=48)
            records = (
                session.query(GlobalNews)
                .filter(GlobalNews.collected_at >= cutoff)
                .order_by(GlobalNews.collected_at.desc())
                .limit(20)
                .all()
            )
            seen = set()
            results = []
            for r in records:
                if r.title not in seen:
                    seen.add(r.title)
                    imp = r.importance or 0
                    imp_label = "high" if imp >= HIGH_IMPORTANCE_THRESHOLD else ("medium" if imp >= MEDIUM_IMPORTANCE_THRESHOLD else "low")
                    results.append({
                        "title": r.title,
                        "source": r.source or "",
                        "category": r.category or "",
                        "importance": imp_label,
                        "direction": r.a_share_impact_direction or "",
                        "time": r.collected_at.strftime("%m-%d %H:%M") if r.collected_at else "",
                    })
            return results
    except Exception as e:
        logger.error(f"获取国际新闻失败: {e}")
        return []


def start_dashboard(config: dict = None):
    """启动 Web 仪表盘"""
    import uvicorn
    cfg = config or load_config()
    dash_cfg = cfg.get("dashboard", {})
    host = dash_cfg.get("host", "0.0.0.0")
    port = dash_cfg.get("port", 8000)
    logger.info(f"Web 仪表盘启动: http://{host}:{port}")
    uvicorn.run(app, host=host, port=port)
