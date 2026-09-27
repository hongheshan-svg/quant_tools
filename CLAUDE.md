# CLAUDE.md

本文件为 Claude Code (claude.ai/code) 在此仓库中工作时提供指引。

## 项目概述

A股舆情驱动量化交易系统：从新闻、社交热榜、行情等多个来源采集舆情数据，用 LLM 分析，按加权因子给涨停股打分，预测下一交易时段的涨停股，并可把交易信号转成模拟盘订单。项目在 Windows 上开发，所以入口脚本会强制 stdout 使用 UTF-8，打包脚本也是 PowerShell。

## 环境准备

```bash
pip install -r requirements.txt
playwright install chromium   # 必需：东方财富、同花顺、社交平台采集器依赖无头 Chromium
cp config/settings.yaml.example config/settings.yaml   # 然后填入 LLM API Key
```

SQLite 数据库会自动创建在 `data/quant.db`，LLM 响应缓存在 `data/llm_cache.sqlite3`。`data/`、`logs/` 和 `config/settings.yaml` 都已被 git 忽略。

## 运行

```bash
python main.py                              # 无界面 APScheduler 定时循环
python run_dashboard.py                     # PyQt6 桌面端（主界面）
python run_dashboard.py --headless          # 只执行一次 PipelineService.run_full()，不打开界面
python run_dashboard.py --warmup-before-ui  # 先跑完整流程，再打开界面
python run_full.py                          # 基于已采集数据一次性执行：LLM 舆情 → 题材 → 国际因子 → 评分 → Top 10
python run_score.py                         # 只对已有数据评分
python run_demo.py                          # 演示采集与展示
python scripts/fetch_history.py --mode daily --start-date 2024-01-01   # 回补历史数据；mode 可选 all/daily/limit_up/dragon_tiger；默认断点续传，--force-full 全量重拉
```

没有任何入口脚本会启动 FastAPI Web 仪表盘（`src/dashboard/app.py`），需要手动运行 `uvicorn src.dashboard.app:app --port 8000`。

## 测试

大部分测试依赖 pytest fixture（`monkeypatch`、`tmp_path`），但 requirements.txt 里没有 pytest：

```bash
pip install pytest
python -m pytest -q tests/
python -m pytest -q tests/test_ths_client.py::test_request_json_cookie_fallback   # 运行单个测试
```

- 测试要在仓库根目录运行。测试里用了相对路径（`config/settings.yaml`、`data/test_quant.db`），`test_fetch_history_daily_fallback.py` 还会导入 `scripts.fetch_history`。
- 只有 `test_basic.py` 和 `test_self_learning.py` 能当普通脚本直接运行（`python tests/test_basic.py`）。
- `.gitignore` 忽略了 `tests/` 以外所有位置的 `test_*.py`，新测试必须放在 `tests/` 下。
- 涉及数据库的测试要先重置引擎单例（可照搬 `tests/test_self_learning.py` 里的 `_reset_db_engine()`）。
- 测试必须能离线运行，GitHub CI（`.github/workflows/ci.yml`，Python 3.12）会在每次推送 main 时跑全部测试。会联网的地方（交易日历、上市日期、采集器）要 monkeypatch 掉；交易日历可以直接用 `trading_calendar._set_days({...})` 指定。

## 打包（Windows EXE）

运行 `powershell .\scripts\build_exe.ps1`。脚本用 `.venv\Scripts\python.exe` 和 `AStockQuantQt6.spec` 调用 PyInstaller；该 `.spec` 文件不在仓库里，因为 `*.spec` 被 git 忽略。产物是 `dist\AStockQuantQt6.exe`，脚本会把 `config/settings.yaml` 复制到它旁边。打包后运行时，`run_dashboard.py` 会切换到 EXE 所在目录，所以 `config/`、`data/`、`logs/` 都相对该目录解析。

## 架构

**采集器**（`src/collectors/`）→ SQLite（`src/database/`）→ **分析器**（`src/analyzers/`，调用 LLM）→ **策略**（`src/strategy/`）→ **交易**（`src/trading/`）

### 两套运行时，各自编排

