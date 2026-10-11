"""Web 问股确定性意图：纯本地实体识别、串行任务、歧义确认与有界追问上下文。"""

import re
from copy import deepcopy
from src.services.stock_search import StockSearch, ALIASES
from src.utils.stock_code import is_ticker_jargon

KINDS = {
    "portfolio_risk": r"持仓|组合|账户|portfolio",
    "market_review": r"大盘|复盘|market review",
    "sector_analysis": r"板块|主线|题材|行业|sector",
    "strategy_screening": r"选股|筛选|screen",
    "quote": r"行情|最新价|报价|quote|price",
    "stock_analysis": r"分析|诊断|走势|能买吗|止损|研究|analy[sz]|research",
}
FOLLOWUP = re.compile(r"^(它|他|这只|这个|那只|继续|再|那么|那|还有|为什么|what about|and |why)")


def _refresh_targets(task):
    """每个实体占据原位置；确认只替换对应槽位，不覆盖已解析的证券。"""
    task['targets'] = []
    task.pop('needs_confirmation', None)
    for index, slot in enumerate(task.get('_target_slots', [])):
        if slot.get('pending') is not None:
            task.setdefault('needs_confirmation', {**slot['pending'], 'slot': index})
        for item in slot.get('targets', []):
            if item not in task['targets']:
                task['targets'].append(item)


def resolve(question, db_path, state=None, stock_context=None):
    state = state or {}
    pending = state.get("pending")
    text = question.strip()
    if pending:
        candidates = pending.get("candidates") or []
        answer = next((candidate for i, candidate in enumerate(candidates, 1) if text in {candidate["code"], candidate["name"], str(i), f"第{i}个"}), None)
        if answer:
            tasks = deepcopy(pending["tasks"])
            for task in tasks:
                if task.get("needs_confirmation"):
                    if '_target_slots' in task:
                        slot = task['needs_confirmation']['slot']
                        task['_target_slots'][slot] = {'targets': [answer]}
                        _refresh_targets(task)
                    else:
                        task["targets"] = list(task.get('targets', [])) + [answer]
                        task.pop("needs_confirmation", None)
                    break
            return _finish(tasks, state, stock_context)
        # 新话题使旧确认失效，不把新的行情或大盘请求当作同意。
        state = {**state, "pending": None}
    search = StockSearch(db_path)
    entries = search._ensure_index()
    tasks = []
    clauses = [part.strip() for part in re.split(r"[，,。！？!?；;]|然后|接着|其次|顺便", text) if part.strip()][:12]
    inherited = (stock_context or {}).get("code") or next(iter(state.get("recent_stocks") or []), None)
    for clause in clauses:
        first_task = len(tasks)
        targets, mentions = [], []
        for match in re.finditer(r"(?<![A-Za-z0-9])(?:sh|sz|bj)?\d{6}(?!\d)", clause, re.I):
            raw = match.group()
            found = search.search(raw, 10)
            exact = [item for item in found if item["code"].lower() == raw.lower() or item["code"] == raw[-6:] and item["kind"] != "index"]
            if exact:
                mentions.append((match.start(), match.end(), raw, exact[:1]))
            else:
                mentions.append((match.start(), match.end(), raw, []))
        for match in re.finditer(r"(?<![A-Za-z0-9])(?:HK[.:]?\d{1,5}|\d{4,6}\.(?:HK|T|KS|KQ|TW|TWO)|US[:.][A-Za-z]{1,5}(?:[-.][A-Za-z])?|[A-Z]{1,5}(?:[-.][A-Z])?)(?![A-Za-z0-9])", clause):
            raw = match.group()
            # 裸大写缩写：单字母（A股、K线）、指标和宏观缩写（MACD、PE、CPI）不是美股代码。
            if re.fullmatch(r"[A-Z]{1,5}(?:[-.][A-Z])?", raw) and is_ticker_jargon(raw, clause[match.end():]):
                continue
            found = search.search(raw, 10)
            from src.utils.stock_code import overseas_identity
            identity = overseas_identity(raw)
            exact = [item for item in found if identity and item["code"] == identity.code]
            if exact:
                mentions.append((match.start(), match.end(), raw, exact[:1]))
        for code, name, _, kind in entries:
            for alias in (name, *ALIASES.get(code, ())):
                pattern = (r'(?<![A-Za-z0-9])' + re.escape(alias) + r'(?![A-Za-z0-9])') if alias.isascii() else re.escape(alias)
                match = re.search(pattern, clause, re.I) if alias and len(alias) >= 2 else None
                if match:
                    start = match.start()
                    candidates = [{"code": code, "name": name, "kind": kind}]
                    if alias != name:
                        candidates = [item for item in search.search(alias, 10) if item["kind"] == "stock" and (item["name"].startswith(alias) or alias in ALIASES.get(item["code"], ()))] or candidates
                    mentions.append((start, start + len(alias), alias, candidates))
        # 最长名称消费对应短别名；同名标的仍保留全部候选。
        mentions = [mention for mention in mentions if not any(other[0] <= mention[0] and other[1] >= mention[1] and other[1] - other[0] > mention[1] - mention[0] for other in mentions)]
        grouped = {}
        for start, end, alias, found in sorted(mentions):
            group = grouped.setdefault((start, end, alias), [])
            for item in found:
                if item not in group: group.append(item)
        ambiguous = None
        slots = []
        for (_, _, alias), found in grouped.items():
            if len(found) == 1:
                if found[0] not in targets: targets.append(found[0])
                slots.append({'targets': found})
            else:
                unresolved = {"name": alias, "candidates": found}
                slots.append({'targets': [], 'pending': unresolved})
                if ambiguous is None:
                    ambiguous = unresolved
        kinds = [kind for kind, pattern in KINDS.items() if re.search(pattern, clause, re.I)]
        if targets or ambiguous:
            stock_kind = "quote" if "quote" in kinds and "stock_analysis" not in kinds else "stock_analysis"
            tasks.append({"kind": stock_kind, "question": clause, "targets": targets, **({"needs_confirmation": ambiguous} if ambiguous is not None else {})})
            tasks[-1]['_target_slots'] = slots
            _refresh_targets(tasks[-1])
            for kind in kinds:
                if kind in {"portfolio_risk", "strategy_screening", "sector_analysis"} or kind == 'market_review' and re.search(r'大盘|market review', clause, re.I):
                    tasks.append({"kind": kind, "question": clause, "targets": targets if kind == "sector_analysis" else []})
        elif kinds:
            for kind in kinds:
                if kind in {"stock_analysis", "quote"}:
                    if FOLLOWUP.search(clause.lower()) and inherited:
                        tasks.append({"kind": kind, "question": clause, "targets": [{"code": inherited}]})
                    elif stock_context:
                        tasks.append({"kind": kind, "question": clause, "targets": [stock_context]})
                    else:
                        stripped = re.sub(r"请|帮我|看看|分析|诊断|走势|研究|行情|最新价|怎么样|一下|的|现在", "", clause).strip()
                        candidates = [item for item in (search.search(stripped, 6) if stripped else [])
                                      if not (item.get("market") == "US" and is_ticker_jargon(item["code"]))]
                        tasks.append({"kind": kind, "question": clause, "targets": [], "needs_confirmation": {"name": stripped or clause, "candidates": candidates}})
                else:
                    tasks.append({"kind": kind, "question": clause, "targets": []})
        elif FOLLOWUP.search(clause.lower()) and inherited:
            tasks.append({"kind": state.get("last_kind") or "stock_analysis", "question": clause, "targets": [{"code": inherited}]})
        else:
            tasks.append({"kind": "chat", "question": clause, "targets": [stock_context] if stock_context else []})
        # 同一分句也按动词/主题在原文中的位置排列，不固定把股票任务放在最前。
        def position(task):
            match = re.search(KINDS.get(task['kind'], r'(?!)'), clause, re.I)
            return match.start() if match else min((m[0] for m in mentions), default=len(clause))
        tasks[first_task:] = sorted(tasks[first_task:], key=position)
        if targets: inherited = targets[-1]["code"]
    return _finish(tasks or [{"kind": "chat", "question": text, "targets": []}], state, stock_context)


