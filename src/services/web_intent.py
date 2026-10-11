"""Web 问股确定性意图：纯本地实体识别、串行任务、歧义确认、主讨论对象与有界追问上下文。

主讨论对象（state["active"]）由会话保存，规则与 daily_stock_analysis 的问股对象确认一致：
- 「改看/换成/切换到 X」只确认目的地 X，句中的原标的不再分析；目的地有多个备选时先确认。
- 否定（不要/别…）里的操作不执行；条件（如果…就改看）或疑问（是否改看）里的切换不生效，只澄清。
- 比较和一句话多个标的只扩大本轮范围，不改主对象；本轮只涉及一只证券时确认为主对象。
- 没有标的的普通追问（分析风险、看看近期走势、风险呢）沿用主对象；没有主对象时请用户确认，不猜。
"""

import re
import unicodedata
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
STOCK_KINDS = {"stock_analysis", "quote"}
FOLLOWUP = re.compile(r"^(它|他|这只|这个|那只|继续|再|那么|那|还有|为什么|what about|and |why)")
_SEPARATOR = re.compile(r"[，,。！？!?；;\n]|然后|接着|其次|顺便")
# 切换讨论对象的操作词；「换成/换到/切到」在没有证券目的地时（把仓位换成现金）不算切换。
SWITCH = re.compile(r"改看|改为分析|改成分析|改分析|切换到|切换成|转看|换成|换到|切到")
STRICT_SWITCH = {"改看", "改为分析", "改成分析", "改分析", "切换到", "切换成", "转看"}
OPERATION = re.compile(r"改看|改为分析|改成分析|改分析|切换到|切换成|转看|换成|换到|切到|分析|看看|研究|诊断|对比|比较|看")
# 「要不要/想不想/用不用」是疑问，「区别/特别/别的」不是否定。
NEGATION = re.compile(r"(?:(?<!要)不要|(?<![区特分告识类级性派个鉴判辨差])别(?![的人家处样])|(?<!想)不想|不必|(?<!用)不用|无需|(?<!需)不需要|禁止)[^，,。！？!?；;\n]*$")
QUESTION = re.compile(r"是否|要不要|该不该|需不需要|可不可以|能不能")
CONDITION = re.compile(r"如果|假如|假设|要是|万一|倘若|(?:^|\s)若")
CONSEQUENT = re.compile(r"^\s*(?:就|那么|那就|则|那)")
COMPARE = re.compile(r"比较|对比|\bvs\b|相比|区别|差异(?!化)|哪个|哪只|谁更|更值得|更适合|选哪|二选一", re.I)
# 只提到一只证券时与主对象比较；不含副词「比较」（茅台最近比较弱）。
COMPARE_WITH_CURRENT = re.compile(r"对比|\bvs\b|相比|比一比|(?:和|跟|与|同)[^,，。!?！？]{0,12}比|区别|差异(?!化)|哪个|哪只|谁更", re.I)
_TICKER = re.compile(r"(?<![A-Za-z0-9])(?:HK[.:]?\d{1,5}|\d{4,6}\.(?:HK|T|KS|KQ|TW|TWO)|US[:.][A-Za-z]{1,5}(?:[-.][A-Za-z])?|[A-Z]{1,5}(?:[-.][A-Z])?)(?![A-Za-z0-9])")
_BARE_TICKER = re.compile(r"[A-Z]{1,5}(?:[-.][A-Z])?")


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


def _identity(item: dict | None) -> dict | None:
    """主讨论对象只保留身份字段（代码、名称、类型、海外市场），不带确认来源等附加信息。"""
    if not item or not item.get("code"):
        return None
    return {key: item[key] for key in ("code", "name", "kind", "market") if item.get(key)}


def _mentions(clause: str, search: StockSearch, entries) -> list[tuple[int, int, str, list[dict]]]:
    """分句里每个证券提及：(起点, 终点, 原文, 候选)。候选为空表示写了代码但查不到。"""
    mentions = []
    for match in re.finditer(r"(?<![A-Za-z0-9])(?:sh|sz|bj)?\d{6}(?!\d)", clause, re.I):
        raw = match.group()
        found = search.search(raw, 10)
        exact = [item for item in found if item["code"].lower() == raw.lower() or item["code"] == raw[-6:] and item["kind"] != "index"]
        mentions.append((match.start(), match.end(), raw, exact[:1]))
    for match in _TICKER.finditer(clause):
        raw = match.group()
        # 裸大写缩写：单字母（A股、K线）、指标和宏观缩写（MACD、PE、CPI）不是美股代码。
        if _BARE_TICKER.fullmatch(raw) and is_ticker_jargon(raw, clause[match.end():]):
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
    grouped: dict[tuple, list] = {}
    for start, end, alias, found in sorted(mentions):
        group = grouped.setdefault((start, end, alias), [])
        for item in found:
            if item not in group:
                group.append(item)
    return [(start, end, alias, found) for (start, end, alias), found in grouped.items()]