- **`main.py` → `src/scheduler.py`**（BlockingScheduler）有两类任务：
  - 间隔任务：热搜每 30 分钟，财联社每 5 分钟，行情每 15 分钟，国际新闻每 30 分钟。
  - 工作日定时任务：15:30 每日分析（舆情 → 涨停 → 国际因子 → `CompositeScorer.score_today()`），16:00 生成信号并调用 `ExecutionService.execute_signals()` 生成订单，16:10 推送日报，16:20 自学习。
  - 行情采集任务结束后会调用 `ExecutionService.generate_exit_orders()`，检查持仓是否触及止损或止盈。
  - 所有间隔和时间都读自 `scheduler.*` 配置项。
  - 行情采集和上面三个每日任务在非交易日跳过（`_skip_non_trade_day()`），新闻、热搜、国际新闻照常采集。
- **桌面端**（`run_dashboard.py` → `src/desktop/main_window.py`）**不使用** `scheduler.py`：
  - Qt 定时器和按钮驱动 `PipelineService`（`collect()` → `self_learn()` → `premarket_predict()`；预测后生成订单并推送日报）。每次自动采集完成后，在后台调用 `check_exits()`。
  - 【信号绩效】【主线分析】页在切换到该页时才计算，【数据源状态】页读取的是进程内的健康记录。
  - 个股详情对话框（在交易决策表中双击股票）的【AI诊断】页调用 `PipelineService.diagnose_stock()`。打开时只显示上次的诊断结果，点击按钮才调用 AI。
  - 【模拟交易】页通过 `PipelineService` 的交易方法确认、撤销订单。这些方法共用一把锁，因为它们在不同工作线程里被调用。
  - `CollectorOrchestrator` 负责并发采集。
  - `DataQueryService` 提供界面上的全部查询。
  - 耗时任务放在 `QThreadPool` 工作线程里执行。

新增任务或数据源时，需要接入每一个应该运行它的运行时。

### 交易日历（`src/trading_calendar.py`）

- `load(db_path, refresh=True)` 把新浪交易日历（缓存在 `trade_calendar` 表）读进内存；缓存超过 7 天或覆盖不到今天之后 30 天时联网更新，失败沿用缓存，6 小时内不重复尝试。`refresh=False` 只读本地缓存。
- `is_trade_day()` / `next_trade_day()` 只查内存，可在界面线程高频调用；没有日历或日期超出日历范围时退化为周一至周五。
- 调度器、预测器负责联网刷新；桌面端启动时先读缓存再在后台刷新；自学习只读缓存。判断交易日一律用这个模块，不要再写跳过周末的简易规则。
- `market_data_ready()`：交易日且已过 9:25。在此之前（包括节假日），行情接口返回的是上一个交易日的数据，所以按当天日期入库的行情和涨停池采集会直接跳过。
- 数据库里可能已经有旧版本在节假日写入的重复数据。按日期取数的分析代码（大盘环境、主线、信号绩效、自学习）要先用 `trade_days_only()` 或 `is_trade_day()` 过滤掉非交易日。

### 数据源

- 多数据源统一用 `source_chain.fetch_with_fallback(dataset, [(名称, 取数函数)], attempts=, allow_stale=)` 取数：
  - **熔断：** 每个数据集一个 `CircuitBreaker`，连续失败 3 次熔断 5 分钟，冷却后放行一次探测；全部熔断时仍会尝试第一个源。
  - **缓存兜底：** 开启 `allow_stale` 时，所有源都失败后返回上次成功的数据，并标记 `stale`。按日期入库的数据（行情）不要开这个选项。
  - **健康记录：** 每次尝试都会记入进程级的 `source_health`。熔断器、缓存和健康记录都是进程级状态，测试前后要清空（参考 `tests/test_source_chain.py` 的 fixture）。
