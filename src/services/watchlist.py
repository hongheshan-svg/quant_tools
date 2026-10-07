"""
自选股（参考 daily_stock_analysis 的 STOCK_LIST 与智能导入）

- 增删：代码、名称或拼音首字母都可以，先经股票搜索解析成代码；最多 watchlist.max_stocks 只
- ETF 和指数也可以加入（指数的规范代码带交易所前缀，如 sh000300；纯 6 位数字优先当个股）；批量导入和图片导入只识别个股
- 批量导入：粘贴文本或 CSV / Excel 文件，从中识别 6 位代码（可带 sh/sz/bj 前缀）和股票名称
- 配置里的 alerts.watchlist 仍然有效，与这里的自选股合并使用（盘中提醒）
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from loguru import logger

from src.config_loader import load_config
from src.database.db import get_db_session
from src.database.models import FundDaily, StockDaily, Watchlist
from src.utils.stock_code import bare_code, code_candidates, diagnosis_code

DEFAULT_MAX_STOCKS = 50
_CODE = re.compile(r"(?<![0-9A-Za-z])(?:sh|sz|bj|SH|SZ|BJ)?(\d{6})(?:\.(?:SH|SZ|BJ|sh|sz|bj))?(?![0-9])")
_SPLIT = re.compile(r"[\s,，;；、|\t]+")
_NAME = re.compile(r"^[一-龥A-Za-z*]{2,12}$")
CODE_HEADERS = ("证券代码", "股票代码", "代码", "code")
NAME_HEADERS = ("证券名称", "股票名称", "名称", "name")
HEADER_WORDS = {*CODE_HEADERS, *NAME_HEADERS, "备注", "持仓", "数量", "成本"}


def _strip_bom(text: str | None) -> str:
    """去掉 UTF-8 BOM（\ufeff），否则首行表头「代码」识别不出来。"""
    return (text or "").replace("\ufeff", "")


def _table_tokens(text: str) -> tuple[list[str], list[str]] | None:
    """有表头（含「代码」列）的表格只读代码列和名称列，避免把数量、金额里的 6 位数字当成代码。"""
    lines = [line for line in _strip_bom(text).splitlines() if line.strip()]
    for i, line in enumerate(lines):
        delimiter = "\t" if "\t" in line else "," if "," in line else None
        cells = [_strip_bom(c).strip().strip('"').strip().lower() for c in (line.split(delimiter) if delimiter else line.split())]
        code_idx = next((cells.index(h) for h in CODE_HEADERS if h in cells), None)
        if code_idx is None:
            continue
        name_idx = next((cells.index(h) for h in NAME_HEADERS if h in cells), None)
        codes, names = [], []
        for row in lines[i + 1:]:
            values = [c.strip().strip('"') for c in (row.split(delimiter) if delimiter else row.split())]
            code = values[code_idx] if code_idx < len(values) else ""
            match = _CODE.search(code)
            if match:
                codes.append(diagnosis_code(match.group(0)))
            elif name_idx is not None and name_idx < len(values) and values[name_idx]:
                names.append(values[name_idx])
        return list(dict.fromkeys(codes)), list(dict.fromkeys(names))
    return None


def extract_tokens(text: str) -> tuple[list[str], list[str]]:
    """从文本里找出 (代码列表, 可能的名称列表)，都按出现顺序去重。"""
    text = _strip_bom(text)
    table = _table_tokens(text)
    if table:
        return table
    codes = list(dict.fromkeys(diagnosis_code(m.group(0)) for m in _CODE.finditer(text or "")))
    stripped = _CODE.sub(" ", text or "")
    names = []
    for token in _SPLIT.split(stripped):
        token = token.strip().strip('"\'')
        if token and token not in HEADER_WORDS and _NAME.match(token) and not token.isascii():
            names.append(token)
    return codes, list(dict.fromkeys(names))


def read_import_file(path: str) -> str:
    """CSV / TXT 按 UTF-8 或 GBK 读成文本；Excel 用 pandas 读出所有单元格。"""
    p = Path(path)
    if p.suffix.lower() in (".xlsx", ".xls"):
        import pandas as pd

        frames = pd.read_excel(p, sheet_name=None, dtype=str, header=None)
        return "\n".join("\t".join(v if isinstance(v, str) else "" for v in row) for df in frames.values() for row in df.values.tolist())
    raw = p.read_bytes()
    for encoding in ("utf-8-sig", "gbk"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


class WatchlistService:
    def __init__(self, config: dict | None = None):
        self.config = config or load_config()
        self.db_path = self.config.get("database", {}).get("sqlite_path", "data/quant.db")
        self.max_stocks = int((self.config.get("watchlist") or {}).get("max_stocks", DEFAULT_MAX_STOCKS))

    def list(self) -> list[dict[str, Any]]:
        from src.services.fund_registry import fund_kind

        with get_db_session(self.db_path) as session:
            rows = session.query(Watchlist).order_by(Watchlist.added_at, Watchlist.id).all()
            return [{"code": r.code, "name": r.name or "", "note": r.note or "",
                     "kind": fund_kind(r.code, self.db_path) or "stock",
                     "added_at": r.added_at.strftime("%Y-%m-%d %H:%M") if r.added_at else ""} for r in rows]

    def overview(self) -> list[dict[str, Any]]:
        """自选股 + 最新行情 + 最近一次 AI 诊断，供界面和问股使用（ETF/指数的行情取自 fund_daily，诊断取自基金诊断）。"""
        from src.services.fund_diagnosis import FundDiagnosisService
        from src.services.stock_diagnosis import StockDiagnosisService
        from src.services.data_freshness import daily_quality
        from src.services.watchlist_status import row_status
        from src.database.models import TaskRun
        import json

        diagnosis = StockDiagnosisService(self.config)
        fund_diagnosis = FundDiagnosisService(self.config)
        rows = self.list()
        with get_db_session(self.db_path) as session:
            for r in rows:
                if r["kind"] == "stock":
                    bar = (session.query(StockDaily.trade_date, StockDaily.close, StockDaily.change_pct, StockDaily.source, StockDaily.updated_at)
                           .filter(StockDaily.code.in_(code_candidates(r["code"])), StockDaily.close > 0)
                           .order_by(StockDaily.trade_date.desc()).first())
                else:
                    bar = (session.query(FundDaily.trade_date, FundDaily.close, FundDaily.change_pct, FundDaily.source, FundDaily.updated_at)
                           .filter(FundDaily.code == r["code"], FundDaily.close > 0)
                           .order_by(FundDaily.trade_date.desc()).first())
                r.update({"trade_date": bar[0], "close": bar[1], "change_pct": bar[2]} if bar else
                         {"trade_date": "", "close": None, "change_pct": None})
                r["quote_source"] = bar[3] if bar else None
                r['quote_quality'] = daily_quality(bar[0] if bar else None, bar[4] if bar else None)
            tasks = []
            for record in session.query(TaskRun).order_by(TaskRun.created_at.desc()).limit(200).all():
                try:
                    task = json.loads(record.payload_json)
                    if isinstance(task, dict) and task.get('kind') in {'diagnosis', 'watchlist_report'}: tasks.append(task)
                except (TypeError, ValueError):
                    continue
        for r in rows:
            query_error = None
            try:
                latest = (diagnosis if r["kind"] == "stock" else fund_diagnosis).latest(r["code"])
                query_error = latest.get('error') if latest else None
            except Exception:
                latest, query_error = None, '读取报告失败，请重试查询'
            r["diagnosis"] = ({k: latest.get(k) for k in ("diagnosis_id", "action", "action_label", "score", "created_at", "one_sentence")}
                              if latest and not latest.get("error") else None)
            task = next((task for task in tasks if r['code'] in (task.get('subject') or {}).get('codes', []) or task.get('kind') == 'diagnosis' and r['code'] in str(task.get('label') or '')), None)
            r['state'] = row_status(r['diagnosis'], r['quote_quality'], task, query_error)
        return rows

    def codes(self) -> list[str]:
        return [r["code"] for r in self.list()]

    def contains(self, code: str) -> bool:
        return diagnosis_code(code) in self.codes()

    def resolve(self, text: str, include_funds: bool = True) -> tuple[str, str] | None:
        """代码 / 名称 / 拼音 → (代码, 名称)；代码必须在行情库或股票列表里存在。

        include_funds 为真时也识别 ETF 和指数，返回规范代码（指数带前缀）；纯 6 位数字优先当个股，
        要加指数需输入带前缀的代码或名称。批量导入、图片导入、实盘等只要个股的地方传 False。
        """
        query = (text or "").strip()
        if not query:
            return None
        from src.services.fund_registry import resolve_fund

        stock = self._resolve_stock(query)
        fund = resolve_fund(query, self.db_path)
        if fund and not (stock and re.fullmatch(r"\d{6}", query)):
            # 明确是 ETF/指数：include_funds=False 时不能退回去匹配同码个股（如 sh000001 → 平安银行）
            return (fund["code"], fund["name"]) if include_funds else None
        return stock

    def _resolve_stock(self, query: str) -> tuple[str, str] | None:
        from src.services.stock_search import StockSearch

        found = [r for r in StockSearch(self.db_path).search(query, 10) if r.get("kind", "stock") == "stock"][:5]
        exact = next((r for r in found if r["code"] == bare_code(query) or r["name"] == query), None)
        if exact:
            return exact["code"], exact["name"]
        bare = bare_code(query)
        if len(bare) == 6 and bare.isdigit():  # 搜索索引里没有（如新股），但行情库里有
            with get_db_session(self.db_path) as session:
                name = (session.query(StockDaily.name).filter(StockDaily.code.in_(code_candidates(bare)))
                        .order_by(StockDaily.trade_date.desc()).limit(1).scalar())
            return (bare, name or "") if name is not None else None
        return (found[0]["code"], found[0]["name"]) if len(found) == 1 else None

    def add(self, text: str, note: str = "", include_funds: bool = True) -> dict[str, Any]:
        resolved = self.resolve(text, include_funds=include_funds)
        if not resolved:
            return {"ok": False, "error": f"找不到股票「{text}」"}
        code, name = resolved
        with get_db_session(self.db_path) as session:
            if session.query(Watchlist).filter(Watchlist.code == code).first():
                return {"ok": False, "error": f"{name}({code}) 已在自选股中", "code": code, "name": name}
            if session.query(Watchlist).count() >= self.max_stocks:
                return {"ok": False, "error": f"自选股最多 {self.max_stocks} 只（watchlist.max_stocks）"}
            session.add(Watchlist(code=code, name=name, note=note[:200]))
        logger.info(f"加入自选股: {name}({code})")
        return {"ok": True, "code": code, "name": name}

    def remove(self, code: str) -> bool:
        with get_db_session(self.db_path) as session:
            return session.query(Watchlist).filter(Watchlist.code == diagnosis_code(code)).delete() > 0

    def import_text(self, text: str) -> dict[str, list[str]]:
        """批量导入：返回 {"added": [...], "existing": [...], "unknown": [...], "over_limit": [...]}。"""
        codes, names = extract_tokens(text)
        result: dict[str, list[str]] = {"added": [], "existing": [], "unknown": [], "over_limit": []}
        seen: set[str] = set()
        for token in [*codes, *names]:
            resolved = self.resolve(token, include_funds=False)
            if resolved and resolved[0] in seen:  # 同一行里既有代码又有名称
                continue
            if resolved:
                seen.add(resolved[0])
            outcome = self.add(token, include_funds=False)
            label = f"{outcome.get('name', '')}({outcome['code']})" if outcome.get("code") else token
            if outcome["ok"]:
                result["added"].append(label)
            elif "已在自选股中" in outcome["error"]:
                result["existing"].append(label)
            elif "最多" in outcome["error"]:
                result["over_limit"].append(token)
            else:
                result["unknown"].append(token)
        return result

    def import_file(self, path: str) -> dict[str, list[str]]:
        return self.import_text(read_import_file(path))