def mentioned_targets(text: str, db_path: str) -> list[dict]:
    """文本里能唯一确定的证券（问股工具用它检查联网搜索词是否超出当前范围）。"""
    text = unicodedata.normalize("NFKC", str(text or ""))
    search = StockSearch(db_path)
    found = []
    for _, _, _, candidates in _mentions(text, search, search._ensure_index()):
        if len(candidates) == 1 and candidates[0] not in found:
            found.append(candidates[0])
    return found


def _qualification(prefix: str, previous: str, separator: str) -> str:
    """操作词前文的限定：denied 否定、conditional 条件后件、question 疑问、affirmative 肯定。"""
    if NEGATION.search(prefix):
        return "denied"
    if CONDITION.search(prefix):
        return "conditional"
    # 逗号连接的「如果…，就改看…」：后件属于前面的条件，不是独立请求。
    if separator in (",", "，") and CONDITION.search(previous) and (not prefix.strip() or CONSEQUENT.match(prefix)):
        return "conditional"
    if QUESTION.search(prefix):
        return "question"
    return "affirmative"


def _split(text: str) -> list[tuple[str, str, str]]:
    """(分句, 上一分句, 两者之间的分隔符)，最多 12 个分句。"""
    parts, previous, separator, start = [], "", "", 0
    for match in [*_SEPARATOR.finditer(text), None]:
        end = match.start() if match else len(text)
        clause = text[start:end].strip()
        if clause:
            parts.append((clause, previous, separator))
            previous = clause
        if match:
            separator, start = match.group(), match.end()
    return parts[:12]


def _clarify(state: dict, message: str) -> dict:
    return {"tasks": [], "requires_confirmation": True, "state": {**state, "pending": None},
            "message": message, "object_decision": "clarify"}


