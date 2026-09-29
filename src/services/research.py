"""
深度研究：把一个主题拆成子问题，联网搜索 + 本地资讯 + 行情/技术面/主线/大盘取证，再由 LLM 综合成带引用的报告。

流程：规划（chat_json）-> 取证（无 LLM）-> 综合（chat）-> 保存到 research_report。
单个证据来源失败只记日志；规划或综合失败抛 RuntimeError。
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from loguru import logger

from src.collectors import news_search
from src.database.db import get_db_session
from src.database.models import FinanceNews, ResearchReport
from src.services.stock_search import StockSearch

MAX_QUESTIONS = 5
MAX_STOCKS = 5
MAX_KEYWORDS = 6
SEARCH_RESULTS = 5
LOCAL_NEWS_LIMIT = 10
LOCAL_NEWS_DAYS = 7
EVIDENCE_CONTENT_CHARS = 400
EVIDENCE_TOTAL_CHARS = 12000
SUMMARY_CHARS = 80
DISCLAIMER = "仅供学习研究，不构成投资建议"

PLAN_SYSTEM = "你是 A 股行业与主题研究员，负责把研究主题拆解成可检索的子问题。只输出 JSON。"
PLAN_PROMPT = """研究主题：{topic}

请拆解研究计划，输出 JSON：
{{"questions": ["3~5 个需要回答的子问题"], "stocks": ["涉及的股票/ETF/指数名称或代码，最多 5 个"], "keywords": ["用于检索本地资讯的关键词，最多 6 个"]}}"""

REPORT_SYSTEM = "你是严谨的 A 股研究员，只依据给定证据写作，证据不足时明确说明，不编造数据。"
REPORT_PROMPT = """研究主题：{topic}

子问题：
{questions}

证据（用编号引用）：
{evidence}

请写一份 Markdown 研究报告，严格按以下结构，全文 1500 字以内：
## 结论
## 核心逻辑
## 关键证据（引用证据时用 [E1] 这样的编号标注）
## 风险与失效条件
## 相关标的与观察点
## 数据局限

