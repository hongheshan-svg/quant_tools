"""资讯相关度分级与垃圾页面过滤（纯函数，不联网）。

给每条资讯打 0~100 的相关度分，归为直接相关（公司本身）、行业相关（所属行业/题材背景）、
宏观市场三类，并给出最多 3 条中文依据；同时识别股吧、行情页、荐股广告等低质量页面。
"""
from __future__ import annotations

import re
from typing import Iterable

from src.utils.stock_code import name_variants

CATEGORY_LABELS = {"direct": "直接相关", "sector": "行业相关", "macro": "宏观市场"}
_ORDER = {"direct": 0, "sector": 1, "macro": 2}

CODE_TITLE, CODE_SNIPPET, CODE_URL = 55, 34, 18
NAME_TITLE, NAME_SNIPPET = 45, 28
EVENT_BONUS, OFFICIAL_BONUS, SECTOR_BONUS, MACRO_PENALTY = 12, 8, 6, 12
DIRECT_THRESHOLD = 38
MAX_REASONS = 3

EVENT_WORDS = (
    "公告", "财报", "业绩", "年报", "季报", "中标", "签约", "订单", "回购", "增持", "减持", "分红", "重组",
    "定增", "问询函", "立案", "股东大会", "停牌", "复牌", "解禁", "质押", "投资者关系",
)
MACRO_WORDS = (
    "央行", "降准", "降息", "国务院", "证监会", "美联储", "GDP", "CPI", "PMI", "汇率", "A股", "大盘", "沪指", "指数", "外资",
)
GENERIC_SECTOR_WORDS = ("行业", "板块", "产业链", "概念股")

# 官方/权威渠道：来源名称或链接域名命中任一即可
OFFICIAL_SOURCES = (
    "巨潮", "上交所", "上海证券交易所", "深交所", "深圳证券交易所", "北交所", "北京证券交易所",
    "证券时报", "中国证券报", "上海证券报", "证券日报", "财联社", "东方财富公告",
)
OFFICIAL_DOMAINS = (
    "cninfo.com.cn", "sse.com.cn", "szse.cn", "bse.cn", "stcn.com", "cs.com.cn", "cnstock.com", "zqrb.cn",
    "cls.cn", "np-anotice-stock.eastmoney.com", "data.eastmoney.com/notices",
)

JUNK_URL_PARTS = (
    ("guba.eastmoney.com", "股吧帖子"), ("tieba.baidu.com", "贴吧帖子"), ("zhidao.baidu.com", "问答页面"),
    ("baike.baidu.com", "百科页面"), ("baike.", "百科页面"), ("zhihu.com/question/", "问答页面"),
)
QUOTE_PAGE_WORDS = ("股票价格_行情_走势图", "实时行情", "股吧", "资金流向_", "行情_走势图", "股价_行情")
# 只用组合词，「带你看懂」「股价翻倍」「空降高管」之类的正常标题不能误伤
AD_WORDS = ("荐股", "牛股推荐", "带你赚", "带你翻倍", "加微信", "加群", "内幕消息", "稳赚", "翻倍秘籍", "翻倍牛股", "必涨")
SPAM_WORDS = ("约炮", "上门服务", "博彩", "娱乐城", "六合彩")
_QUOTE_TEMPLATE = re.compile(
    r"\s*[\w一-龥\*]{2,10}\s*[\(（]\s*(?:sh|sz|bj)?\d{6}\s*[\)）]\s*(?:股票|行情|走势|走势图|股价|资料|简介|[_\-\s,，])*",
    re.I,
)


def _code_pattern(code: str) -> re.Pattern | None:
    digits = re.sub(r"\D", "", code or "")[-6:]
    if len(digits) != 6:
        return None
    return re.compile(rf"(?<!\d){digits}(?!\d)")


def is_official(source: str = "", url: str = "") -> bool:
    """来源或链接是否属于官方/权威渠道。"""
    source, url = source or "", (url or "").lower()
    return any(k in source for k in OFFICIAL_SOURCES) or any(d in url for d in OFFICIAL_DOMAINS)


