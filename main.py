"""
A股舆情驱动量化交易系统 - 主入口
"""

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

    # 4. 启动调度器
    from src.scheduler import start_scheduler
    logger.info("正在启动任务调度器...")
    start_scheduler(config)


if __name__ == "__main__":
    main()
