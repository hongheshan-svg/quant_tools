"""对话按 token 预算压缩：问题和标的优先，保留消息锚点和最近的证据。"""

import re


def token_count(text: str) -> int:
    # 本地保守估计；不能为了计数在首次对话时联网下载分词表。
    return sum((len(part) + 2) // 3 if part.isascii() else (len(part.encode("utf-8")) + 1) // 2
               for part in re.findall(r"[\x00-\x7f]+|[^\x00-\x7f]+", text))


def fit_tokens(text: str, budget: int) -> str:
    low, high = 0, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if token_count(text[:mid]) <= max(0, budget):
            low = mid
        else:
            high = mid - 1
    return text[:low]


def compress_context(parts: list[str], essential: str, budget: int, max_chars: int | None = None) -> tuple[str, dict]:
    original_essential = essential
    essential = fit_tokens(essential, budget)
    if max_chars is not None:
        essential = essential[:max_chars]
    remaining = max(0, budget - token_count(essential) - 8)
    remaining_chars = max(0, max_chars - len(essential) - 2) if max_chars is not None else None
    selected = []
    for part in reversed(parts):
        fitted = fit_tokens(part, remaining)
        if remaining_chars is not None:
            fitted = fitted[:remaining_chars]
        if fitted:
            selected.insert(0, fitted)
            remaining -= token_count(fitted) + 2
            if remaining_chars is not None:
                remaining_chars = max(0, remaining_chars - len(fitted) - 2)
        if remaining <= 0:
            break
    text = "\n\n".join([*selected, essential])
    return text, {"budget": budget, "estimated_tokens": token_count(text), "compressed": essential != original_essential or len(selected) < len(parts) or any(a != b for a, b in zip(selected, parts))}
