"""修复 stock_daily 里被放大 100 倍的科创板（688/689）成交量。

用法：
    python scripts/repair_star_volume.py [--db data/quant.db] [--apply]

默认只统计不写入；带 --apply 才把成交量除以 100 写回。建议先备份数据库。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="修复科创板成交量被放大 100 倍的日线数据")
    parser.add_argument("--db", default=None, help="SQLite 库路径，默认取配置的 database.sqlite_path")
    parser.add_argument("--apply", action="store_true", help="实际写入（默认只统计）")
    args = parser.parse_args()

    from src.collectors.daily_history import repair_star_volume
    from src.config_loader import load_config

    db_path = args.db or (load_config().get("database") or {}).get("sqlite_path", "data/quant.db")
    if not Path(db_path).exists():
        print(f"数据库不存在：{db_path}")
        return 1
    result = repair_star_volume(db_path, apply=args.apply)
    print(f"数据库：{db_path}")
    print(f"扫描科创板日线 {result['checked']} 行，需要修复 {result['matched']} 行")
    if result["codes"]:
        print("涉及代码（最多 50 个）：" + "、".join(result["codes"]))
    if args.apply:
        print(f"已修复 {result['fixed']} 行")
    elif result["matched"]:
        print("当前为预览模式，未写入；确认后加 --apply 执行修复")
    return 0


if __name__ == "__main__":
    sys.exit(main())
