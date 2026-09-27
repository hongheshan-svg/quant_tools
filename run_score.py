"""
手动运行评分引擎 - 基于已采集的涨停池数据生成 Top 10
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

from src.config_loader import load_config
from src.database.db import init_db, get_db_session
from src.database.models import LimitUpStock, StockScore, TradeSignal


def main():
    config = load_config()
    db_path = config.get("database", {}).get("sqlite_path", "data/quant.db")
    init_db(db_path)

    today = date.today().strftime("%Y-%m-%d")

    # 先看看涨停池有多少数据
    with get_db_session(db_path) as session:
        lu_count = session.query(LimitUpStock).filter(
            LimitUpStock.trade_date == today
        ).count()

    print(f"\n{'='*60}")
    print(f"  今日涨停股: {lu_count} 只 ({today})")
    print(f"{'='*60}\n")

    if lu_count == 0:
        # 今天是周日，可能没有当日数据，尝试找最近有数据的日期
        with get_db_session(db_path) as session:
            latest = session.query(LimitUpStock).order_by(
                LimitUpStock.trade_date.desc()
            ).first()
            if latest:
                today = latest.trade_date
                lu_count = session.query(LimitUpStock).filter(
                    LimitUpStock.trade_date == today
                ).count()
                print(f"  使用最近交易日数据: {today} ({lu_count} 只涨停)\n")
            else:
                print("  数据库中无涨停数据，请先运行数据采集")
                return

    # 运行综合评分
    print("[1/3] 运行综合评分引擎...")
    from src.strategy.scorer import CompositeScorer
    scorer = CompositeScorer(config)

    # 手动获取候选并评分
    candidates = scorer._get_candidates(today)
    print(f"  过滤后候选: {len(candidates)} 只（已排除ST等）\n")

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

    # 保存到数据库
    scorer._save_scores(scored, today)

    # 生成交易信号
    print("[2/3] 生成交易信号...\n")
    scorer.generate_signals()

    # 显示 Top 10
    top10 = scored[:10]
    print(f"\n{'='*80}")
    print(f"  Top 10 选股推荐 ({today})")
    print(f"{'='*80}")
    print(f"{'排名':>4} {'名称':<8} {'代码':<8} {'连板':>4} {'综合分':>6} {'涨停':>6} {'资金':>6} {'技术':>6} {'国际':>6} {'建议':<6}")
    print(f"{'-'*80}")

    rec_map = {
        "strong_buy": "强推",
        "buy": "推荐",
        "hold": "观望",
        "avoid": "回避",
    }

    for s in top10:
        rec_text = rec_map.get(s["recommendation"], s["recommendation"])
        print(
            f"  #{s['rank']:<3} {s['name']:<8} {s['code']:<8} "
            f"{s.get('continuous_days', '-'):>3}板 "
            f"{s['composite_score']:>6.1f} "
            f"{s['limit_up_score']:>6.1f} "
            f"{s['capital_score']:>6.1f} "
            f"{s['tech_score']:>6.1f} "
            f"{s['global_score']:>6.1f} "
            f"{rec_text:<6}"
        )

    print(f"{'-'*80}")
    print(f"  共评分 {len(scored)} 只股票，以上为综合得分最高的 10 只\n")

    # 显示板块统计
    print(f"{'='*60}")
    print(f"  涨停板块分布 (Top 10)")
    print(f"{'='*60}")
    sector_count = {}
    for s in scored:
        sector = s.get("sector", "").strip()
        if sector:
            for sec in sector.split(","):
                sec = sec.strip()
                if sec:
                    sector_count[sec] = sector_count.get(sec, 0) + 1

    sorted_sectors = sorted(sector_count.items(), key=lambda x: x[1], reverse=True)[:10]
    for sec, count in sorted_sectors:
        bar = "#" * count
        print(f"  {sec:<15} {count:>3} 只  {bar}")

    print(f"\n  Web 仪表盘: http://localhost:8000 (刷新查看最新数据)")
    print()


if __name__ == "__main__":
    main()
