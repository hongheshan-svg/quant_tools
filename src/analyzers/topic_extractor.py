"""
热点话题提取器
从多平台热搜中提取股市相关话题并关联到具体个股/板块
"""

from datetime import datetime, timedelta

from loguru import logger

from src.analyzers.llm_client import LLMClient
from src.config_loader import load_config
from src.database.db import get_db_session
from src.database.models import FinanceNews, HotSearch

EXTRACT_PROMPT = """你是一位资深A股题材挖掘专家。

从以下多平台热搜/新闻中，提取出与股市相关的核心题材主线。
注意去重合并：多个平台出现的相似话题应合并为同一题材。

请以 JSON 格式返回：
{
  "themes": [
    {
      "theme_name": "题材名称（如：AI算力、新能源车）",
      "heat_level": "high/medium/low（根据出现频率和热度值判断）",
      "related_sources": ["出现在哪些平台"],
      "related_keywords": ["相关关键词列表"],
      "related_stocks": [
        {"code": "股票代码", "name": "名称", "reason": "关联原因"}
      ],
      "related_sectors": ["关联板块列表"],
      "sentiment": "bullish/bearish/neutral",
      "summary": "题材概述（50字内）"
    }
  ]
}

要求：
1. 只提取与股市、经济、产业链有关的题材
2. 题材要聚焦和具体，不要太宽泛
3. 按热度从高到低排列
4. 至少识别出3个题材，最多10个"""


class TopicExtractor:
    """热点话题提取器"""

    def __init__(self, config: dict = None):
        self.config = config or load_config()
        self.llm = LLMClient(self.config.get("llm", {}))
        self.db_path = self.config.get("database", {}).get("sqlite_path", "data/quant.db")

    def extract_today_themes(self) -> list[dict]:
        """提取今日热点题材"""
        logger.info("开始提取热点题材...")

        # 收集多平台数据
        all_texts = self._gather_all_texts()
        if not all_texts:
            logger.warning("无可分析的热搜数据")
            return []

        # 构建提示词
        user_message = "以下是今日各平台热搜和财经新闻汇总：\n\n"
        for source, texts in all_texts.items():
            user_message += f"【{source}】\n"
            for text in texts[:30]:  # 每平台最多30条
                user_message += f"- {text}\n"
            user_message += "\n"

        # LLM 分析
        try:
            result = self.llm.chat_json(
                user_message=user_message,
                system_message=EXTRACT_PROMPT,
            )
            themes = result.get("themes", [])
            logger.info(f"提取到 {len(themes)} 个热点题材")
            return themes
        except Exception as e:
            logger.error(f"题材提取失败: {e}")
            return []

    def _gather_all_texts(self) -> dict[str, list[str]]:
        """汇总各平台文本"""
        result = {}
        cutoff = datetime.now() - timedelta(hours=24)

        try:
            with get_db_session(self.db_path) as session:
                # 热搜数据按平台分组
                hot_records = (
                    session.query(HotSearch)
                    .filter(HotSearch.collected_at >= cutoff)
                    .order_by(HotSearch.hot_value.desc())
                    .all()
                )
                for r in hot_records:
                    source_name = {"weibo": "微博热搜", "douyin": "抖音热搜",
                                   "toutiao": "头条热搜"}.get(r.source, r.source)
                    result.setdefault(source_name, [])
                    if r.title not in result[source_name]:
                        result[source_name].append(r.title)

                # 财经新闻
                news_records = (
                    session.query(FinanceNews)
                    .filter(FinanceNews.collected_at >= cutoff)
                    .order_by(FinanceNews.id.desc())
                    .limit(50)
                    .all()
                )
                result["财联社快讯"] = []
                for r in news_records:
                    if r.title not in result["财联社快讯"]:
                        result["财联社快讯"].append(r.title)

        except Exception as e:
            logger.error(f"汇总文本数据失败: {e}")

        return result

    def get_theme_stocks(self, themes: list[dict]) -> dict[str, list[str]]:
        """从题材列表中提取股票代码映射

        Returns:
            {"题材名": ["股票代码1", "股票代码2", ...]}
        """
        mapping = {}
        for theme in themes:
            name = theme.get("theme_name", "")
            stocks = theme.get("related_stocks", [])
            codes = [s.get("code", "") for s in stocks if s.get("code")]
            if codes:
                mapping[name] = codes
        return mapping
