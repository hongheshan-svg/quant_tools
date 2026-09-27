"""
演示脚本 - 一次性运行数据采集和展示
"""

import sys
import io

# Windows 控制台 UTF-8 支持
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

from loguru import logger

from src.config_loader import load_config
from src.database.db import init_db, get_db_session

# 配置日志
logger.remove()
logger.add(sys.stderr, level="INFO",
           format="<green>{time:HH:mm:ss}</green> | <level>{level:<7}</level> | <level>{message}</level>")


def main():
    config = load_config()
    db_path = config.get("database", {}).get("sqlite_path", "data/quant.db")

    # 初始化数据库
    init_db(db_path)

    print("\n" + "=" * 60)
    print("  A股舆情驱动量化交易系统 - 数据采集演示")
    print("=" * 60 + "\n")

    # 1. 采集微博热搜
    print("[1/5] 采集微博热搜...")
    try:
        from src.collectors.weibo import WeiboCollector
        from src.database.models import HotSearch
        from src.database.db import bulk_insert

        collector = WeiboCollector(config)
        items = collector.safe_collect()
        if items:
            records = [
                HotSearch(
                    source="weibo", title=item["title"],
                    rank=item.get("rank"), hot_value=item.get("hot_value"),
                )
                for item in items
            ]
            bulk_insert(records, db_path)
            print(f"  ✓ 微博热搜: {len(items)} 条")
            for item in items[:5]:
                print(f"    #{item.get('rank', '?')} {item['title']}")
        else:
            print("  ⚠ 微博热搜暂无数据（可能需要网络访问）")
    except Exception as e:
        print(f"  ✗ 微博热搜失败: {e}")

    # 2. 采集头条热搜
    print("\n[2/5] 采集头条热搜...")
    try:
        from src.collectors.toutiao import ToutiaoCollector
        collector = ToutiaoCollector(config)
        items = collector.safe_collect()
        if items:
            records = [
                HotSearch(
                    source="toutiao", title=item["title"],
                    rank=item.get("rank"), hot_value=item.get("hot_value"),
                )
                for item in items
            ]
            bulk_insert(records, db_path)
            print(f"  ✓ 头条热搜: {len(items)} 条")
            for item in items[:5]:
                print(f"    #{item.get('rank', '?')} {item['title']}")
        else:
            print("  ⚠ 头条热搜暂无数据")
    except Exception as e:
        print(f"  ✗ 头条热搜失败: {e}")

    # 3. 采集财联社
    print("\n[3/5] 采集财联社快讯...")
    try:
        from src.collectors.cailianshe import CailiansheCollector
        from src.database.models import FinanceNews

        collector = CailiansheCollector(config)
        items = collector.safe_collect()
        if items:
            records = [
                FinanceNews(
                    source="cailianshe", title=item["title"][:100],
                    content=item.get("content"), category="快讯",
                )
                for item in items
            ]
            bulk_insert(records, db_path)
            print(f"  ✓ 财联社快讯: {len(items)} 条")
            for item in items[:5]:
                print(f"    • {item['title'][:80]}")
        else:
            print("  ⚠ 财联社快讯暂无数据")
    except Exception as e:
        print(f"  ✗ 财联社快讯失败: {e}")

    # 4. 采集涨停池数据
    print("\n[4/5] 采集A股涨停池...")
    try:
        from src.collectors.stock_data import StockDataCollector
        collector = StockDataCollector(config)
        collector._collect_limit_up_pool(
            __import__('datetime').date.today().strftime("%Y-%m-%d"), db_path
        )
    except Exception as e:
        print(f"  ✗ 涨停池采集失败: {e}")

    # 5. 采集美股数据
    print("\n[5/5] 采集美股/国际数据...")
    try:
        from src.collectors.us_earnings import USEarningsCollector
        collector = USEarningsCollector(config)
        collector._collect_us_market_overview(
            __import__('datetime').date.today().strftime("%Y-%m-%d"), db_path
        )
    except Exception as e:
        print(f"  ✗ 美股数据采集失败: {e}")

    # 统计
    print("\n" + "=" * 60)
    print("  数据库统计")
    print("=" * 60)
    try:
        from src.database.models import (
            HotSearch, FinanceNews, LimitUpStock, USMarketDaily
        )
        with get_db_session(db_path) as session:
            hs_count = session.query(HotSearch).count()
            fn_count = session.query(FinanceNews).count()
            lu_count = session.query(LimitUpStock).count()
            us_count = session.query(USMarketDaily).count()
            print(f"  热搜数据: {hs_count} 条")
            print(f"  财经新闻: {fn_count} 条")
            print(f"  涨停股票: {lu_count} 只")
            print(f"  美股指数: {us_count} 条")
    except Exception as e:
        print(f"  统计失败: {e}")

    print("\n" + "=" * 60)
    print("  Web 仪表盘已运行: http://localhost:8000")
    print("  请在浏览器中打开查看效果")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
