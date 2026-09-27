"""
国际重大新闻/事件采集器（增强版）
多源采集: 财联社国际 → 华尔街见闻 → 金十数据 → 东方财富全球
附加: 大宗商品异动、VIX恐慌指数、美元人民币汇率、美联储日历
"""

from contextlib import suppress
from datetime import datetime
from typing import Any

from loguru import logger

from src.collectors.base import BaseCollector
from src.collectors.em_client import get_em_client
from src.database.db import get_db_session
from src.database.models import GlobalNews

HTTP_OK_STATUS = 200

IMPORTANCE_MAX = 10
MIN_NEWS_TITLE_LEN = 10
EARLY_CATEGORY_ROW_LIMIT = 20

HIGH_VOLATILITY_PCT = 5
DEFAULT_IMPORTANCE = 5
HIGH_IMPORTANCE = 7

VIX_IMPORTANT_LEVEL = 20
VIX_PANIC_LEVEL = 30
VIX_WARNING_LEVEL = 25
VIX_IMPORTANCE_HIGH = 8
VIX_IMPORTANCE_DEFAULT = 6

USDCNH_ALERT_PCT = 0.3
FX_IMPORTANCE_ALERT_PCT = 0.5
UDI_ALERT_PCT = 0.5


class GlobalNewsCollector(BaseCollector):
    """国际重大新闻/事件采集器（多源增强版）"""

    SOURCE_NAME = "global_news"

    # 关键词分类映射（扩展）
    CATEGORY_KEYWORDS = {
        "美联储": [
            "美联储", "联储", "加息", "降息", "FOMC", "鲍威尔", "利率决议",
            "缩表", "QE", "点阵图", "联邦基金利率", "鸽派", "鹰派",
        ],
        "经济数据": [
            "CPI", "PPI", "非农", "失业率", "GDP", "PMI", "初请",
            "零售销售", "PCE", "ISM", "ADP", "就业", "通胀",
            "消费者信心", "密歇根", "耐用品",
        ],
        "地缘政治": [
            "中美", "贸易战", "制裁", "关税", "俄乌", "中东", "台海",
            "拜登", "特朗普", "出口管制", "芯片禁令", "实体清单",
        ],
        "大宗商品": [
            "原油", "黄金", "白银", "铜价", "铁矿", "油价", "金价",
            "OPEC", "天然气", "锂价", "镍", "铝",
        ],
        "美股": [
            "纳斯达克", "道琼斯", "标普", "美股", "华尔街",
            "英伟达", "苹果", "特斯拉", "微软", "谷歌", "亚马逊", "Meta",
            "AMD", "台积电", "博通", "高通",
        ],
        "中概股": ["中概股", "中概", "ADR", "阿里", "拼多多", "京东", "百度"],
        "央行政策": ["欧央行", "日央行", "BOJ", "ECB", "央行", "货币政策"],
        "科技产业": [
            "AI", "人工智能", "ChatGPT", "DeepSeek", "大模型", "算力",
            "芯片", "半导体", "光刻机", "ASML",
        ],
    }

    def collect(self) -> list[dict[str, Any]]:
        """采集国际重大新闻（多源并行）"""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        db_path = self.config.get("database", {}).get("sqlite_path", "data/quant.db")
        results = []

        # 并行采集所有源（比串行快 3~5 倍）
        sources = [
            self._collect_from_cailianshe,
            self._collect_from_wallstreetcn,
            self._collect_from_jin10,
            self._collect_from_eastmoney_global,
            self._collect_commodity_prices,
            self._collect_market_indicators,
        ]
        with ThreadPoolExecutor(max_workers=4, thread_name_prefix="gnews") as pool:
            futures = {pool.submit(fn): fn.__name__ for fn in sources}
            for future in as_completed(futures):
                try:
                    data = future.result()
                    if data:
                        results.extend(data)
                except Exception as e:
                    logger.debug(f"国际新闻 [{futures[future]}] 异常: {e}")

        # 去重（按标题前60字符）
        seen = set()
        unique = []
        for item in results:
            key = item.get("title", "")[:60]
            if key and key not in seen:
                seen.add(key)
                unique.append(item)

        # 写入数据库
        if unique:
            self._save_to_db(unique, db_path)

        return unique

    # ============================================================
    # 新闻源采集
    # ============================================================

    def _collect_from_cailianshe(self) -> list[dict[str, Any]]:
        """财联社国际快讯"""
        results = []
        try:
            params = {
                "app": "CailianpressWeb",
                "os": "web",
                "rn": "100",
            }
            resp = self.fetch_url(
                "https://www.cls.cn/nodeapi/telegraphList",
                params=params, max_retries=2,
            )
            if resp and resp.status_code == HTTP_OK_STATUS:
                data = resp.json()
                for item in data.get("data", {}).get("roll_data", []):
                    content = item.get("content", "")
                    title = item.get("title", "") or content[:100]
                    category = self._classify_news(title + " " + content)
                    if not category:
                        continue
                    importance = self._assess_importance(title + " " + content)
                    news_time = self._parse_timestamp(item.get("ctime"))
                    # 财联社快讯ID构建链接
                    item_id = item.get("id") or ""
                    news_url = f"https://www.cls.cn/detail/{item_id}" if item_id else ""
                    results.append({
                        "source": "cailianshe_global",
                        "title": title, "content": content,
                        "category": category, "news_time": news_time,
                        "importance": importance,
                        "url": news_url,
                    })
        except Exception as e:
            logger.warning(f"财联社国际新闻采集失败: {e}")
        return results

    def _collect_from_wallstreetcn(self) -> list[dict[str, Any]]:
        """华尔街见闻要闻/快讯"""
        results = []
        try:
            import requests as _req
            # 华尔街见闻 7x24 快讯 API
            url = "https://api-one-wscn.awtmt.com/apiv1/content/lives"
            params = {"channel": "global-channel", "limit": "50"}
            headers = {
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://wallstreetcn.com/",
            }
            resp = _req.get(url, params=params, headers=headers, timeout=12)
            if resp.status_code == HTTP_OK_STATUS:
                data = resp.json()
                items = data.get("data", {}).get("items", [])
                for item in items:
                    content_text = item.get("content_text", "")
                    title = content_text[:120] if content_text else ""
                    category = self._classify_news(title)
                    if not category:
                        continue
                    importance = self._assess_importance(title)
                    # 华尔街见闻标记重要性
                    if item.get("is_important"):
                        importance = min(importance + 2, IMPORTANCE_MAX)

                    news_time = None
                    display_time = item.get("display_time")
                    if display_time:
                        with suppress(Exception):
                            news_time = datetime.fromtimestamp(int(display_time))

                    # 华尔街见闻快讯链接
                    item_id = item.get("id") or ""
                    news_url = f"https://wallstreetcn.com/live/{item_id}" if item_id else "https://wallstreetcn.com/live"
                    results.append({
                        "source": "wallstreetcn",
                        "title": title, "content": content_text[:500],
                        "category": category, "news_time": news_time,
                        "importance": importance,
                        "url": news_url,
                    })
                if results:
                    logger.info(f"华尔街见闻采集: {len(results)} 条国际新闻")
        except Exception as e:
            logger.debug(f"华尔街见闻采集失败: {e}")
        return results

    def _collect_from_jin10(self) -> list[dict[str, Any]]:
        """金十数据快讯（全球财经日历和快讯）"""
        results = []
        try:
            import requests as _req
            url = "https://flash-api.jin10.com/get_flash_list"
            params = {"channel": "-8200", "max_time": "", "vip": "1"}
            headers = {
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://www.jin10.com/",
                "x-app-id": "bVBF4FyRTn5NJF5n",
                "x-version": "1.0.0",
            }
            resp = _req.get(url, params=params, headers=headers, timeout=12)
            if resp.status_code == HTTP_OK_STATUS:
                data = resp.json()
                items = data.get("data", [])
                for item in items:
                    # 金十的数据结构
                    content = item.get("data", {})
                    if isinstance(content, dict):
                        title = content.get("content", "")
                    elif isinstance(content, str):
                        title = content
                    else:
                        continue
                    if not title or len(title) < MIN_NEWS_TITLE_LEN:
                        continue
                    # 清除HTML标签
                    import re
                    title = re.sub(r"<[^>]+>", "", title).strip()
                    category = self._classify_news(title)
                    if not category:
                        continue
                    importance = self._assess_importance(title)
                    # 金十标星级别
                    if item.get("important"):
                        importance = min(importance + 2, IMPORTANCE_MAX)

                    news_time = None
                    time_str = item.get("time")
                    if time_str:
                        with suppress(Exception):
                            news_time = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")

                    # 金十快讯链接
                    item_id = item.get("id") or ""
                    news_url = f"https://www.jin10.com/flash_detail/{item_id}.html" if item_id else "https://www.jin10.com/"
                    results.append({
                        "source": "jin10",
                        "title": title[:300], "content": title[:500],
                        "category": category, "news_time": news_time,
                        "importance": importance,
                        "url": news_url,
                    })
                if results:
                    logger.info(f"金十数据采集: {len(results)} 条国际快讯")
        except Exception as e:
            logger.debug(f"金十数据采集失败: {e}")
        return results

    def _collect_from_eastmoney_global(self) -> list[dict[str, Any]]:
        """东方财富全球资讯"""
        results = []
        try:
            import akshare as ak
            df = ak.stock_info_global_em()
            if df is not None and not df.empty:
                cutoff = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
                for idx, (_, row) in enumerate(df.head(200).iterrows()):
                    title = str(row.get("标题", "") or "")
                    content = str(row.get("摘要", "") or title)
                    t = str(row.get("发布时间", "") or "").strip()
                    news_time = None
                    if t:
                        try:
                            news_time = datetime.strptime(t, "%Y-%m-%d %H:%M:%S")
                        except Exception:
                            news_time = None
                    if news_time and news_time < cutoff:
                        continue
                    category = self._classify_news(title + " " + content)
                    if not category and idx < EARLY_CATEGORY_ROW_LIMIT:
                        category = "经济数据"
                    if not category:
                        continue
                    importance = self._assess_importance(title + " " + content)
                    art_url = str(row.get("链接", "") or "")
                    results.append({
                        "source": "eastmoney_global",
                        "title": title[:300], "content": content[:500],
                        "category": category, "news_time": news_time or datetime.now(),
                        "importance": importance,
                        "url": art_url,
                    })
                if results:
                    logger.info(f"东方财富全球资讯: {len(results)} 条")
        except Exception as e:
            logger.debug(f"东方财富全球资讯失败: {e}")
        return results

    # ============================================================
    # 市场指标
    # ============================================================

    def _collect_commodity_prices(self) -> list[dict[str, Any]]:
        """大宗商品价格异动（多源: AKShare → 东方财富）"""
        results = []
        filled = False

        # 源1: AKShare
        try:
            import akshare as ak
            for symbol, label, threshold in [
                ("WTI原油", "WTI原油", 2),
                ("伦敦金", "伦敦金", 1),
                ("伦敦银", "伦敦银", 2),
                ("COMEX铜", "COMEX铜", 2),
            ]:
                try:
                    df = ak.futures_foreign_commodity_realtime(symbol=symbol)
                    if df is not None and not df.empty:
                        latest = df.iloc[0]
                        change_pct = float(latest.get("涨跌幅", 0) or 0)
                        price = latest.get("最新价", "N/A")
                        if abs(change_pct) > threshold:
                            results.append({
                                "source": "akshare",
                                "title": f"{label}价格异动: {price} 涨跌幅 {change_pct:+.2f}%",
                                "content": f"{label}最新价 {price}, 涨跌幅 {change_pct:+.2f}%",
                                "category": "大宗商品",
                                "news_time": datetime.now(),
                                "importance": HIGH_IMPORTANCE if abs(change_pct) > HIGH_VOLATILITY_PCT else DEFAULT_IMPORTANCE,
                            })
                            filled = True
                except Exception:
                    pass
        except Exception:
            pass

        # 源2: 东方财富直接API（兜底）
        if not filled:
            try:
                url = "https://push2.eastmoney.com/api/qt/ulist.np/get"
                params = {
                    "fltt": "2", "invt": "2",
                    "fields": "f2,f3,f4,f12,f14",
                    "secids": "113.CL00Y,113.GC00Y,113.SI00Y,113.HG00Y",
                    "ut": "fa5fd1943c7b386f172d6893dbbd10d0",
                }
                data = get_em_client().request_json(
                    url,
                    params=params,
                    timeout=10000,
                    referer="https://quote.eastmoney.com/",
                )
                if data:
                    label_map = {"CL": "WTI原油", "GC": "黄金", "SI": "白银", "HG": "铜"}
                    for item in (data.get("data", {}) or {}).get("diff", []) or []:
                        code = item.get("f12", "")
                        price = item.get("f2", "N/A")
                        change_pct = float(item.get("f3", 0) or 0)
                        label = next((v for k, v in label_map.items() if k in code), code)
                        threshold = 1 if "GC" in code else 2
                        if abs(change_pct) > threshold:
                            results.append({
                                "source": "eastmoney",
                                "title": f"{label}价格异动: {price} 涨跌幅 {change_pct:+.2f}%",
                                "content": f"{label}最新价 {price}, 涨跌幅 {change_pct:+.2f}%",
                                "category": "大宗商品",
                                "news_time": datetime.now(),
                                "importance": HIGH_IMPORTANCE if abs(change_pct) > HIGH_VOLATILITY_PCT else DEFAULT_IMPORTANCE,
                            })
            except Exception as e:
                logger.debug(f"大宗商品(东方财富API)失败: {e}")
        return results

    def _collect_market_indicators(self) -> list[dict[str, Any]]:
        """采集VIX恐慌指数、美元人民币汇率等市场指标"""
        results = []
        try:
            # VIX + 美元人民币: 东方财富全球指标
            url = "https://push2.eastmoney.com/api/qt/ulist.np/get"
            params = {
                "fltt": "2", "invt": "2",
                "fields": "f2,f3,f4,f12,f14",
                # VIX=100.VIX, 美元人民币=119.USDCNH, 美元指数=100.UDI
                "secids": "100.VIX,119.USDCNH,100.UDI",
                "ut": "fa5fd1943c7b386f172d6893dbbd10d0",
            }
            data = get_em_client().request_json(
                url,
                params=params,
                timeout=10000,
                referer="https://quote.eastmoney.com/",
            )
            if data:
                for item in (data.get("data", {}) or {}).get("diff", []) or []:
                    code = item.get("f12", "")
                    price = item.get("f2", "N/A")
                    change_pct = float(item.get("f3", 0) or 0)

                    if "VIX" in code:
                        # VIX > 20 就是重要信息，> 30 恐慌
                        try:
                            vix_val = float(price)
                            if vix_val > VIX_IMPORTANT_LEVEL or abs(change_pct) > HIGH_VOLATILITY_PCT:
                                level = "恐慌" if vix_val > VIX_PANIC_LEVEL else "警戒" if vix_val > VIX_WARNING_LEVEL else "偏高"
                                results.append({
                                    "source": "eastmoney",
                                    "title": f"VIX恐慌指数{level}: {vix_val:.1f} ({change_pct:+.2f}%)",
                                    "content": f"VIX恐慌指数 {vix_val:.1f}，变化 {change_pct:+.2f}%，{level}水平",
                                "category": "美股",
                                "news_time": datetime.now(),
                                "importance": VIX_IMPORTANCE_HIGH if vix_val > VIX_PANIC_LEVEL else VIX_IMPORTANCE_DEFAULT,
                            })
                        except (ValueError, TypeError):
                            pass
                    elif "USDCNH" in code:
                        if abs(change_pct) > USDCNH_ALERT_PCT:
                            direction = "贬值" if change_pct > 0 else "升值"
                            results.append({
                                "source": "eastmoney",
                                "title": f"人民币{direction}: 美元/人民币 {price} ({change_pct:+.2f}%)",
                                "content": f"离岸人民币{direction}，美元/人民币 {price}",
                                "category": "经济数据",
                                "news_time": datetime.now(),
                                "importance": VIX_IMPORTANCE_DEFAULT if abs(change_pct) > FX_IMPORTANCE_ALERT_PCT else DEFAULT_IMPORTANCE,
                            })
                    elif "UDI" in code and abs(change_pct) > UDI_ALERT_PCT:
                        results.append({
                            "source": "eastmoney",
                            "title": f"美元指数异动: {price} ({change_pct:+.2f}%)",
                            "content": f"美元指数 {price}，涨跌幅 {change_pct:+.2f}%",
                            "category": "经济数据",
                            "news_time": datetime.now(),
                            "importance": DEFAULT_IMPORTANCE,
                        })

                if results:
                    logger.debug(f"市场指标采集: VIX/汇率/美元指数 {len(results)} 条")
        except Exception as e:
            logger.debug(f"市场指标采集失败: {e}")
        return results

    # ============================================================
    # 工具方法
    # ============================================================

    def _classify_news(self, text: str) -> str | None:
        """判断新闻分类，非国际相关返回 None"""
        for category, keywords in self.CATEGORY_KEYWORDS.items():
            for kw in keywords:
                if kw in text:
                    return category
        return None

    @staticmethod
    def _assess_importance(text: str) -> int:
        """评估新闻重要性 (1-10)"""
        score = 5
        high_impact = [
            "加息", "降息", "利率决议", "非农", "CPI", "制裁", "关税",
            "暴跌", "暴涨", "熔断", "紧急", "突发", "出口管制", "芯片禁令",
            "财报", "超预期", "不及预期", "下调", "上调",
        ]
        medium_impact = [
            "美联储", "FOMC", "PMI", "GDP", "中美", "原油",
            "英伟达", "苹果", "特斯拉", "AI", "半导体",
        ]
        for w in high_impact:
            if w in text:
                score = min(score + 2, 10)
        for w in medium_impact:
            if w in text:
                score = min(score + 1, 10)
        return score

    @staticmethod
    def _parse_timestamp(ctime) -> datetime | None:
        if ctime:
            try:
                return datetime.fromtimestamp(int(ctime))
            except (ValueError, TypeError):
                pass
        return None

    def _save_to_db(self, items: list[dict], db_path: str):
        """保存到数据库"""
        try:
            with get_db_session(db_path) as session:
                for item in items:
                    record = GlobalNews(
                        source=item.get("source", self.SOURCE_NAME),
                        title=item.get("title", ""),
                        content=item.get("content"),
                        category=item.get("category"),
                        news_time=item.get("news_time"),
                        importance=item.get("importance", 5),
                        url=item.get("url", ""),
                    )
                    session.add(record)
            logger.info(f"国际新闻保存完成: {len(items)} 条")
        except Exception as e:
            logger.error(f"国际新闻保存失败: {e}")
