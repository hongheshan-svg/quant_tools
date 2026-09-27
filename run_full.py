"""
完整流程运行 - 采集 + AI分析 + 评分 + Top10
"""

import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

from datetime import date
from loguru import logger

logger.remove()
logger.add(sys.stderr, level="INFO",
           format="<green>{time:HH:mm:ss}</green> | <level>{level:<7}</level> | <level>{message}</level>")

from src.config_loader import load_config, reload_config
from src.database.db import init_db, get_db_session
from src.database.models import LimitUpStock, HotSearch, FinanceNews


def main():
    config = reload_config()  # 强制重新加载获取新的API Key
    db_path = config.get("database", {}).get("sqlite_path", "data/quant.db")
    init_db(db_path)

    today = date.today().strftime("%Y-%m-%d")

    # 检查涨停数据
    with get_db_session(db_path) as session:
        lu_count = session.query(LimitUpStock).filter(LimitUpStock.trade_date == today).count()
        hs_count = session.query(HotSearch).count()

    if lu_count == 0:
        with get_db_session(db_path) as session:
            latest = session.query(LimitUpStock).order_by(LimitUpStock.trade_date.desc()).first()
            if latest:
                today = latest.trade_date
                lu_count = session.query(LimitUpStock).filter(LimitUpStock.trade_date == today).count()

    print(f"\n{'='*70}")
    print(f"  A股舆情驱动量化交易系统 - 完整分析流程")
    print(f"  日期: {today} | 涨停: {lu_count} 只 | 热搜: {hs_count} 条")
    print(f"{'='*70}\n")

    # ========== Step 1: AI 舆情分析 ==========
    print("[1/4] DeepSeek AI 舆情分析...")
    try:
        from src.analyzers.sentiment import SentimentAnalyzer
        analyzer = SentimentAnalyzer(config)
        analyzer.analyze_today()
        print("  --> 舆情分析完成\n")
    except Exception as e:
        print(f"  --> 舆情分析异常: {e}\n")

    # ========== Step 2: 热点题材提取 ==========
    print("[2/4] DeepSeek 热点题材提取...")
    try:
        from src.analyzers.topic_extractor import TopicExtractor
        extractor = TopicExtractor(config)
        themes = extractor.extract_today_themes()
        if themes:
            print(f"  --> 提取到 {len(themes)} 个热点题材:")
            for t in themes[:5]:
                heat = {"high": "🔥高", "medium": "中", "low": "低"}.get(t.get("heat_level", ""), "")
                stocks_str = ", ".join(
                    [s.get("name", s.get("code", "")) for s in t.get("related_stocks", [])[:3]]
                )
                print(f"      [{heat}] {t.get('theme_name', '?')} -> {stocks_str or '暂无关联个股'}")
        else:
            print("  --> 暂无可提取的题材")
        print()
    except Exception as e:
        print(f"  --> 题材提取异常: {e}\n")

    # ========== Step 3: 国际因子分析 ==========
    print("[3/4] DeepSeek 国际因子影响分析...")
    try:
        from src.analyzers.global_impact import GlobalImpactAnalyzer
        gi = GlobalImpactAnalyzer(config)
        impact = gi.analyze_today()
        if impact:
            direction_map = {"bullish": "利好", "bearish": "利空", "neutral": "中性"}
            d = direction_map.get(impact.get("overall_direction", ""), "未知")
            s = impact.get("overall_impact_score", "?")
            print(f"  --> 国际因子: {d} (影响度: {s}/10)")
            summary = impact.get("summary", "")
            if summary:
                print(f"  --> 摘要: {summary[:100]}")
            events = impact.get("key_events", [])
            for ev in events[:3]:
                ev_dir = direction_map.get(ev.get("impact_direction", ""), "")
                print(f"      [{ev_dir}] {ev.get('event', '?')[:60]}")
        else:
            print("  --> 暂无国际数据可分析")
        print()
    except Exception as e:
        print(f"  --> 国际因子分析异常: {e}\n")

    # ========== Step 4: 综合评分 ==========
    print("[4/4] 综合评分引擎...")
    from src.strategy.scorer import CompositeScorer
    scorer = CompositeScorer(config)

    candidates = scorer._get_candidates(today)
    print(f"  --> 候选: {len(candidates)} 只\n")

    scored = []
    for stock in candidates:
        code = stock["code"]
        scores = scorer._score_stock(code)
        scores["code"] = code
        scores["name"] = stock.get("name", "")
        scores["continuous_days"] = stock.get("continuous_days", 1)
        scores["sector"] = stock.get("sector", "")
        scored.append(scores)

    scored.sort(key=lambda x: x["composite_score"], reverse=True)
    for rank, item in enumerate(scored, 1):
        item["rank"] = rank

    scorer._save_scores(scored, today)
    scorer.generate_signals()

    # 显示 Top 10
    top10 = scored[:10]
    rec_map = {"strong_buy": "强推 ★★★", "buy": "推荐 ★★", "hold": "观望 ★", "avoid": "回避"}

    print(f"{'='*90}")
    print(f"  >>> Top 10 最可能继续涨停的股票 ({today}) <<<")
    print(f"{'='*90}")
    print(f"{'排名':>4} {'名称':<10} {'代码':<9} {'连板':>4} {'综合分':>7} {'舆情':>6} {'涨停':>6} {'资金':>6} {'技术':>6} {'国际':>6}  {'建议'}")
    print(f"{'-'*90}")

    for s in top10:
        rec_text = rec_map.get(s["recommendation"], s["recommendation"])
        print(
            f"  #{s['rank']:<3} {s['name']:<10} {s['code']:<9} "
            f"{s.get('continuous_days', '-'):>3}板 "
            f"{s['composite_score']:>7.1f} "
            f"{s['sentiment_score']:>6.1f} "
            f"{s['limit_up_score']:>6.1f} "
            f"{s['capital_score']:>6.1f} "
            f"{s['tech_score']:>6.1f} "
            f"{s['global_score']:>6.1f}  "
            f"{rec_text}"
        )

    print(f"{'-'*90}")

    # 涨停板块
    print(f"\n  涨停板块热度:")
    sector_count = {}
    for s in scored:
        sector = s.get("sector", "").strip()
        if sector:
            for sec in sector.split(","):
                sec = sec.strip()
                if sec:
                    sector_count[sec] = sector_count.get(sec, 0) + 1
    sorted_sectors = sorted(sector_count.items(), key=lambda x: x[1], reverse=True)[:8]
    for sec, count in sorted_sectors:
        bar = "█" * count
        print(f"  {sec:<15} {count:>3} 只  {bar}")

    print(f"\n  Web 仪表盘: http://localhost:8000")
    print(f"{'='*90}\n")


if __name__ == "__main__":
    main()
