# 最初 12 项 DSA 对齐完成核验

本记录对应用户最初确认的「1 到 12 全做」，核验当前代码而非原始差距快照。其后扩展的数据可靠性、策略和研究协议实现见[全面补齐实施](dsa_alignment_implementation_2026-10-02.md)。固定参考仍为此前审计的 daily_stock_analysis `be148f39`。

## 十二项实现

| 编号 | 完成行为 | 实现与回归 |
| --- | --- | --- |
| 1 | 每日分析、信号生成、日报、自选报告、自学习、信号后验和提醒摘要在独立进程执行，超过 `scheduler.job_timeout_minutes` 终止进程树并报告错误。间隔采集另有采集预算与进程保护。 | `src/scheduler.py`；`tests/test_scheduler_isolation.py` |
| 2 | 新闻搜索区分成功、无结果、失败和关闭；诊断如实披露覆盖范围，失败或未搜索不能解释为没有利空。 | `news_search.py`、`stock_diagnosis.py`；`test_news_search.py`、`test_report_language.py` |
| 3 | 安装包冒烟实际运行 MiniRacer 原生库、LiteLLM 本地价格表、拼音、内置策略、报告模板与二维码，再启动后台检查接口。增加内置英文策略资源检查。 | `server.py --self-check`、`scripts/smoke_backend.py`；`test_server.py` |
| 4 | 实盘出入金流水具有账户、日期、方向、金额和备注，可新增/查看/删除；接入现金重放、净入金与累计收益，随账户更名。补拒绝 NaN、无穷、非法数字和四舍五入后为零的金额，防止余额污染。 | `real_portfolio.py`、`api/v1/trading.py`、`RealPage.tsx`；`test_real_accounts.py`、`test_real_portfolio.py` |
| 5 | 个股、ETF/指数决策员以及技术/情报/风险分析员、策略会诊、策略审议采用完整英文模板。由同一事实生成英文段落，保留市场阶段、行情日期、资讯状态及分析员数据范围。19 个内置策略均有英文规则；解析枚举保持原值。 | `diagnosis_prompts.py`、`diagnosis_agents.py`、`strategy_synthesis.py`、`skills/*.yaml`；`test_report_language.py` |
| 6 | 个股 profile 聚合行情、研究产物、信号、持仓、监控、历史报告和相关资讯；支持 `history_days`，各块独立返回质量与限制。研究概览展示报告、持仓与提醒入口。 | `stock_profile.py`、`api/v1/stocks.py`、`StockPage.tsx`；`test_stock_profile.py`、前端研究回归 |
| 7 | 活跃决策信号可手动关闭或作废并记录原因，终态不能重新激活。 | `decision_signals.py`、`api/v1/signals.py`、`SignalsPage.tsx`；`test_decision_signals.py` |
| 8 | RSS/Atom 与 NewsNow JSON 订阅共用资讯源管理；可从财联社、华尔街见闻、金十、格隆汇和 MarketWatch 模板添加。 | `rss.py`、资讯设置及管理页；`test_rss.py`、前端模板回归 |
| 9 | 补齐 12 个指数，内置共 35 个；中证与国证独有指数采用官网日线，保留交易所和 `.csi` 别名。 | `fund_registry.py`、`fund_data.py`；`test_fund_registry.py` |
| 10 | 常用股票简称与别名接入统一搜索，精确别名优先，同时覆盖自选与机器人复用入口。 | `stock_search.py`；`test_stock_lookup.py` |
| 11 | 增加 Anspire 与 MiniMax 新闻搜索，接入密钥轮换、错误/空结果区分和降级链。 | `news_search.py`；`test_news_search.py` |
| 12 | 增加 AIHubMix、Anspire、MiniMax、OpenRouter、StepFun、xAI 六个模型平台预设；未填写兼容接口地址时采用所选平台默认地址。 | `llm_platforms.py`、模型路由与设置；LLM 配置回归 |

## 使用与边界

在【设置 → AI 模型 → AI 输出语言】选择英文，或配置 `report.language: en`。界面语言独立设置。原始股票名称、源新闻和存储枚举保留原文，不调用翻译模型。自定义策略可填写 `instructions_en`；没有英文规则时保留用户原始规则，避免改变策略含义。中文诊断沿用中文模板。

实盘资金锚点与出入金并存时，流水按日期末计入；设置锚点当天的流水视为锚点之后。仅有完整出入金账本、没有现金锚点时计算「总资产 − 净入金」累计收益，不能把它当作时间加权收益率。

## 本轮验证

- 后端：`.venv/bin/python -m pytest -q -p no:cacheprovider tests/`，1,938 项通过，4 个第三方弃用警告；Python 编译和默认策略资源检查通过。
- Web：180 项测试、ESLint、TypeScript 与构建通过；桌面端 24 项测试通过。
- 真实 Chromium 隔离验收通过：工作台、历史报告与导出身份、选股、设置保存、提醒、来源降级、错误任务恢复、窄屏及认证故障恢复；无页面异常。
- 英文回归检查完整指令、事实价格、分析员范围、19 个内置策略、用户规则保留、审议枚举、未完成日线和搜索状态；出入金回归检查非法金额不写账本且余额不变。
- macOS ARM64 桌面后台：`scripts/build_desktop.py --backend-only --skip-web` 构建成功；`scripts/smoke_backend.py` 通过。MiniRacer、价格表、拼音、含英文规则的策略、报告模板和二维码均实际运行；子进程任务入口返回预期退出码，健康、首页、模型设置、技能与模板接口全部返回 200。

离线测试使用临时数据库和假模型；浏览器隔离服务禁用外网、真实模型、交易及通知。付费供应商真实账户权限、真实模型英语生成质量和其他操作系统的本轮安装启动不由这些离线测试保证；Windows/Linux 以远端发行验证为准。本次补修不修改已有发布标签。
