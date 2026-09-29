"""
A股舆情驱动量化交易系统 - 主入口

    python main.py                                   # 定时任务常驻运行
    python main.py --once                            # 按顺序执行一遍收盘后的全部任务后退出
    python main.py --once --steps collect,analysis   # 只执行部分步骤
"""

import argparse
import sys
from pathlib import Path

from loguru import logger

from src.config_loader import load_config
from src.database.db import init_db


def setup_logging(config: dict):
    """配置日志"""
    log_cfg = config.get("logging", {})
    log_dir = Path(log_cfg.get("log_dir", "logs"))
    log_dir.mkdir(parents=True, exist_ok=True)

    # 移除默认的 stderr handler
    logger.remove()

    # 控制台输出
    logger.add(
        sys.stderr,
        level=log_cfg.get("level", "INFO"),
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
               "<level>{level: <8}</level> | "
               "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
               "<level>{message}</level>",
    )

    # 文件输出
    logger.add(
        str(log_dir / "quant_{time:YYYY-MM-DD}.log"),
        level=log_cfg.get("level", "INFO"),
        rotation=log_cfg.get("rotation", "10 MB"),
        retention=log_cfg.get("retention", "30 days"),
        encoding="utf-8",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
    )


def main():
    """主函数"""
    from src.scheduler import ONCE_STEPS

    parser = argparse.ArgumentParser(description="A股舆情驱动量化交易系统")
    parser.add_argument("--once", action="store_true", help="执行一遍收盘后的任务后退出（GitHub Actions、Docker 定时任务用）")
    parser.add_argument("--steps", help=f"--once 时只执行这些步骤，逗号分隔：{','.join(ONCE_STEPS)}")
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("A股舆情驱动量化交易系统 启动中...")
    logger.info("=" * 60)

    # 1. 加载配置
    config = load_config()
    if not config:
        logger.error("配置加载失败，请检查 config/settings.yaml")
        sys.exit(1)

    # 2. 配置日志
    setup_logging(config)
    logger.info("日志系统初始化完成")

    # 3. 初始化数据库
    db_cfg = config.get("database", {})
    init_db(
        db_path=db_cfg.get("sqlite_path", "data/quant.db"),
        echo=db_cfg.get("echo", False),
    )
    logger.info("数据库初始化完成")

    # 4. 执行一次，或启动调度器
    if args.once:
        from src.scheduler import run_once

        steps = [s.strip() for s in args.steps.split(",") if s.strip()] if args.steps else None
        try:
            results = run_once(config, steps)
        except ValueError as e:
            logger.error(str(e))
            sys.exit(2)
        for r in results:
            logger.info(f"{r['label']}：{r['seconds']}s")
        return

    from src.bot.manager import start_bots
    from src.scheduler import start_scheduler

    start_bots(config)
    logger.info("正在启动任务调度器...")
    start_scheduler(config)


if __name__ == "__main__":
    main()
