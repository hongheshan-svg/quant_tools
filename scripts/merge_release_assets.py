"""合并各架构产物；latest-mac.yml 的 files 合并，禁止静默覆盖。"""

import argparse
import shutil
from pathlib import Path

import yaml


def merge_metadata(entries: list[dict]) -> dict:
    versions = {item["version"] for item in entries}
    if len(versions) != 1:
        raise ValueError("各架构版本不一致")
    merged, files = dict(entries[0]), {}
    for item in entries:
        for file in item.get("files", []):
            if file["url"] in files and files[file["url"]] != file:
                raise ValueError("同名更新文件校验信息冲突")
            files[file["url"]] = file
    merged["files"] = list(files.values())
    return merged


def merge_assets(source: Path, target: Path):
    target.mkdir(parents=True, exist_ok=True)
    metadata = {}
    for file in source.glob("*/*"):
        if not file.is_file():
            continue
        if file.name.startswith("latest") and file.suffix == ".yml":
            metadata.setdefault(file.name, []).append(yaml.safe_load(file.read_text()))
        else:
            dest = target / file.name
            if dest.exists() and dest.read_bytes() != file.read_bytes():
                raise ValueError(f"同名产物冲突：{file.name}")
            shutil.copy2(file, dest)
    for name, entries in metadata.items():
        (target / name).write_text(yaml.safe_dump(merge_metadata(entries), allow_unicode=True, sort_keys=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    merge_assets(args.source, args.target)
