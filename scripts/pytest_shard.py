"""稳定按文件分片；每个测试模块完整分配到一片，避免遗漏或重复。"""

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path


def shard_files(files: list[Path], index: int, count: int) -> list[Path]:
    if not 0 <= index < count:
        raise ValueError("分片索引必须在 0..count-1")
    return [path for path in sorted(files) if int(hashlib.sha256(path.as_posix().encode()).hexdigest(), 16) % count == index]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", type=int, required=True)
    parser.add_argument("--count", type=int, default=3)
    args = parser.parse_args()
    files = shard_files(list(Path("tests").glob("test_*.py")), args.index, args.count)
    if not files:
        raise SystemExit("该分片没有测试文件")
    raise SystemExit(subprocess.call([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *map(str, files)]))


if __name__ == "__main__":
    main()
