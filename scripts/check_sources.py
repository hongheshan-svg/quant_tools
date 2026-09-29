"""
数据源连通性检查（联网）：逐个请求各数据源，输出是否可用、条数和用时。不写数据库。

    python scripts/check_sources.py              # 检查全部
    python scripts/check_sources.py --only 腾讯,同花顺
    python scripts/check_sources.py --no-browser # 跳过需要 Playwright 的源

关键数据源（行情、交易日历、涨停池）不可用时退出码为 1，GitHub Actions 的 network-smoke 用它发现接口变化。
在 GitHub Actions 里运行时把结果表格写进任务摘要（GITHUB_STEP_SUMMARY）。
"""

import argparse
import io
import os
import sys
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

if sys.platform == "win32" and hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger  # noqa: E402

PROBE_TIMEOUT = 90
SAMPLE_CODE = "600519"


@dataclass
class Probe:
    name: str
    fn: Callable[[], int]  # 返回拿到的条数；抛异常或返回 0 视为失败
    critical: bool = False
    browser: bool = False


@dataclass
class Result:
    probe: Probe
    ok: bool
    count: int = 0
    seconds: float = 0.0
    error: str = ""


def _count(data) -> int:
    if data is None:
        return 0
    if hasattr(data, "__len__"):
        return len(data)
    return 1


def last_trade_day(days: set[str], now: datetime | None = None) -> str:
    """最近一个已经开盘（9:25 后）的交易日，用来请求涨停池等按日期的接口"""
    now = now or datetime.now()
    today = now.strftime("%Y-%m-%d")
    ready = now.strftime("%H:%M") >= "09:25"
    past = sorted(d for d in days if d < today or (d == today and ready))
    if past:
        return past[-1]
    d = now.date() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime("%Y-%m-%d")


def build_probes(trade_day: Callable[[], str]) -> list[Probe]:
    import httpx

    def tencent() -> int:
        resp = httpx.get("https://qt.gtimg.cn/q=sh600519,sz000001,sz300750", timeout=10)
        resp.raise_for_status()
        return sum(1 for line in resp.text.split(";") if "~" in line)

    def calendar() -> int:
        from src import trading_calendar

        return len(trading_calendar._fetch_remote())

    def em_limit_up() -> int:
        from src.collectors.em_client import get_em_client

        return _count(get_em_client().stock_zt_pool_em(date=trade_day().replace("-", "")))

    def em_index() -> int:
        from src.collectors.em_client import get_em_client

        return _count(get_em_client().stock_zh_index_spot_em())

    def ths_reasons() -> int:
        from src.collectors.limit_up_reasons import fetch_ths_limit_up_reasons

        return len(fetch_ths_limit_up_reasons(trade_day()))

    def ths_fund_flow() -> int:
        from src.collectors.fund_flow import fetch_ths_fund_flow

        return len(fetch_ths_fund_flow())

    def em_fund_flow() -> int:
        from src.collectors.fund_flow import fetch_em_fund_flow

        return len(fetch_em_fund_flow())

    def daily_history() -> int:
        from src.collectors.daily_history import fetch_daily_df_with_fallback

        end = date.today()
        _, df = fetch_daily_df_with_fallback(SAMPLE_CODE, (end - timedelta(days=30)).isoformat(), end.isoformat())
        return _count(df)

    def stock_news() -> int:
        from src.collectors.stock_news import fetch_stock_news

        return len(fetch_stock_news(SAMPLE_CODE))

    def stock_notices() -> int:
        from src.collectors.stock_news import fetch_stock_notices

        return len(fetch_stock_notices(SAMPLE_CODE))

    def stock_list() -> int:
        from src.collectors.stock_info import StockInfoCollector

        return len(StockInfoCollector({}).collect())

    def collector(module: str, cls: str) -> Callable[[], int]:
        def run() -> int:
            import importlib

            return len(getattr(importlib.import_module(f"src.collectors.{module}"), cls)({}).collect() or [])
        return run

    return [
        Probe("腾讯行情", tencent, critical=True),
        Probe("新浪交易日历", calendar, critical=True),
        Probe("东方财富涨停池", em_limit_up, critical=True, browser=True),
        Probe("东方财富指数", em_index, browser=True),
        Probe("同花顺涨停原因", ths_reasons),
        Probe("同花顺资金流", ths_fund_flow, browser=True),
        Probe("东方财富资金流", em_fund_flow, browser=True),
        Probe("历史日线", daily_history),
        Probe("东方财富个股新闻", stock_news),
        Probe("东方财富公告", stock_notices),
        Probe("交易所股票列表", stock_list),
        Probe("财联社", collector("cailianshe", "CailiansheCollector")),
        Probe("雪球", collector("xueqiu", "XueqiuCollector"), browser=True),
        Probe("韭研公社", collector("jiuyan", "JiuyanCollector"), browser=True),
        Probe("微博热搜", collector("weibo", "WeiboCollector"), browser=True),
        Probe("抖音热榜", collector("douyin", "DouyinCollector"), browser=True),
        Probe("今日头条", collector("toutiao", "ToutiaoCollector"), browser=True),
    ]


