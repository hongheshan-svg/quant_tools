"""
A股舆情驱动量化交易系统 - 主入口

    python main.py                                   # 定时任务常驻运行
    python main.py --once                            # 按顺序执行一遍收盘后的全部任务后退出
    python main.py --once --steps collect,analysis   # 只执行部分步骤
    python main.py --stocks 600519,510300,沪深300      # 只诊断这些股票（支持 ETF、指数）并推送决策仪表盘
    python main.py --check-notify                    # 检查推送配置后退出
"""

import argparse
import os
import sys
from pathlib import Path

from loguru import logger

from src.config_loader import load_config
from src.database.db import init_db


def setup_logging(config: dict, debug: bool = False):
    """配置日志；debug 为真时控制台和文件都用 DEBUG 级别"""
    log_cfg = config.get("logging", {})
    level = "DEBUG" if debug else log_cfg.get("level", "INFO")
    log_dir = Path(log_cfg.get("log_dir", "logs"))
    log_dir.mkdir(parents=True, exist_ok=True)

    # 移除默认的 stderr handler
    logger.remove()

    # 控制台输出
    logger.add(
        sys.stderr,
        level=level,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
               "<level>{level: <8}</level> | "
               "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
               "<level>{message}</level>",
    )

    # 文件输出
    logger.add(
        str(log_dir / "quant_{time:YYYY-MM-DD}.log"),
        level=level,
        rotation=log_cfg.get("rotation", "10 MB"),
        retention=log_cfg.get("retention", "30 days"),
        encoding="utf-8",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析命令行参数（argv 为 None 时读 sys.argv）"""
    from src.scheduler import ONCE_STEPS

    parser = argparse.ArgumentParser(description="A股舆情驱动量化交易系统")
    parser.add_argument("--once", action="store_true", help="执行一遍收盘后的任务后退出（GitHub Actions、Docker 定时任务用）")
    parser.add_argument("--scheduled", action="store_true", help="--once 作为定时计划运行，共享数据库认领每日任务；人工补跑不要加此参数")
    parser.add_argument("--steps", help=f"--once 时只执行这些步骤，逗号分隔：{','.join(ONCE_STEPS)}")
    parser.add_argument("--stocks", help="只对这些股票执行 AI 诊断并推送决策仪表盘后退出，逗号分隔，代码、名称、拼音首字母均可，支持 ETF 和指数，如 600519,贵州茅台,510300,沪深300；不受交易日限制")
    parser.add_argument("--no-notify", action="store_true", help="不推送任何消息（对 --once、--stocks 和常驻调度都生效）")
    parser.add_argument("--check-notify", action="store_true", help="检查推送渠道配置，打印结果后退出（全部正常时退出码为 0）")
    parser.add_argument("--check-config", action="store_true", help="校验 settings.yaml（未知键、类型、格式、语义），打印问题列表后退出（没有错误时退出码为 0）")
    parser.add_argument("--debug", action="store_true", help="日志级别改为 DEBUG（控制台和文件）")
    return parser.parse_args(argv)


def format_notify_check(result: dict) -> tuple[str, bool]:
    """把 notifier.diagnose() 的结果整理成可读文字，返回（文本, 是否通过）。

    通过条件：至少一个渠道已启用，所有已启用渠道都没有问题，路由也没有问题。
    """
    lines = ["推送配置检查"]
    channels = result.get("channels") or []
    enabled = [c for c in channels if c.get("enabled")]
    ok = bool(enabled)
    for c in channels:
        label = c.get("label") or c.get("channel", "")
        issues = c.get("issues") or []
        if c.get("enabled"):
            if issues or not c.get("configured", True):
                ok = False
                lines.append(f"  [异常] {label}：已启用，" + ("；".join(issues) if issues else "配置不完整"))
            else:
                lines.append(f"  [正常] {label}：已启用")
        elif not issues and c.get("configured"):
            lines.append(f"  [未启用] {label}：配置完整，但没有启用")
        else:
            lines.append(f"  [未启用] {label}")
    route_issues = result.get("routes") or []
    if route_issues:
        ok = False
        lines.append("路由问题：")
        lines.extend(f"  - {issue}" for issue in route_issues)
    if not enabled:
        lines.append("没有任何已启用的推送渠道，消息不会推送")
    lines.append("结论：" + ("通过" if ok else "未通过"))
    return "\n".join(lines), ok


def _parse_stock_inputs(text: str) -> list[str]:
    """按中英文逗号、顿号、分号和空白拆分股票输入"""
    import re

    return [t for t in re.split(r"[,，、;；\s]+", text or "") if t]


def run_stocks(config: dict, text: str) -> int:
    """--stocks：解析股票、逐只 AI 诊断并推送决策仪表盘；返回退出码"""
    from src.services.watchlist import WatchlistService
    from src.services.watchlist_report import WatchlistReportService

    resolver = WatchlistService(config)
    codes: list[str] = []
    unresolved: list[str] = []
    for token in _parse_stock_inputs(text):
        found = resolver.resolve(token)
        if found:
            codes.append(found[0])
        else:
            unresolved.append(token)
    if unresolved:
        logger.warning(f"无法识别的股票，已跳过：{'、'.join(unresolved)}")
    if not codes:
        logger.error("没有可诊断的股票")
        return 2
    result = WatchlistReportService(config).run(push=True, codes=codes)
    if result.get("error"):
        logger.error(result["error"])
        return 1
    logger.info(f"诊断完成：{result['done']}/{result['total']} 只，推送 {result['pushed']}")
    return 0


def main():
    """主函数"""
    args = parse_args()
    if args.no_notify:
        os.environ["QUANT_NO_NOTIFY"] = "1"

    if args.check_notify:
        from src.notifier import diagnose

        text, ok = format_notify_check(diagnose(load_config()))
        print(text)
        sys.exit(0 if ok else 1)

    if args.check_config:
        from src.services.config_check import check_current, format_check

        result = check_current(load_config())
        print(format_check(result))
        sys.exit(0 if result["ok"] else 1)

    logger.info("=" * 60)
    logger.info("A股舆情驱动量化交易系统 启动中...")
    logger.info("=" * 60)

    # 1. 加载配置
    config = load_config()
    if not config:
        logger.error("配置加载失败，请检查 config/settings.yaml")
        sys.exit(1)

    # 2. 配置日志
    setup_logging(config, args.debug)
    logger.info("日志系统初始化完成")
    from src.services.config_check import log_startup_issues

    log_startup_issues(config)

    # 3. 初始化数据库
    db_cfg = config.get("database", {})
    init_db(
        db_path=db_cfg.get("sqlite_path", "data/quant.db"),
        echo=db_cfg.get("echo", False),
    )
    logger.info("数据库初始化完成")
    from src.collectors.source_chain import source_health
    source_health.configure(db_cfg.get("sqlite_path", "data/quant.db"))

    # 4. 执行一次，或启动调度器
    if args.stocks:
        sys.exit(run_stocks(config, args.stocks))

    if args.once:
        from src.scheduler import run_once

        steps = [s.strip() for s in args.steps.split(",") if s.strip()] if args.steps else None
        try:
            results = run_once(config, steps, scheduled=True) if args.scheduled else run_once(config, steps)
        except (ValueError, RuntimeError) as e:
            logger.error(str(e))
            sys.exit(2)
        for r in results:
            logger.info(f"{r['label']}：{r['seconds']}s")
        return

    from src.bot.manager import start_bots
    from src.scheduler import start_scheduler

    start_bots(config)
    from src.services.fund_registry import refresh_etf_list_background

    refresh_etf_list_background(config.get("database", {}).get("sqlite_path", "data/quant.db"))  # 股票和 ETF 列表（搜索用）
    logger.info("正在启动任务调度器...")
    start_scheduler(config)


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    main()
