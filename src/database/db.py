"""
数据库引擎与会话管理
"""

import os
from contextlib import contextmanager
from pathlib import Path

from loguru import logger
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from src.database.models import Base

# 全局引擎和会话工厂
_engine = None
_SessionFactory = None


def get_engine(db_path: str = "data/quant.db", echo: bool = False):
    """获取或创建数据库引擎"""
    global _engine
    if _engine is None:
        # 确保目录存在
        db_dir = os.path.dirname(db_path)
        if db_dir:
            Path(db_dir).mkdir(parents=True, exist_ok=True)

        url = f"sqlite:///{db_path}"
        _engine = create_engine(
            url,
            echo=echo,
            # SQLite 优化：连接池 + WAL 模式
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,
            connect_args={"check_same_thread": False},
        )
        # 启用 WAL 模式（写不阻塞读，并发性能提升 5~10 倍）
        with _engine.connect() as conn:
            conn.execute(text("PRAGMA journal_mode=WAL"))
            conn.execute(text("PRAGMA synchronous=NORMAL"))
            conn.execute(text("PRAGMA cache_size=-64000"))  # 64MB 页面缓存
            conn.commit()
        logger.info(f"数据库引擎已创建(WAL模式): {url}")
    return _engine


def get_session_factory(db_path: str = "data/quant.db", echo: bool = False):
    """获取会话工厂"""
    global _SessionFactory
    if _SessionFactory is None:
        engine = get_engine(db_path, echo)
        _SessionFactory = sessionmaker(bind=engine)
    return _SessionFactory


def init_db(db_path: str = "data/quant.db", echo: bool = False):
    """初始化数据库 - 创建所有表，并自动迁移新增列"""
    engine = get_engine(db_path, echo)
    Base.metadata.create_all(engine)
    _auto_migrate(engine)
    _normalize_stock_names(engine)
    logger.info("数据库表已创建/更新")


def _normalize_stock_names(engine):
    """一次性迁移：把 stock_info、watchlist 里带空格/全角字母的股票名称规范化（stock_daily 行太多，读取处兜底）。"""
    try:
        from src.utils.stock_code import normalize_name

        with engine.begin() as conn:
            for table in ("stock_info", "watchlist"):
                rows = conn.execute(text(f"SELECT id, name FROM {table} WHERE name IS NOT NULL")).fetchall()
                for row_id, name in rows:
                    fixed = normalize_name(name)
                    if fixed != name:
                        conn.execute(text(f"UPDATE {table} SET name = :name WHERE id = :id"), {"name": fixed, "id": row_id})
    except Exception as e:
        logger.warning(f"股票名称规范化迁移失败: {e}")


def _auto_migrate(engine):
    """自动检测并添加 models 中定义但数据库中缺失的列（仅 ADD COLUMN）。"""
    try:
        insp = inspect(engine)
        for table_name, table_obj in Base.metadata.tables.items():
            if not insp.has_table(table_name):
                continue
            existing_cols = {c["name"] for c in insp.get_columns(table_name)}
            for col in table_obj.columns:
                if col.name not in existing_cols:
                    col_type = col.type.compile(engine.dialect)
                    sql = f"ALTER TABLE {table_name} ADD COLUMN {col.name} {col_type}"
                    with engine.begin() as conn:
                        conn.execute(text(sql))
                    logger.info(f"自动迁移: {table_name} 新增列 {col.name} ({col_type})")
    except Exception as e:
        logger.warning(f"自动迁移检查异常(可忽略): {e}")


@contextmanager
def get_db_session(db_path: str = "data/quant.db") -> Session:
    """获取数据库会话（上下文管理器）

    使用方式:
        with get_db_session() as session:
            session.add(record)
            session.commit()
    """
    factory = get_session_factory(db_path)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def bulk_insert(records: list, db_path: str = "data/quant.db"):
    """批量插入记录"""
    if not records:
        return
    with get_db_session(db_path) as session:
        session.add_all(records)
    logger.debug(f"批量插入 {len(records)} 条记录")
