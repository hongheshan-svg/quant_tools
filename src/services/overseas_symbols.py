"""海外常用证券种子；日韩条目参考 DSA ce364e4，许可见 docs/licenses。"""
from functools import lru_cache
from pathlib import Path
import csv


@lru_cache(maxsize=1)
def symbols() -> tuple[dict, ...]:
    with (Path(__file__).parent / 'resources' / 'overseas_symbols.csv').open(encoding='utf-8') as file:
        return tuple({'code': row['code'], 'name': row['name'],
                      'aliases': tuple(a for a in row['aliases'].split('|') if a)} for row in csv.DictReader(file))


def aliases() -> dict[str, tuple[str, ...]]:
    return {row['code']: row['aliases'] for row in symbols()}


def name_for(code: str) -> str:
    return next((row['name'] for row in symbols() if row['code'] == code), code)
