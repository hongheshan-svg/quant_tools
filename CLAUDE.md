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

## 打包（Windows EXE）

运行 `powershell .\scripts\build_exe.ps1`。脚本用 `.venv\Scripts\python.exe` 和 `AStockQuantQt6.spec` 调用 PyInstaller；该 `.spec` 文件不在仓库里，因为 `*.spec` 被 git 忽略。产物是 `dist\AStockQuantQt6.exe`，脚本会把 `config/settings.yaml` 复制到它旁边。打包后运行时，`run_dashboard.py` 会切换到 EXE 所在目录，所以 `config/`、`data/`、`logs/` 都相对该目录解析。

## 架构

**采集器**（`src/collectors/`）→ SQLite（`src/database/`）→ **分析器**（`src/analyzers/`，调用 LLM）→ **策略**（`src/strategy/`）→ **交易**（`src/trading/`）

### 两套运行时，各自编排

- **`main.py` → `src/scheduler.py`**（BlockingScheduler）有两类任务：
  - 间隔任务：热搜每 30 分钟，财联社每 5 分钟，行情每 15 分钟，国际新闻每 30 分钟。
  - 工作日定时任务：15:30 每日分析（舆情 → 涨停 → 国际因子 → `CompositeScorer.score_today()`），16:00 生成信号，16:20 自学习。
  - 所有间隔和时间都读自 `scheduler.*` 配置项。
- **桌面端**（`run_dashboard.py` → `src/desktop/main_window.py`）**不使用** `scheduler.py`：
  - Qt 定时器和按钮驱动 `PipelineService`（`collect()` → `self_learn()` → `LimitUpPredictor.predict()`）。
  - `CollectorOrchestrator` 负责并发采集。
  - `DataQueryService` 提供界面上的全部查询。
  - 耗时任务放在 `QThreadPool` 工作线程里执行。

新增任务或数据源时，需要接入每一个应该运行它的运行时。

### 数据源

- 实时行情按顺序回退：腾讯 `qt.gtimg.cn`（普通 HTTP）→ 东方财富 → AKShare 新浪（见 `StockDataCollector._collect_realtime_quotes`）。
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
- `SelfLearningService.run_daily_learning()` 用之后的 `StockDaily` 和 `LimitUpStock` 数据检验历史信号的结果，并计算各因子与结果的相关性。
  - 它把 `strategy.adaptive_weights` 和按新闻源区分的 `strategy.source_confidence` 写回 `settings.yaml`，除非设置了 `strategy.learning.persist_to_yaml: false`。
  - 同时写入一条 `LearningSnapshot` 记录。

### LLM

- `LLMClient`（`src/analyzers/llm_client.py`）通过 OpenAI SDK 调用任意兼容 OpenAI 协议的 `base_url`（DeepSeek、通义千问、智谱 GLM、Kimi、硅基流动等）。
  - 主模型失败时切换到备用模型；备用模型的 Key 仍以 `your-` 开头时会被跳过。
  - 响应缓存在 SQLite 里，缓存键是模型 + 提示词 + 参数的哈希，带 TTL。
  - `reload()` 可热切换模型平台。
- `LimitUpPredictor`（`src/services/premarket_predictor.py`）按时段选择提示词，时段为 `premarket`（9:25 前）、`morning`、`noon`、`afternoon`、`aftermarket`（15:00 后）。

### 交易（`src/trading/`）

- `ExecutionService` 通过 `BrokerAdapter` 把 `TradeSignal` 记录转成 `TradeOrder` 记录。目前只有 `PaperBrokerAdapter`（模拟盘）一个实现。
- 新订单初始状态为 `PENDING_CONFIRM`，用 `order_mapper.build_idempotency_key` 去重。
- `RiskManager` 负责仓位限制和 ST/*ST 黑名单。

## 约定

- 文档字符串、注释和日志信息用中文（辅助文档采用中文）。
- 提交信息遵循 Conventional Commits 前缀（`feat:`、`fix:`、`refactor:`、`test:`、`docs:`、`chore:`），一个提交只做一件事。
- `AGENTS.md` 提到的 Alembic、`frontend/`、`infra/` 在本仓库中都不存在，这些部分请忽略。

每次修改提交git