def resolve(question, db_path, state=None, stock_context=None):
    state = dict(state or {})
    active = _identity(state.get("active"))
    pending = state.get("pending")
    text = unicodedata.normalize("NFKC", question).strip()
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
            return _finish(tasks, state, stock_context, switch=answer if pending.get("switch") else None)
        # 新话题使旧确认失效，不把新的行情或大盘请求当作同意。
        state = {**state, "pending": None}
    search = StockSearch(db_path)
    entries = search._ensure_index()
    names = {code: (name, kind) for code, name, _, kind in entries}
    lock = _identity(stock_context)
    tasks, switch = [], None
    current = lock or active   # 本条消息里「它」指向的对象：限定证券 > 主对象 > 本句前面的标的
    for clause, previous, separator in _split(text):
        first_task = len(tasks)
        mentions = _mentions(clause, search, entries)
        operations = list(OPERATION.finditer(clause))
        qualifications = [(op, _qualification(clause[:op.start()], previous, separator)) for op in operations]
        # 否定的操作（不要改看X、别研究A和B的区别）：本分句的标的都不执行，也不改主对象。
        if any(q == "denied" for _, q in qualifications) and not any(q == "affirmative" for _, q in qualifications):
            continue
        switch_op = next((op for op, q in qualifications if SWITCH.fullmatch(op.group())), None)
        if switch_op is not None:
            after = [m for m in mentions if m[0] >= switch_op.end()]
            strict = switch_op.group() in STRICT_SWITCH
            if after or strict:
                quality = dict((op.start(), q) for op, q in qualifications)[switch_op.start()]
                if quality in ("conditional", "question"):
                    return _clarify(state, f"「{clause}」是条件或疑问，没有切换讨论对象；如需改看，请直接说「改看 证券名称或代码」。")
                if not after:
                    return _clarify(state, "请说明要改看哪只证券（名称或代码）。")
                destinations = []
                for _, _, _, found in after:
                    for item in found:
                        if item not in destinations:
                            destinations.append(item)
                alias = "、".join(m[2] for m in after)
                if len(after) != 1 or len(destinations) != 1 or re.search(r"或者|或|还是", clause[switch_op.end():]):
                    task = {"kind": "stock_analysis", "question": clause, "targets": [],
                            "needs_confirmation": {"name": alias, "candidates": destinations, "prompt": "请确认要改看哪一只"}}
                    return _finish([*tasks, task], state, stock_context, switch_pending=True)
                switch = destinations[0]
                tasks.append({"kind": "stock_analysis", "question": clause, "targets": [switch]})
                current = lock or switch
                continue
        targets, slots, ambiguous = [], [], None
        for _, _, alias, found in mentions:
            if len(found) == 1:
                if found[0] not in targets:
                    targets.append(found[0])
                slots.append({'targets': found})
            else:
                unresolved = {"name": alias, "candidates": found}
                slots.append({'targets': [], 'pending': unresolved})
                if ambiguous is None:
                    ambiguous = unresolved
        kinds = [kind for kind, pattern in KINDS.items() if re.search(pattern, clause, re.I)]
        # 比较只提到一只时，与当前主对象比较（「和宁德时代比呢」）。
        if COMPARE_WITH_CURRENT.search(clause) and len(targets) == 1 and not ambiguous and current and _identity(targets[0])["code"] != current["code"]:
            targets.insert(0, current)
            slots.insert(0, {'targets': [current]})
        if targets or ambiguous:
            stock_kind = "quote" if "quote" in kinds and "stock_analysis" not in kinds else "stock_analysis"
            tasks.append({"kind": stock_kind, "question": clause, "targets": targets, **({"needs_confirmation": ambiguous} if ambiguous is not None else {})})
            tasks[-1]['_target_slots'] = slots
            tasks[-1]['_compare'] = bool(COMPARE.search(clause)) and len(targets) > 1
            _refresh_targets(tasks[-1])
            for kind in kinds:
                if kind in {"portfolio_risk", "strategy_screening", "sector_analysis"} or kind == 'market_review' and re.search(r'大盘|market review', clause, re.I):
                    tasks.append({"kind": kind, "question": clause, "targets": targets if kind == "sector_analysis" else []})
        elif kinds:
            for kind in kinds:
                if kind in STOCK_KINDS:
                    if _continue_previous(tasks, first_task, clause):
                        continue
                    if current:
                        tasks.append({"kind": kind, "question": clause, "targets": [current]})
                    else:
                        tasks.append(_unknown_target(kind, clause, search, state, names))
                else:
                    tasks.append({"kind": kind, "question": clause, "targets": []})
        elif _continue_previous(tasks, first_task, clause):
            continue
        elif FOLLOWUP.search(clause.lower()) and not current and state.get("recent_stocks"):
            tasks.append(_unknown_target(state.get("last_kind") or "stock_analysis", clause, search, state, names, followup=True))
        elif FOLLOWUP.search(clause.lower()) and current:
            tasks.append({"kind": state.get("last_kind") or "stock_analysis", "question": clause, "targets": [current]})
        else:
            # 没有标的、没有明确任务的普通追问（风险呢、为什么）：沿用限定证券或主对象。
            tasks.append({"kind": "chat", "question": clause, "targets": [current] if current else []})
        # 同一分句也按动词/主题在原文中的位置排列，不固定把股票任务放在最前。
        def position(task):
            match = re.search(KINDS.get(task['kind'], r'(?!)'), clause, re.I)
            return match.start() if match else min((m[0] for m in mentions), default=len(clause))
        tasks[first_task:] = sorted(tasks[first_task:], key=position)
        if len(targets) == 1 and not lock:
            current = targets[0]
    if not tasks:
        # 整句都是被否定的操作（不要改看300750）：不执行分析，主对象保持不变。
        return _clarify(state, "已按你的要求不切换讨论对象；请说明接下来要分析什么。")
    return _finish(tasks, state, stock_context, switch=switch)


def _continue_previous(tasks: list[dict], first_task: int, clause: str) -> bool:
    """没有标的的后续分句（如果茅台跌破1500，要不要止损）并入同一条消息前一个个股任务，不另起任务。"""
    if first_task == 0 or not tasks or tasks[-1]["kind"] not in STOCK_KINDS or tasks[-1].get("needs_confirmation"):
        return False
    tasks[-1]["question"] = f"{tasks[-1]['question']}，{clause}"
    return True