- 实时行情按顺序回退：腾讯 `qt.gtimg.cn`（普通 HTTP）→ 东方财富 → AKShare 新浪。
- 涨停池：东方财富涨停池 → 东方财富强势股池。强势股池里有没涨停的股票，所以只保留涨幅达到该板块涨停幅度的行。
- 涨停原因（概念题材）来自同花顺涨停池（`src/collectors/limit_up_reasons.py`，普通 HTTP，不需要 Playwright），入库时写入 `limit_up_stock.concepts`（用 + 连接），并优先作为 `reason`。同花顺失败不影响涨停池入库。历史数据用 `fetch_history.py --mode concepts` 补齐。
- 结构不适合改成回退链的内联回退（指数、板块、北向资金、各资讯采集器），直接调用 `source_health.record()` 记录健康状态。
- 股票代码统一用 `src/utils/stock_code.py`（`bare_code`、`code_candidates`、`exchange_of`、`board_of`、`daily_limit_pct`），不要再手写代码前缀规则。北交所新代码以 92 开头，不是上交所。
- 东方财富数据统一走 `em_client.get_em_client()`：这是一个 Playwright 单例，用来绕过 TLS 指纹检测，替代 AKShare 的 `ak.*_em()` 系列函数。不要直接调用 `ak.*_em()`。
- `browser_client.py` 每个线程持有一个 Playwright 实例，因为同步 Playwright 对象不能跨线程使用。`ths_client.py`（同花顺）在它之上加了重试、Cookie 回退和过期缓存回退。
- `BaseCollector.fetch_url()` 和 `post_url()` 带指数退避重试。`safe_collect()` 会吞掉所有异常并返回 `[]`，所以采集器出错只会体现在日志里。
- `CollectorOrchestrator.collect_all()` 要求所有新闻源（cailianshe、xueqiu、jiuyan、hot_topics、weibo、douyin、toutiao）以及行情、国际新闻、美股财报这几组都有数据。缺哪组就只重试哪组，最多 `desktop.collect_max_attempts` 次。

### 数据库（`src/database/`）

- `db.py` 在模块级保存引擎和会话工厂单例，所以 `init_db(path)` 和 `get_db_session(path)` 只有第一次调用时传入的 `path` 生效。要切换数据库（比如测试里），先把 `db._engine` 和 `db._SessionFactory` 设为 `None`。
- `get_db_session()` 退出时自动提交，出异常时回滚。
- 没有 Alembic。`init_db()` 执行 `create_all` 加 `_auto_migrate`，后者只会给模型里新增的列执行 `ALTER TABLE ... ADD COLUMN`。改列名、改类型、删列都要手动迁移。

### 配置（`src/config_loader.py`）

- `load_config()` 把 `settings.yaml` 深度合并到 `settings.yaml.example` 之上，所以新增配置项要在 example 文件里给默认值。
- `settings.yaml` 不存在时，`load_config()` 会回退到 example 文件。
- 结果按路径缓存，`reload_config()` 会清掉缓存。
- 运行时有两处代码会重写 `settings.yaml`，而且都会丢掉文件里的注释：
  - 自学习（`save_config()`）写入完整的合并后配置。
  - 桌面端 AI 设置对话框（`AISettingsDialog._save_to_yaml`）重写 `llm` 部分。

### 评分与自学习

- `CompositeScorer`（`src/strategy/scorer.py`）按 `strategy.weights` 组合 8 个因子分，各因子由 `src/strategy/*_score.py` 计算。
  - 如果开启了 `strategy.learning.enabled` 且 `strategy.adaptive_weights` 非空，每个实际权重为 `base*(1-blend_ratio) + adaptive*blend_ratio`，`blend_ratio` 默认 0.35。
  - `generate_signals()` 把 Top N 中 `strong_buy`/`buy` 的评分写成 `TradeSignal` 记录。
- 大盘环境 `MarketRegimeAnalyzer`（`src/analyzers/market_regime.py`）：
  - **评分：** 从 `stock_daily` 和 `limit_up_stock` 计算，分为趋势 35、短线情绪 45、量能 20 三部分。
  - **档位：** 按总分映射为进攻、均衡、防守、冰点，并给出情绪周期。
  - **样本要求：** 当天有行情的股票少于 300 只时返回「未知」，不做仓位调整。
  - **使用方：** `ExecutionService._regime_adjusted_budget()` 按档位缩放单笔预算（`trading.regime_position_control`），冰点时不开新仓。预测器提示词、日报、桌面端、个股诊断都会用到它。