def within_scope(tasks: list[dict], stock_context: dict | None) -> bool:
    """确认、追问及执行前使用同一个规范证券边界。"""
    if not stock_context:
        return True
    from src.utils.stock_code import diagnosis_code, StockCodeError
    try:
        scope = diagnosis_code(stock_context['code'])
        return all(diagnosis_code(target['code']) == scope for task in tasks for target in task['targets'])
    except (KeyError, StockCodeError):
        return False


def _finish(tasks, state, stock_context=None):
    if not within_scope(tasks, stock_context):
        return {'tasks': [], 'requires_confirmation': True, 'state': {**state, 'pending': None},
                'message': f"当前限定证券为 {stock_context['code']}；请先修改限定股票代码，再分析其他标的。"}
    tasks = tasks[:12]
    ambiguous = next((task["needs_confirmation"] for task in tasks if task.get("needs_confirmation") is not None), None)
    if ambiguous is not None:
        pending = {**ambiguous, "tasks": tasks}
        return {"tasks": tasks, "requires_confirmation": True, "state": {**state, "pending": pending},
                "message": f"请确认「{ambiguous['name']}」对应的证券：" + "；".join(f"{i}. {item.get('name', '')}({item['code']})" for i, item in enumerate(ambiguous["candidates"], 1)) if ambiguous["candidates"] else f"未能明确识别「{ambiguous['name']}」，请提供证券代码或完整名称。"}
    recent = list(dict.fromkeys([item["code"] for task in reversed(tasks) for item in task["targets"]] + (state.get("recent_stocks") or [])))[:8]
    for task in tasks:
        task.pop('_target_slots', None)
    return {"tasks": tasks, "requires_confirmation": False, "state": {"pending": None, "recent_stocks": recent, "last_kind": tasks[-1]["kind"]}}