def _unknown_target(kind: str, clause: str, search: StockSearch, state: dict, names: dict, followup: bool = False) -> dict:
    """需要个股却没有主对象：优先列出最近讨论过的证券请用户确认，不按「风险」「走势」之类的词去搜索。"""
    recent = [{"code": code, "name": names.get(code, ("", ""))[0], "kind": names.get(code, ("", "stock"))[1] or "stock"}
              for code in state.get("recent_stocks") or []]
    stripped = re.sub(r"请|帮我|看看|分析|诊断|走势|研究|行情|最新价|怎么样|一下|的|现在|它|他|这只|那只|这个|呢|吗|[?？]", "", clause).strip()
    if followup or not stripped or re.fullmatch(r"(?:近期|最近|当前)?(?:风险|趋势|估值|基本面|技术面|财务状况|财务|盈利能力|业绩|资金|筹码|止损|止盈|买点|卖点|支撑|压力|前景|消息|新闻|公告|情况|表现|还能买|能买)+", stripped):
        candidates = recent[:6]
        prompt = "还没有确认讨论哪只证券，请选择" if candidates else "还没有确认讨论哪只证券，请提供证券名称或代码。"
        return {"kind": kind, "question": clause, "targets": [],
                "needs_confirmation": {"name": "当前讨论的证券", "candidates": candidates, "prompt": prompt}}
    candidates = [item for item in search.search(stripped, 6)
                  if not (item.get("market") == "US" and is_ticker_jargon(item["code"]))]
    return {"kind": kind, "question": clause, "targets": [], "needs_confirmation": {"name": stripped, "candidates": candidates}}


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


def _finish(tasks, state, stock_context=None, switch=None, switch_pending=False):
    if not within_scope(tasks, stock_context):
        return {'tasks': [], 'requires_confirmation': True, 'state': {**state, 'pending': None}, 'object_decision': 'clarify',
                'message': f"当前限定证券为 {stock_context['code']}；请先修改限定股票代码，再分析其他标的。"}
    tasks = tasks[:12]
    ambiguous = next((task["needs_confirmation"] for task in tasks if task.get("needs_confirmation") is not None), None)
    if ambiguous is not None:
        pending = {**ambiguous, "tasks": tasks, "switch": switch_pending}
        listed = "；".join(f"{i}. {item.get('name', '')}({item['code']})" for i, item in enumerate(ambiguous["candidates"], 1))
        if ambiguous.get("prompt"):
            message = f"{ambiguous['prompt']}：{listed}" if listed else ambiguous["prompt"]
        elif ambiguous["candidates"]:
            message = f"请确认「{ambiguous['name']}」对应的证券：{listed}"
        else:
            message = f"未能明确识别「{ambiguous['name']}」，请提供证券代码或完整名称。"
        return {"tasks": tasks, "requires_confirmation": True, "state": {**state, "pending": pending},
                "message": message, "object_decision": "clarify"}
    previous = _identity(state.get("active"))
    lock = _identity(stock_context)
    distinct = []
    for task in tasks:
        if task["kind"] in STOCK_KINDS or task["kind"] == "chat":
            for item in task["targets"]:
                identity = _identity(item)
                if identity and identity["code"] not in [x["code"] for x in distinct]:
                    distinct.append(identity)
    compared = any(task.get("_compare") for task in tasks)
    if lock:
        active, decision = lock, "confirm"
    elif switch is not None:
        active, decision = _identity(switch), "confirm"
    elif len(distinct) == 1 and not compared:
        active, decision = distinct[0], "confirm"
    else:
        active, decision = previous, ("compare" if len(distinct) > 1 else "maintain")
    if active and previous and active["code"] == previous["code"]:
        active = {**previous, **active}
        if decision == "confirm" and switch is None:
            decision = "maintain"
    recent = list(dict.fromkeys([item["code"] for task in reversed(tasks) for item in task["targets"]] + (state.get("recent_stocks") or [])))[:8]
    for task in tasks:
        task.pop('_target_slots', None)
        task.pop('_compare', None)
    return {"tasks": tasks, "requires_confirmation": False, "object_decision": decision,
            "state": {"pending": None, "recent_stocks": recent, "last_kind": tasks[-1]["kind"], "active": active}}