末尾单独一行写：{disclaimer}"""


def _strings(value: Any, limit: int) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    out: list[str] = []
    for item in value:
        text = str(item).strip() if item is not None else ""
        if text and text not in out:
            out.append(text)
    return out[:limit]


class ResearchService:
    def __init__(self, config: dict, llm=None, tools=None) -> None:
        self.config = config
        self.db_path = (config or {}).get("database", {}).get("sqlite_path", "data/quant.db")
        self._llm = llm
        self._tools = tools

    @property
    def llm(self):
        if self._llm is None:
            from src.analyzers.llm_client import LLMClient

            self._llm = LLMClient(self.config)
        return self._llm

    @property
    def tools(self):
        if self._tools is None:
            from src.services.chat_tools import ChatTools

            self._tools = ChatTools(self.config)
        return self._tools

    # ---------- 主流程 ----------

    def run(self, topic: str, progress: Callable[[str], None] | None = None,
            cancel: threading.Event | None = None) -> dict:
        topic = (topic or "").strip()
        if not topic:
            raise RuntimeError("研究主题不能为空")
        notify = progress or (lambda _text: None)

        def check() -> None:
            if cancel is not None and cancel.is_set():
                raise RuntimeError("已取消")

        check()
        notify("拆解问题")
        questions, stocks, keywords = self._plan(topic)

        evidence = _EvidenceBook()
        for i, question in enumerate(questions, 1):
            check()
            notify(f"检索资讯（{i}/{len(questions)}）")
            self._collect_search(evidence, question)
        check()
        self._collect_local_news(evidence, keywords)

        check()
        notify("查询行情")
        resolved = self._collect_quotes(evidence, stocks)
        self._collect_market(evidence)

        check()
        notify("撰写报告")
        markdown = self._write(topic, questions, evidence.items)

        check()
        created = datetime.now()
        report_id = self._save(topic, markdown, questions, evidence.items, resolved, created)
        return {
            "id": report_id, "topic": topic, "markdown": markdown, "questions": questions,
            "evidence": evidence.items, "stocks": resolved, "created_at": created.isoformat(timespec="seconds"),
        }

    # ---------- 规划 ----------

    def _plan(self, topic: str) -> tuple[list[str], list[str], list[str]]:
        try:
            plan = self.llm.chat_json(PLAN_PROMPT.format(topic=topic), PLAN_SYSTEM)
        except Exception as e:
            raise RuntimeError(f"拆解研究问题失败：{e}") from e
        plan = plan if isinstance(plan, dict) else {}
        questions = _strings(plan.get("questions"), MAX_QUESTIONS) or [topic]
        return questions, _strings(plan.get("stocks"), MAX_STOCKS), _strings(plan.get("keywords"), MAX_KEYWORDS)

    # ---------- 取证 ----------

    def _collect_search(self, book: "_EvidenceBook", question: str) -> None:
        try:
            if not news_search.is_enabled(self.config):
                return
            results = news_search.search(question, self.config, max_results=SEARCH_RESULTS)
        except Exception as e:
            logger.warning(f"[深度研究] 联网搜索失败 [{question}]: {e}")
            return
        for r in results or []:
            provider = news_search.PROVIDERS.get(getattr(r, "provider", ""), getattr(r, "provider", "") or "搜索")
            content = " ".join(x for x in (getattr(r, "published", ""), getattr(r, "source", ""), getattr(r, "snippet", "")) if x)
            book.add(f"联网·{provider}", r.title, content, r.url)

    def _collect_local_news(self, book: "_EvidenceBook", keywords: list[str]) -> None:
        if not keywords:
            return
        try:
            from sqlalchemy import or_

            since = datetime.now() - timedelta(days=LOCAL_NEWS_DAYS)
            cond = or_(*[c for k in keywords for c in (FinanceNews.title.contains(k), FinanceNews.content.contains(k))])
            with get_db_session(self.db_path) as session:
                rows = (
                    session.query(FinanceNews).filter(FinanceNews.news_time >= since, cond)
                    .order_by(FinanceNews.news_time.desc()).limit(LOCAL_NEWS_LIMIT).all()
                )
                items = [(r.source, r.title, r.content or "", r.url or "", r.news_time) for r in rows]
        except Exception as e:
            logger.warning(f"[深度研究] 读取本地资讯失败: {e}")
            return
        labels = {"cailianshe": "财联社", "xueqiu": "雪球", "jiuyan": "韭研公社"}
        for source, title, content, url, news_time in items:
            when = news_time.strftime("%Y-%m-%d %H:%M") if news_time else ""
            book.add(labels.get(source, source or "本地资讯"), title, f"{when} {content}".strip(), url)

    def _collect_quotes(self, book: "_EvidenceBook", stocks: list[str]) -> list[dict]:
        resolved: list[dict] = []
        for text in stocks:
            try:
                found = StockSearch(self.db_path).search(text, 1)
            except Exception as e:
                logger.warning(f"[深度研究] 解析标的失败 [{text}]: {e}")
                continue
            if not found or any(x["code"] == found[0]["code"] for x in resolved):
                continue
            code, name = found[0]["code"], found[0].get("name") or text
            resolved.append({"code": code, "name": name})
            for tool, source in (("quote", "行情"), ("technical", "技术面"), ("theme", "主线")):
                try:
                    result = self.tools.call(tool, {"code": code})
                except Exception as e:
                    logger.warning(f"[深度研究] 工具 {tool} 失败 [{code}]: {e}")
                    continue
                if result:
                    book.add(source, f"{name}({code}) {source}", str(result), "")
        return resolved

    def _collect_market(self, book: "_EvidenceBook") -> None:
        try:
            result = self.tools.call("market", {})
        except Exception as e:
            logger.warning(f"[深度研究] 大盘环境获取失败: {e}")
            return
        if result:
            book.add("大盘", "大盘环境", str(result), "")

    # ---------- 综合与保存 ----------

    def _write(self, topic: str, questions: list[str], evidence: list[dict]) -> str:
        ev_text = "\n".join(
            f"[{e['id']}]（{e['source']}）{e['title']}：{e['content']}" for e in evidence
        ) or "（没有取到任何证据，请说明数据局限，不要编造）"
        prompt = REPORT_PROMPT.format(
            topic=topic, questions="\n".join(f"{i}. {q}" for i, q in enumerate(questions, 1)),
            evidence=ev_text, disclaimer=DISCLAIMER,
        )
        try:
            text = self.llm.chat(prompt, REPORT_SYSTEM)
        except Exception as e:
            raise RuntimeError(f"生成研究报告失败：{e}") from e
        text = (text or "").strip()
        if not text:
            raise RuntimeError("生成研究报告失败：模型没有返回内容")
        return text

    def _save(self, topic: str, markdown: str, questions: list[str], evidence: list[dict],
              stocks: list[dict], created: datetime) -> int:
        dump = lambda v: json.dumps(v, ensure_ascii=False)  # noqa: E731
        with get_db_session(self.db_path) as session:
            row = ResearchReport(
                topic=topic, markdown=markdown, questions_json=dump(questions), evidence_json=dump(evidence),
                stocks_json=dump(stocks), created_at=created,
            )
            session.add(row)
            session.flush()
            return row.id

    # ---------- 历史 ----------

    @staticmethod
    def _summary(markdown: str) -> str:
        """结论段前 80 字；没有「## 结论」时取正文开头。"""
        lines = (markdown or "").splitlines()
        body: list[str] = []
        in_conclusion = False
        for line in lines:
            if line.strip().startswith("##"):
                if in_conclusion:
                    break
                in_conclusion = "结论" in line
                continue
            if in_conclusion and line.strip():
                body.append(line.strip())
        text = "".join(body) if body else "".join(l.strip() for l in lines if l.strip() and not l.startswith("#"))
        return text[:SUMMARY_CHARS]

    @staticmethod
    def _load(text: str | None) -> list:
        try:
            return json.loads(text) if text else []
        except ValueError:
            return []

    def list(self, limit: int = 50) -> list[dict]:
        with get_db_session(self.db_path) as session:
            rows = session.query(ResearchReport).order_by(ResearchReport.created_at.desc(), ResearchReport.id.desc()).limit(limit).all()
            return [
                {"id": r.id, "topic": r.topic, "created_at": r.created_at.isoformat(timespec="seconds") if r.created_at else "",
                 "summary": self._summary(r.markdown)}
                for r in rows
            ]

    def get(self, report_id: int) -> dict | None:
        with get_db_session(self.db_path) as session:
            r = session.get(ResearchReport, report_id)
            if r is None:
                return None
            return {
                "id": r.id, "topic": r.topic, "markdown": r.markdown, "questions": self._load(r.questions_json),
                "evidence": self._load(r.evidence_json), "stocks": self._load(r.stocks_json),
                "created_at": r.created_at.isoformat(timespec="seconds") if r.created_at else "",
            }

    def delete(self, report_id: int) -> bool:
        with get_db_session(self.db_path) as session:
            r = session.get(ResearchReport, report_id)
            if r is None:
                return False
            session.delete(r)
            return True


class _EvidenceBook:
    """证据簿：自动编号 E1…，单条截断到 400 字，总字数超 12000 后丢弃后续证据。"""

    def __init__(self) -> None:
        self.items: list[dict] = []
        self.used = 0

    def add(self, source: str, title: str, content: str, url: str) -> None:
        content = (content or "").strip()[:EVIDENCE_CONTENT_CHARS]
        remain = EVIDENCE_TOTAL_CHARS - self.used
        if remain <= 0:
            return
        content = content[:remain]
        self.used += len(content)
        self.items.append({"id": f"E{len(self.items) + 1}", "source": source, "title": (title or "").strip(),
                           "content": content, "url": url or ""})