- 主线 `ThemeTracker`（`src/analyzers/theme_tracker.py`）：
  - **维度：** `analyze(dimension=)` 支持 `industry`（`sector` 字段）和 `concept`（`concepts` 字段，一只股票可属于多个题材）。题材维度会过滤业绩类原因（`is_generic_concept`），以及只属于一只股票的标签。`analyze_all()` 合并两个维度的结果。
  - **热度：** 用近 5 个交易日的涨停池计算每日热度。
  - **阶段与角色：** 判断启动、加速、持续发酵、降温、退潮、观察。至少 3 天数据才判为持续发酵。`stock_roles()` 给出龙头或跟风；一只股票属于多个主线时，取热度最高的那个。
  - **预测器接入：** 预测器在提示词中加入「主线梯队」，并在涨停候选股描述行里标注主线角色。
- `tech_score.analyze_technical()` 返回评分、趋势、MACD、RSI6、乖离率、量比，以及理由和风险；`calculate_tech_score()` 只取其中的分数。按打板策略调整过：乖离率只按高位风险扣分，RSI 高于 80 只提示偏热。预测器会把 `brief()` 摘要追加到涨停候选股的描述行里。
- `SelfLearningService.run_daily_learning()` 用之后的 `StockDaily` 和 `LimitUpStock` 数据检验历史信号的结果，并计算各因子与结果的相关性。
  - 信号的验证日（模块级函数 `signal_evaluation_date()`，信号绩效回测也用它）：`premarket` 信号的 `signal_date` 已经是预测的目标交易日，就用这一天；其他信号都是收盘后用当天数据生成的，必须用下一个交易日验证。用信号当天行情评估等于拿已知结果给自己打分。
  - 它把 `strategy.adaptive_weights` 和按新闻源区分的 `strategy.source_confidence` 写回 `settings.yaml`，除非设置了 `strategy.learning.persist_to_yaml: false`。
  - 同时写入一条 `LearningSnapshot` 记录。
- `SignalPerformanceService`（`src/services/signal_performance.py`）只读统计，不落库，统计口径如下：
  - **入场：** 开盘前生成的信号按验证日开盘价入场，盘中生成的按收盘价入场，依据是 `created_at` 早于还是晚于验证日 9:30。
  - **止损止盈：** 按 T+1 从入场后的下一根 K 线开始判断。开盘就越过止损价或止盈价时按开盘价离场，同一根 K 线同时触及两者时按止损处理。

### LLM

- `LLMClient`（`src/analyzers/llm_client.py`）通过 OpenAI SDK 调用任意兼容 OpenAI 协议的 `base_url`（DeepSeek、通义千问、智谱 GLM、Kimi、硅基流动等）。
  - 主模型失败时切换到备用模型；备用模型的 Key 仍以 `your-` 开头时会被跳过。
  - 响应缓存在 SQLite 里，缓存键是模型 + 提示词 + 参数的哈希，带 TTL。
  - `reload()` 可热切换模型平台。
  - `chat_json()` 解析失败时依次尝试：提取 markdown 代码块 → 修复截断的 `items` 列表 → 截到最后一个完整对象后交给 `json_repair`。截断的输出不能直接交给 `json_repair`，它会把半截的对象（如半截股票代码）当成有效数据保留下来。
- 操作建议统一用 `src/analyzers/decision.py`：`normalize_action()` 把文本归一为 buy、add、hold、watch、reduce、sell、avoid、alert 八种。否定说法（「不建议买入」）识别为 avoid；多个关键词同时出现时，取最先出现的；无法识别时返回空字符串。下单只接受 `is_bullish()` 为真的建议。
- AI 预测保存前会经过 `_validate_predictions()`：剔除在 `stock_daily` 和 `stock_info` 里都查不到的代码（AI 编造的）、ST/退市股和重复代码，名称以数据库为准。行情库为空时无法校验，直接跳过。
- 个股诊断 `StockDiagnosisService`（`src/services/stock_diagnosis.py`）：
  - **流程：** 先用 `build_context()` 汇总行情、技术面、涨停记录、主线、大盘、资讯、龙虎榜和持仓，再由 LLM 输出决策仪表盘 JSON。
  - **护栏：** `_apply_guardrails()` 负责动作归一化，缺失时按评分推断；评分低于 50 的买入降为观望；冰点不给买入；价格计划用 `sanitize_price_plan()` 校验。
  - **缓存：** 结果存入 `stock_diagnosis` 表，30 分钟内复用。
  - **测试：** 通过构造函数的 `llm=` 参数注入假 LLM。