def score_news(title: str, snippet: str = "", url: str = "", source: str = "", code: str = "",
               name: str = "", sector_terms: Iterable[str] = ()) -> dict:
    """给一条资讯打相关度分，返回 {"score", "category", "label", "reasons"}。"""
    title, snippet, url, source = title or "", snippet or "", url or "", source or ""
    reasons: list[str] = []
    signal = 0

    pattern = _code_pattern(code)
    if pattern:
        digits = re.sub(r"\D", "", code)[-6:]
        if pattern.search(title):
            signal += CODE_TITLE
            reasons.append(f"标题命中股票代码 {digits}")
        elif pattern.search(snippet):
            signal += CODE_SNIPPET
            reasons.append(f"摘要命中股票代码 {digits}")
        elif pattern.search(url):
            signal += CODE_URL
            reasons.append(f"链接命中股票代码 {digits}")

    name_score = 0
    for v in name_variants(name):
        if len(v) < 2:
            continue
        if v in title:
            name_score, hit, where = NAME_TITLE, v, "标题"
            break
        if v in snippet and name_score < NAME_SNIPPET:
            name_score, hit, where = NAME_SNIPPET, v, "摘要"
    if name_score:
        signal += name_score
        reasons.append(f"{where}命中公司名 {hit}")

    score = signal
    if signal > 0 and any(w in title or w in snippet for w in EVENT_WORDS):
        score += EVENT_BONUS
        reasons.append("命中公司事件词")
    if is_official(source, url):
        score += OFFICIAL_BONUS
        reasons.append("来源为官方/权威渠道")

    text = title + " " + snippet
    if signal >= DIRECT_THRESHOLD:
        category = "direct"
    elif any(w in text for w in MACRO_WORDS):
        category = "macro"
        score = max(0, score - MACRO_PENALTY)
        reasons.append("未命中公司，归为宏观/市场新闻")
    else:
        category = "sector"
        term = next((t for t in sector_terms if t and len(t) >= 2 and t in text), "")
        if term:
            score += SECTOR_BONUS
            reasons.append(f"命中所属行业/题材 {term}")
        elif any(w in text for w in GENERIC_SECTOR_WORDS):
            score += SECTOR_BONUS
            reasons.append("仅命中行业或板块背景")
    return {"score": max(0, min(100, int(score))), "category": category,
            "label": CATEGORY_LABELS[category], "reasons": reasons[:MAX_REASONS]}


def junk_reason(title: str, snippet: str = "", url: str = "", source: str = "") -> str:
    """低质量页面返回原因，正常资讯返回空字符串；官方/权威渠道永远不是垃圾。"""
    title, snippet, url = title or "", snippet or "", (url or "").lower()
    if is_official(source, url):
        return ""
    for part, reason in JUNK_URL_PARTS:
        if part in url:
            return reason
    if any(w in title for w in QUOTE_PAGE_WORDS):
        return "行情或资料页"
    if title.strip() and _QUOTE_TEMPLATE.fullmatch(title):
        return "行情或资料页"
    text = title + " " + snippet
    if any(w in text for w in SPAM_WORDS):
        return "垃圾内容"
    if any(w in text for w in AD_WORDS):
        return "荐股/引流广告"
    return ""


def rank_news(items: list[dict], code: str, name: str, sector_terms: Iterable[str] = (),
              limit: int | None = None) -> list[dict]:
    """过滤垃圾页面、附上 relevance 并排序（直接 → 行业 → 宏观，同类按分数降序）；返回新列表。"""
    terms = list(sector_terms)
    scored: list[dict] = []
    for item in items:
        title, snippet, url, source = (item.get("title") or "", item.get("snippet") or "",
                                       item.get("url") or "", item.get("source") or "")
        if junk_reason(title, snippet, url, source):
            continue
        scored.append({**item, "relevance": score_news(title, snippet, url, source, code, name, terms)})
    if any(r["relevance"]["category"] == "direct" or r["relevance"]["score"] > 0 for r in scored):
        scored = [r for r in scored if r["relevance"]["category"] == "direct" or r["relevance"]["score"] > 0]
    scored.sort(key=lambda r: (_ORDER[r["relevance"]["category"]], -r["relevance"]["score"]))  # 稳定排序保留原顺序
    return scored[:limit] if limit is not None else scored