def make_trade_day() -> Callable[[], str]:
    """按需取一次交易日历（失败时按周一至周五推算），结果缓存"""
    cached: list[str] = []

    def get() -> str:
        if not cached:
            try:
                from src import trading_calendar

                days = trading_calendar._fetch_remote()
            except Exception:
                days = set()
            cached.append(last_trade_day(days))
        return cached[0]
    return get


def run_probe(probe: Probe, timeout: float = PROBE_TIMEOUT) -> Result:
    started = time.monotonic()
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        count = pool.submit(probe.fn).result(timeout=timeout)
        ok = count > 0
        return Result(probe, ok, count, time.monotonic() - started, "" if ok else "没有返回数据")
    except FutureTimeout:
        return Result(probe, False, 0, time.monotonic() - started, f"超过 {timeout:.0f} 秒没有响应")
    except Exception as e:
        return Result(probe, False, 0, time.monotonic() - started, f"{type(e).__name__}: {e}"[:200])
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def render_markdown(results: list[Result], trade_day: str) -> str:
    lines = [f"### 数据源检查（{datetime.now():%Y-%m-%d %H:%M}，交易日 {trade_day}）", "",
             "| 数据源 | 状态 | 条数 | 用时 | 说明 |", "| --- | --- | ---: | ---: | --- |"]
    for r in results:
        status = "✅" if r.ok else ("❌ 关键" if r.probe.critical else "⚠️")
        error = r.error.replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {r.probe.name} | {status} | {r.count} | {r.seconds:.1f}s | {error} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="检查各数据源是否可用（联网，不写数据库）")
    parser.add_argument("--only", help="只检查名称包含这些关键词的数据源，逗号分隔")
    parser.add_argument("--no-browser", action="store_true", help="跳过需要 Playwright 浏览器的数据源")
    parser.add_argument("--timeout", type=float, default=PROBE_TIMEOUT, help="单个数据源的超时（秒）")
    args = parser.parse_args()

    logger.remove()
    logger.add(sys.stderr, level="WARNING")
    trade_day = make_trade_day()
    probes = build_probes(trade_day)
    if args.only:
        keywords = [k.strip() for k in args.only.split(",") if k.strip()]
        probes = [p for p in probes if any(k in p.name for k in keywords)]
    if args.no_browser:
        probes = [p for p in probes if not p.browser]

    results = []
    for probe in probes:
        result = run_probe(probe, args.timeout)
        results.append(result)
        mark = "OK  " if result.ok else "FAIL"
        print(f"[{mark}] {probe.name:<10} {result.count:>6} 条 {result.seconds:6.1f}s  {result.error}", flush=True)

    summary = render_markdown(results, trade_day())
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(summary)
    failed = [r for r in results if not r.ok]
    critical = [r.probe.name for r in failed if r.probe.critical]
    print(f"\n可用 {len(results) - len(failed)}/{len(results)}" + (f"，关键数据源不可用：{'、'.join(critical)}" if critical else ""))
    try:
        from src.collectors.em_client import get_em_client

        get_em_client().close()
    except Exception:
        pass
    return 1 if critical else 0


if __name__ == "__main__":
    exit_code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    # 超时的数据源可能还卡在工作线程或浏览器里，直接退出，不等它们
    os._exit(exit_code)