- `LimitUpPredictor`（`src/services/premarket_predictor.py`）按时段选择提示词，时段为 `premarket`（9:25 前）、`morning`、`noon`、`afternoon`、`aftermarket`（15:00 后）。非交易日一律按 `premarket` 处理。预测写入 `TradeSignal` 时，`signal_date` 存的是目标交易日：交易日盘前和盘中是当天，盘后和非交易日是下一个交易日。

### 交易（`src/trading/`）

- `ExecutionService` 通过 `BrokerAdapter` 把 `TradeSignal` 记录转成 `TradeOrder` 记录。目前只有 `PaperBrokerAdapter`（模拟盘）一个实现。入口是 `execute_signals()`，由上面两套运行时在生成信号后调用。委托价优先使用 `TradeSignal.entry_price`，没有时用最新收盘价。
- 价格计划（`src/trading/price_plan.py`）：AI 预测的 `buy_price`/`stop_loss`/`target_price` 先经过 `sanitize_price_plan()`，按最新价和各板块涨跌幅限制校验，不合理的丢弃，再写入 `TradeSignal.entry_price`/`stop_loss_price`/`target_price`。
- `generate_exit_orders()`：持仓最新价 ≤ 止损价或 ≥ 目标价时生成卖单，幂等键为 `日期|代码|sell|exit`，所以同一只股票每天最多一笔。止损价和目标价取最近一笔已成交买单对应信号的价格计划，没有时按 `risk.stop_loss_pct`/`take_profit_pct` 计算。
- 模拟盘实现了 T+1：重放成交时，只有今天之前的买入才计入可卖数量；当天的实时买入不增加可卖数量。
- 新订单初始状态为 `PENDING_CONFIRM`，用 `order_mapper.build_idempotency_key` 去重。`trading.auto_confirm` 为 true 时直接确认下单，且只对模拟盘生效。
- 评分引擎的信号没有 `ai_verdict`，而且 `hold` 评级也记为 `signal_type="buy"`。下单时按当日 `StockScore.recommendation` 过滤，只放行 `strong_buy`/`buy`。不要改 `generate_signals()` 的 `signal_type`，否则会影响自学习对 buy 信号的计分。
- 模拟盘的现金和持仓只存在内存里。默认模拟盘在每次使用前（`_sync_paper_account()`）都会按 `trade_fill` 从初始资金重放一遍，所以测试或新代码要改账户状态，必须通过成交落库，直接改 broker 内存会被覆盖。
- `RiskManager` 负责仓位限制、ST/*ST 黑名单和 `config/stock_pool.yaml` 股票池过滤。股票池规则只在 `validate_order_intent()`（下单前校验）里执行；`filter_signals()` 目前没有调用方。黑名单和股票池只对买单生效，卖单（止损止盈离场）不能被拦截。
- 当 `min_listing_days > 0` 时，每个 `RiskManager` 实例第一次校验股票池会调用 `StockInfoCollector.refresh_if_stale()`：如果 `stock_info` 表超过一天没更新，就联网从沪深北交易所列表采集。涉及股票池的测试要 monkeypatch 掉这一步。

### 推送（`src/notifier/`、`src/services/daily_report.py`）

- 统一通过 `notifier.broadcast(config, title, content)` 推送到所有已启用的渠道。`enabled_channels()` 只认 `enabled: true` 且配置了 `webhook_url` 的渠道。
- 各渠道 `send()` 用 `split_by_bytes()` 按字节上限拆分消息并逐条发送：企业微信 3800、钉钉 18000、飞书 20000。
- 飞书签名比较特殊：用「时间戳\n密钥」作为 HMAC 密钥，对空消息签名。
- `DailyReportService.push()` 在没有任何渠道启用时直接返回，不会生成报告，也不会访问网络。

## 约定

- 文档字符串、注释和日志信息用中文（辅助文档采用中文）。
- 提交信息遵循 Conventional Commits 前缀（`feat:`、`fix:`、`refactor:`、`test:`、`docs:`、`chore:`），一个提交只做一件事。

每次修改提交git
