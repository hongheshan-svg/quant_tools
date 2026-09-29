"""
问股策略技能：内置策略（src/services/skills/*.yaml）+ 用户自定义策略（config/strategies/*.yaml）

每个策略是一个 YAML 文件，instructions 写进 AI 问股的提示词，决定 AI 按什么标准判断。
自定义策略与内置策略 name 相同时覆盖内置；缺少必填字段或解析失败的文件记 warning 并跳过。
加载结果按目录和文件修改时间缓存，改文件后自动生效（reset_cache() 可强制清空）。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

import yaml
from loguru import logger

BUILTIN_DIR = Path(__file__).parent / "skills"
CUSTOM_DIR = Path("config/strategies")
DEFAULT_SKILL = "综合"
CATEGORIES = ("trend", "pattern", "reversal", "emotion", "framework", "fundamental")
CATEGORY_LABELS = {"trend": "趋势", "pattern": "形态", "reversal": "反转", "emotion": "情绪",
                   "framework": "框架", "fundamental": "基本面"}
REQUIRED_FIELDS = ("name", "display_name", "instructions")


@dataclass(frozen=True)
class Skill:
    name: str
    display_name: str
    description: str
    category: str
    aliases: tuple[str, ...]
    instructions: str
    market_regimes: tuple[str, ...] = ()
    priority: int = 100
    source: str = "builtin"

    def to_dict(self) -> dict:
        return {"name": self.name, "display_name": self.display_name, "description": self.description,
                "category": self.category, "aliases": list(self.aliases), "market_regimes": list(self.market_regimes),
                "source": self.source, "instructions": self.instructions}


_cache: dict[tuple, tuple[tuple, list[Skill]]] = {}
_lock = threading.Lock()


def reset_cache() -> None:
    with _lock:
        _cache.clear()


def _as_tuple(value) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        value = [value]
    return tuple(str(v).strip() for v in value if str(v).strip())


def _parse_file(path: Path, source: str) -> Skill | None:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"策略文件解析失败，已跳过 {path}: {e}")
        return None
    if not isinstance(data, dict):
        logger.warning(f"策略文件格式不对（应为键值对），已跳过 {path}")
        return None
    missing = [k for k in REQUIRED_FIELDS if not str(data.get(k) or "").strip()]
    if missing:
        logger.warning(f"策略文件缺少必填字段 {'、'.join(missing)}，已跳过 {path}")
        return None
    try:
        priority = int(data.get("priority", 100))
    except (TypeError, ValueError):
        priority = 100
    category = str(data.get("category") or "framework").strip()
    return Skill(
        name=str(data["name"]).strip(), display_name=str(data["display_name"]).strip(),
        description=str(data.get("description") or "").strip(),
        category=category if category in CATEGORIES else "framework",
        aliases=_as_tuple(data.get("aliases")), instructions=str(data["instructions"]).strip(),
        market_regimes=_as_tuple(data.get("market_regimes")), priority=priority, source=source,
    )


def _dir_stamp(directory: Path) -> tuple:
    try:
        files = sorted([*directory.glob("*.yaml"), *directory.glob("*.yml")])
        return tuple((f.name, f.stat().st_mtime_ns) for f in files)
    except OSError:
        return ()


def _load_dir(directory: Path, source: str) -> list[Skill]:
    if not directory.is_dir():
        return []
    files = sorted([*directory.glob("*.yaml"), *directory.glob("*.yml")])
    return [s for s in (_parse_file(f, source) for f in files) if s]


def load_skills(custom_dir: Path | None = None) -> list[Skill]:
    """内置 + 自定义策略，按 (priority, display_name) 排序。"""
    custom = Path(custom_dir) if custom_dir is not None else CUSTOM_DIR
    key = (str(BUILTIN_DIR.resolve()), str(custom.resolve()))
    stamp = (_dir_stamp(BUILTIN_DIR), _dir_stamp(custom))
    with _lock:
        cached = _cache.get(key)
        if cached and cached[0] == stamp:
            return list(cached[1])
    merged: dict[str, Skill] = {s.name: s for s in _load_dir(BUILTIN_DIR, "builtin")}
    for skill in _load_dir(custom, "custom"):
        merged[skill.name] = skill  # 同名覆盖内置
    skills = sorted(merged.values(), key=lambda s: (s.priority, s.display_name))
    with _lock:
        _cache[key] = (stamp, skills)
    return list(skills)


def _norm(text: str) -> str:
    return text.strip().lower()


def match_keys(skill: Skill) -> tuple[str, ...]:
    """可用于匹配该策略的全部写法（英文标识、中文名、别名）。"""
    return (skill.name, skill.display_name, *skill.aliases)


def get_skill(key: str | None, custom_dir: Path | None = None) -> Skill | None:
    """按 name、display_name、alias 匹配（忽略大小写和首尾空白）。"""
    target = _norm(key or "")
    if not target:
        return None
    skills = load_skills(custom_dir)
    for pick in (lambda s: (s.name,), lambda s: (s.display_name,), lambda s: s.aliases):
        for skill in skills:
            if target in {_norm(k) for k in pick(skill)}:
                return skill
    return None


def match_prefix(text: str, custom_dir: Path | None = None) -> tuple[Skill, str] | None:
    """文本开头是策略名/别名时返回 (策略, 剩余文字)，取最长匹配；英文写法后面必须是空格或结尾。"""
    body = text.strip()
    lowered = body.lower()
    best: tuple[int, Skill] | None = None
    for skill in load_skills(custom_dir):
        for key in match_keys(skill):
            k = _norm(key)
            if not k or not lowered.startswith(k):
                continue
            rest = body[len(k):]
            if k.isascii() and rest[:1].isascii() and rest[:1] not in ("", " ", "\t", "　"):
                continue
            if best is None or len(k) > best[0] or (len(k) == best[0] and skill.source == "custom" and best[1].source != "custom"):  # 同长度自定义优先
                best = (len(k), skill)
    if best is None:
        return None
    return best[1], body[best[0]:].strip()
