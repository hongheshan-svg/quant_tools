"""
财联社重要快讯实时 AI 处理器。
收到红色/重要快讯后，逐条立即送入 LLM 分析并写入 sentiment_analysis。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any

from loguru import logger

from src.analyzers.llm_client import LLMClient
from src.config_loader import load_config
from src.database.db import get_db_session
from src.database.models import FinanceNews, SentimentAnalysis

REALTIME_PROMPT = """你是A股快讯交易分析助手。请精准分析这条快讯，必须落实到具体行业和个股。

返回JSON格式：
{
  "items": [
    {
      "original_text": "原文摘要",
      "sector": "受影响的具体行业板块(如:光伏/锂电池/半导体/白酒/医疗器械/AI算力)",
      "stocks": [
        {"code": "6位股票代码", "name": "股票名称", "logic": "10字以内影响原因"}
      ],
      "sentiment": "bullish/bearish/neutral",
      "impact_score": 1到10整数(7以上=重大影响),
      "action": "具体操作建议(如:关注601012隆基绿能低吸/回避002594比亚迪短期利空)",
      "reason": "核心逻辑(30字以内)"
    }
  ]
}

要求：
1. sector必须填写具体细分行业，不能写"多行业"等模糊词
2. stocks必须列出1-3只最直接相关的A股个股（含6位代码），按影响程度排序
3. action必须包含具体个股名称和操作方向（关注/回避/观望）
4. 如果新闻涉及多个行业，返回多个item
仅返回JSON。"""


class RealtimeNewsAIProcessor:
    """实时快讯 AI 处理。"""

    def __init__(self, config: dict | None = None):
        self.config = config or load_config()
        self.llm = LLMClient(self.config.get("llm", {}))
        self.db_path = self.config.get("database", {}).get("sqlite_path", "data/quant.db")
        self.max_workers = int(self.config.get("desktop", {}).get("realtime_ai_workers", 2))

    def process_cailianshe_stream(self, items: list[dict[str, Any]]) -> int:
        """
        对财联社红色/重要快讯逐条实时分析。
        返回成功入库条数。
        """
        important_items = [x for x in items if x.get("importance") in ("red", "important")]
        if not important_items:
            return 0

        saved_count = 0
        with ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix="realtime-ai") as executor:
            future_map = {
                executor.submit(self.process_cailianshe_item, item): item
                for item in important_items
            }
            for future in as_completed(future_map):
                item = future_map[future]
                try:
                    ok = future.result()
                    if ok:
                        saved_count += 1
                except Exception as e:
                    logger.error(f"实时AI处理失败 [{item.get('title', '')[:50]}]: {e}")
        logger.info(f"财联社实时AI处理完成: {saved_count}/{len(important_items)}")
        return saved_count

    def process_cailianshe_item(self, item: dict[str, Any]) -> bool:
        title = (item.get("title") or "").strip()
        content = (item.get("content") or "").strip()
        if not title:
            return False

        # 去重：同标题只分析一次
        with get_db_session(self.db_path) as session:
            existed = (
                session.query(SentimentAnalysis)
                .filter(
                    SentimentAnalysis.source_type == "cailianshe_realtime",
                    SentimentAnalysis.original_text == title,
                )
                .first()
            )
            if existed:
                return False

        user_message = (
            "请精准分析这条财联社重要快讯，必须落实到具体行业和个股：\n"
            f"标题：{title}\n"
            f"内容：{content[:500]}\n"
            "要求：\n"
            "1. 必须给出受影响的具体行业板块（如光伏、锂电池、半导体，不要写'多行业'）\n"
            "2. 必须给出1-3只最直接受影响的A股个股（含6位代码）\n"
            "3. 给出具体操作建议（关注/回避/观望+个股名称）"
        )
        result = self.llm.chat_json(
            user_message=user_message,
            system_message=REALTIME_PROMPT,
            max_tokens=1500,
        )
        ai_items = result.get("items", [])
        if not ai_items:
            return False

        saved_any = False
        ai_tags: list[str] = []
        with get_db_session(self.db_path) as session:
            for ai in ai_items:
                # 解析新格式：sector + stocks[] + action + reason
                sector = ai.get("sector") or ai.get("related_sector") or ""
                stocks = ai.get("stocks") or []
                sentiment = ai.get("sentiment", "neutral")
                impact = ai.get("impact_score", 5)
                action = ai.get("action") or ""
                reason = ai.get("reason") or ai.get("analysis_reason") or ""

                # 提取首个个股信息（兼容旧格式）
                first_code = ""
                first_name = ""
                if stocks and isinstance(stocks, list):
                    first_code = str(stocks[0].get("code", ""))
                    first_name = str(stocks[0].get("name", ""))
                if not first_code:
                    first_code = ai.get("related_stock_code") or ""
                if not first_name:
                    first_name = ai.get("related_stock_name") or ""

                record = SentimentAnalysis(
                    source_type="cailianshe_realtime",
                    original_text=title,
                    related_stock_code=first_code,
                    related_stock_name=first_name,
                    related_sector=sector,
                    sentiment=sentiment,
                    impact_score=impact,
                    duration_estimate=ai.get("duration_estimate", "短期"),
                    analysis_reason=action or reason,
                    analyzed_at=datetime.now(),
                )
                session.add(record)
                saved_any = True

                # 构建结构化 AI 决策标签：行业→个股→操作建议
                sentiment_cn = {"bullish": "利好", "bearish": "利空", "neutral": "中性"}.get(sentiment, "")
                stock_names = []
                if stocks and isinstance(stocks, list):
                    for s in stocks[:3]:
                        code = str(s.get("code", ""))
                        name = str(s.get("name", ""))
                        if name:
                            stock_names.append(f"{name}({code})" if code else name)
                elif first_name:
                    stock_names.append(f"{first_name}({first_code})" if first_code else first_name)

                parts = []
                if sentiment_cn:
                    parts.append(sentiment_cn)
                if sector:
                    parts.append(f"行业:{sector}")
                if stock_names:
                    parts.append(f"个股:{'、'.join(stock_names)}")
                if action:
                    parts.append(f"→{action}")
                elif reason:
                    parts.append(reason)

                tag = " ".join(parts)
                if tag.strip():
                    ai_tags.append(tag.strip())

        # 把 AI 决策结果追加到该条新闻的 tags 里，供界面展示
        if ai_tags:
            ai_label = "AI: " + " | ".join(ai_tags[:3])
            try:
                with get_db_session(self.db_path) as session:
                    news = (
                        session.query(FinanceNews)
                        .filter(
                            FinanceNews.source == "cailianshe",
                            FinanceNews.title == title,
                        )
                        .order_by(FinanceNews.id.desc())
                        .first()
                    )
                    if news:
                        old_tags = news.tags or ""
                        if "AI:" not in old_tags:
                            news.tags = f"{old_tags} | {ai_label}" if old_tags else ai_label
            except Exception as e:
                logger.debug(f"更新新闻AI标签失败: {e}")

        if saved_any:
            logger.info(f"[实时AI] {title[:40]}... => {'; '.join(ai_tags[:2])}")
        return saved_any

