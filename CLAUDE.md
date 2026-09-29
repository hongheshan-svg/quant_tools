# CLAUDE.md

本文件为 Claude Code (claude.ai/code) 在此仓库中工作时提供指引。

## 项目概述

A股舆情驱动量化交易系统：从新闻、社交热榜、行情等多个来源采集舆情数据，用 LLM 分析，按加权因子给涨停股打分，预测下一交易时段的涨停股，并可把交易信号转成模拟盘订单。项目在 Windows 上开发，所以入口脚本会强制 stdout 使用 UTF-8。

技术栈与 daily_stock_analysis 对齐：Python 后端（FastAPI `api/` + 业务代码 `src/`）、React Web 前端（`apps/web`）、Electron 桌面端（`apps/desktop`，内嵌打包的后台服务）、钉钉/飞书聊天机器人（`src/bot`）、Docker（`docker/`）和 GitHub Actions 定时运行。原 PyQt6 桌面端（`src/desktop`、`run_dashboard.py`）仍保留可用，新功能优先做在 Web 端。

## 环境准备

```bash
pip install -r requirements.txt
playwright install chromium   # 必需：东方财富、同花顺、社交平台采集器依赖无头 Chromium
cp config/settings.yaml.example config/settings.yaml   # 然后填入 LLM API Key
cd apps/web && npm ci && npm run build   # Node.js 22+；FastAPI 托管 apps/web/dist，没构建时访问页面返回 404 提示
cd apps/desktop && npm install            # 只有开发 Electron 桌面端时需要
```

SQLite 数据库会自动创建在 `data/quant.db`，LLM 响应缓存在 `data/llm_cache.sqlite3`。`data/`、`logs/` 和 `config/settings.yaml` 都已被 git 忽略。

## 运行

```bash
python server.py                            # Web 界面 + API（默认 127.0.0.1:8000）+ 定时任务 + 聊天机器人；--no-scheduler 只提供接口
python main.py                              # 无界面 APScheduler 定时循环（含聊天机器人）
python main.py --once [--steps collect,analysis]   # 按顺序执行一遍收盘后任务后退出（GitHub Actions、Docker 用）
cd apps/web && npm run dev                  # 前端开发服务器 :5173，/api 代理到 8000（API_TARGET 可改）
cd apps/desktop && npm run dev              # Electron 开发模式：在空闲端口启动仓库里的 server.py
docker compose -f docker/docker-compose.yml up -d
python scripts/check_sources.py [--only 腾讯,财联社] [--no-browser]   # 联网检查各数据源，不写库
python run_dashboard.py                     # PyQt6 桌面端（旧版）
python run_dashboard.py --headless          # 只执行一次 PipelineService.run_full()，不打开界面
python run_dashboard.py --warmup-before-ui  # 先跑完整流程，再打开界面
python run_full.py                          # 基于已采集数据一次性执行：LLM 舆情 → 题材 → 国际因子 → 评分 → Top 10
python run_score.py                         # 只对已有数据评分
python run_demo.py                          # 演示采集与展示
python scripts/fetch_history.py --mode daily --start-date 2024-01-01   # 回补历史数据；mode 可选 all/daily/limit_up/concepts/dragon_tiger
```

日线下载与解析在 `src/collectors/daily_history.py`，脚本只负责批量调度。`fetch_history.py` 的日线回补默认按每只股票的最新日期续传。显式指定 `--start-date` 时，历史起点晚于该日期 10 天以上的股票会从起点重新下载，因为日常采集会给每只股票写入当天行情，否则所有股票都会被当成已是最新。`--force-full` 忽略续传，`--overwrite` 重新下载并覆盖已有行（用于修复旧版本写错的成交量/成交额）。

API 文档在 http://127.0.0.1:8000/docs 。

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
- CI 机器没有 libEGL，`import PyQt6` 抛的是 `ImportError` 而不是 `ModuleNotFoundError`（`pytest.importorskip` 挡不住）。测试不要导入 Qt 模块；需要测的逻辑放到不依赖 Qt 的模块里（如 `src/desktop/markdown_render.py`）。
- API 测试用 `tests/test_api.py` 的 `env` fixture（临时库 + `TestClient` + 临时 `settings.yaml`）；后台任务用 `_wait()` 轮询到结束。

前端和桌面端（CI 的 `web`、`desktop` 任务）：

```bash
cd apps/web && npm run lint && npm test && npm run build   # ESLint、Vitest + Testing Library、tsc + Vite
cd apps/desktop && npm test                                 # node --test，不需要安装 Electron
```

- 前端测试用 `stubFetch()` 伪造接口，或 `vi.spyOn(api, '...')`；页面按 `role="tab"` 找标签页。
- 桌面端测试只测 `src/backend.js` 的纯逻辑，`preload.test.js` 通过 `Module._load` 替换 `electron` 模块。

## 打包

- **桌面端安装包：** `python scripts/build_desktop.py`（`--backend-only`、`--skip-web`、`--dir`）。依次构建前端 → 用 PyInstaller 把 `server.py` 打成 onedir 的 `dist/backend/quant_server`（内置 `settings.yaml.example`、`stock_pool.yaml` 和前端，排除 PyQt6）→ electron-builder 打包（Windows NSIS、macOS dmg，产物在 `apps/desktop/dist/`）。只能打包当前系统的安装包；推送 `v*` 标签时 `desktop-release.yml` 在 Windows 和 macOS 上打包并上传到 Release。
  - 运行时按字符串导入或带数据文件、原生库的包要加到脚本里的 `HIDDEN_IMPORTS`、`COLLECT_DATA`、`COLLECT_SUBMODULES`、`COLLECT_ALL`（例如 litellm 的价格表、akshare 的数据文件、py_mini_racer 的动态库）。打包后记得实际运行 `quant_server` 冒烟，缺文件只有运行到那段代码才会报错。
  - 打包的后台服务用 `--workdir` 指定数据目录，启动时把内置的示例配置复制进去（每次覆盖），股票池规则只在缺失时复制；没传 `--workdir` 时用可执行文件所在目录。它还会在后台执行 `playwright install chromium`。
- **旧版 PyQt6 EXE：** `powershell .\scripts\build_exe.ps1`，需要不在仓库里的 `AStockQuantQt6.spec`（`*.spec` 被 git 忽略）。运行时 `run_dashboard.py` 切换到 EXE 所在目录。

## 架构

**采集器**（`src/collectors/`）→ SQLite（`src/database/`）→ **分析器**（`src/analyzers/`，调用 LLM）→ **策略**（`src/strategy/`）→ **交易**（`src/trading/`）

### 三套运行时，各自编排

- **`server.py` → `api/app.py`**：Web 界面和 API。`web.scheduler` 为真（默认）时在 lifespan 里用 `build_scheduler(config, BackgroundScheduler())` 运行和 `main.py` 相同的定时任务，并在后台线程启动聊天机器人；`--no-scheduler` 两者都不运行（用于 `main.py` 已在运行的情况）。Electron 桌面端和 Docker 都运行它。
- **`main.py` → `src/scheduler.py`**（BlockingScheduler，同时启动聊天机器人）有两类任务：
  - 间隔任务：热搜每 30 分钟，财联社每 5 分钟，行情每 15 分钟，国际新闻每 30 分钟。
  - 工作日定时任务：
    - 15:30 每日分析：舆情 → 涨停 → 国际因子 → `CompositeScorer.score_today()` → 距上次历史回测超过 `screening.backtest_interval_days` 天时先回测 → `StrategyScreener.run()`（全市场策略选股）。
    - 16:00 生成信号 → `TradeAdvisor.advise_top_stocks()`（AI 研判，`strategy.ai_advisor_enabled`）→ `ExecutionService.execute_signals()` 生成订单。
    - 16:10 `MarketReviewService.generate()`（LLM 大盘复盘，`market_review.enabled`）→ 推送日报。
    - 16:20 自学习。
    - 16:30 `WatchlistReportService.run()`：自选股逐只 AI 诊断，推送决策仪表盘（`watchlist.daily_report`）。
  - 行情采集任务结束后依次调用 `ExecutionService.generate_exit_orders()`（持仓止损止盈）和 `AlertService.run()`（盘中提醒）。
  - 所有间隔和时间都读自 `scheduler.*` 配置项。
  - 行情采集和上面三个每日任务在非交易日跳过（`_skip_non_trade_day()`），新闻、热搜、国际新闻照常采集。
  - `run_once(config, steps)`（`main.py --once`）按 `ONCE_STEPS` 的固定顺序把这些任务各执行一次。
- **PyQt6 桌面端（旧版）**（`run_dashboard.py` → `src/desktop/main_window.py`）**不使用** `scheduler.py`：
  - Qt 定时器和按钮驱动 `PipelineService`（`collect()` → `self_learn()` → `premarket_predict()`；预测后生成订单并推送日报，15:00 后的预测还会先生成当天的大盘复盘）。每次自动采集完成后，在后台依次调用 `check_exits()` 和 `check_alerts()`。
  - 【信号绩效】（含 AI 诊断验证）【大盘复盘】【策略选股】【主线分析】【盘中提醒】页在切换到该页时才读取或计算，其中大盘复盘、策略选股和历史回测只读取上次的结果，点击按钮才重新生成；【数据源状态】页读取的是进程内的健康记录。【模拟交易】页每次刷新后在后台计算组合风险。
  - 个股详情对话框（在表格中双击股票，或用顶部搜索框按代码/名称/拼音首字母打开）：本地日线不足 60 根时在后台补齐后重绘 K 线；【AI诊断】页调用 `PipelineService.diagnose_stock()`，打开时只显示上次的诊断结果，点击按钮才调用 AI；【新闻公告】页切换过去才联网获取。
  - 【AI 问股】页持有一个 `StockChatSession`，切换 AI 平台后重建。
  - 【自选股】【实盘记账】页在切换到该页时读取。【推送设置】对话框（`src/desktop/push_settings_dialog.py`）会重写 `settings.yaml` 的 `notifier` 段，保存后 `reload_config()`。
  - 需要进度的后台任务把 `worker.signals.progress.emit`（数字）或 `status.emit`（文字）作为参数传给被调用的函数。
  - 【模拟交易】页通过 `PipelineService` 的交易方法确认、撤销订单。这些方法共用一把锁，因为它们在不同工作线程里被调用。
  - `CollectorOrchestrator` 负责并发采集。
  - `DataQueryService` 提供界面上的全部查询。
  - 耗时任务放在 `QThreadPool` 工作线程里执行。

新增任务或数据源时，需要接入每一个应该运行它的运行时。新增的界面功能要同时提供 API（`api/v1/`）和 Web 页面（`apps/web`）。

### Web API（`api/`）

- `create_app(config, *, pipeline=, start_scheduler=, static_dir=, auth=)`：测试可注入 `PipelineService`、关掉定时任务、指定前端目录和 `AuthStore`。`app.state` 上有 `pipeline`、`tasks`（`TaskManager`）、`auth`、`chat_store`、`background`（本进程是否运行定时任务和机器人）。
- 路由在 `api/v1/`（system、market、stocks、screening、chat、watchlist、trading），统一前缀 `/api/v1`。其他路径托管 `apps/web/dist`（单页应用，找不到的路径返回 `index.html`；`/docs` 等 FastAPI 自带路由优先）。`DEFAULT_STATIC_DIR` 按仓库位置解析，与工作目录无关。
- **耗时操作走后台任务：** `tasks.submit(kind, fn, *args, dedupe_key=, label=)` 立即返回任务字典，前端轮询 `/api/v1/tasks/{id}`。被调用的函数如果有 `progress` 参数会自动传入，`progress(done, total)` 或 `progress(文字)` 都可以；结果经 `jsonable_encoder` 转换。同一 `dedupe_key` 的任务在执行中时直接返回已有任务。
- **访问控制**（`api/auth.py`，`check_request()`）：没开 `web.auth_enabled` 时只允许本机（含 TestClient 的 `testclient`）；开了以后要登录 Cookie（`qt_session`，HMAC 签名，密码 PBKDF2 存在 `data/web_auth.json`，首次登录即设置密码）或 `Authorization: Bearer <web.api_token>`。`/api/v1/health` 和 `/api/v1/auth/*` 公开。
- **设置接口：** 返回时把密钥替换成 `******`（大模型 Key 保留后 4 位），保存时掩码原样回传就保留原值（`_merge_llm`、`_merge_notifier`、`_merge_bot`）；用 `settings_store.save_section()` 写回，再 `apply_config(app, reload_config())` 让 pipeline 和问股会话使用新配置。
- AI 问股的多会话存在 `chat_session` 表（`src/services/chat_sessions.py` 的 `ChatSessionStore`），每个会话一把锁。

### Web 前端（`apps/web`）

- React 19 + TypeScript + Vite + Tailwind 4 + react-router + zustand + recharts。`@/` 指向 `src/`。
- 接口：`src/api/client.ts`（`http.get/post/put/del/upload`，401 时派发 `auth:required` 事件）、`src/api/endpoints.ts`（`api` 对象）、`src/api/types.ts`。新增接口时三处一起改。
- 数据加载用 `useApi(fn, deps)`；后台任务用 `useTask().run(() => api.xxx(), { success })`，它会轮询到任务结束并显示进度，任务中心（右上角）列出全部任务。
- 通用组件在 `src/components/ui.tsx`（Button、Card、Tabs、Modal、Field…）和 `DataTable.tsx`、`CandlestickChart.tsx`（SVG K 线）。颜色用 `index.css` 里的 CSS 变量（深色/浅色主题），A 股习惯红涨绿跌：`text-up` 红、`text-down` 绿。
- 在 Electron 里运行时 `window.quantDesktop`（`src/utils/desktop.ts`）可用，设置页据此显示「桌面端」页。

### Electron 桌面端（`apps/desktop`）

- `main.js`：单实例；在 8000–8100 找空闲端口启动后台服务，轮询 `/api/v1/health` 就绪后加载 Web 界面；启动失败或运行中崩溃显示 `renderer/loading.html` 的错误页（可重试）；退出时停止后台服务（Windows 用 `taskkill /T` 结束进程树）；站外链接用系统浏览器打开。日志写到数据目录的 `logs/desktop.log`。
- `src/backend.js`：不依赖 Electron 的纯逻辑（找端口、启动命令、健康检查、停止进程），测试只测这里。
- 开发模式用仓库 `.venv` 里的 Python 运行 `server.py`，数据就是仓库的 `config/`、`data/`；打包后运行 `resources/backend/quant_server`，数据目录为系统应用数据目录。环境变量：`QUANT_HOME`（数据目录）、`QUANT_PYTHON`（开发时的 Python）、`QUANT_BACKEND_PATH`（指定后台程序）。
- `preload.js` 通过 `contextBridge` 暴露 `quantDesktop`（version、info、openDataDir、openLogDir、retry）。

### 聊天机器人（`src/bot/`）

- `router.CommandRouter`：与平台无关的命令分发，返回 Markdown。不带参数的命令（大盘、持仓…）要整句匹配，带参数的（诊断、自选）按前缀匹配，其余交给 AI 问股（按 `平台:会话:用户` 保存 `StockChatSession`，闲置 30 分钟后重建）。`bot.allowed_users` 非空时只允许名单内的用户。测试通过 `pipeline=`、`chat_factory=` 注入假对象。
- `dispatcher.Dispatcher`：平台回调先确认收到，消息放进线程池处理；按消息 ID 去重（平台会重发）；回复按字节拆分。
- `dingtalk.py`（Stream 模式，回复走消息里的 sessionWebhook）和 `feishu.py`（长连接，回复消息卡片，标题 `#` 转成加粗）。消息解析 `parse_message()` 是纯函数，不依赖 SDK。
  - 飞书 SDK 的长连接客户端用模块级事件循环，在 uvicorn 里导入会拿到正在运行的循环，所以在自己的线程里换成新循环。
  - 两个 SDK 的重连间隔都改成逐次加长（`retry_delay()`，最多 10 分钟），凭证填错时不刷日志。
- `manager.start_bots(config, pipeline)`：同一进程每个平台只启动一次，占位或空的凭证跳过；SDK 导入较慢（打包后十几秒），服务里在后台线程调用。改了已在运行的机器人的凭证要重启服务。

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
- 实时行情按 `data_sources.realtime` 的顺序回退，默认腾讯 `qt.gtimg.cn`（普通 HTTP）→ 东方财富 → AKShare 新浪 → efinance → 通达信（pytdx，全市场约 3 秒，名称取自 `stock_info`）。
- 个股日线（回补和按需补齐）按 `data_sources.daily_history` 的顺序回退：`daily_history.DAILY_SOURCES` 把配置名映射成（来源标记, 取数函数），默认腾讯 → 新浪 → 东方财富 → baostock → 通达信 → efinance → Tushare。来源标记 `tx`/`daily`/`em` 走原有解析；额外数据源（`src/collectors/extra_sources.py`）统一返回 date、OHLC、volume（股）、amount（元）、turnover（小数）。
  - baostock 用模块级单连接，调用加锁，登录输出被静默；不支持北交所。
  - 通达信按「`data_sources.pytdx_servers` → 上次连上的 → 内置列表 → pytdx 自带列表」尝试服务器（pytdx 自带的大多已失效）；日线不复权，成交量单位是手。
  - efinance 走东方财富接口，东方财富不可用时它也不可用；Tushare 需要 `data_sources.tushare_token`，日线不复权。
- 财联社电报用签名的 `/v1/roll/get_roll_list`（`cailianshe.sign_params()`：参数按键排序后 SHA1 再 MD5），每次最多 50 条（超过返回空列表）；旧的 `nodeapi` 接口已 404，只作兜底。
- 东方财富 `push2`/`push2his` 行情接口有时直接断开连接（2026-09 观察到，浏览器和普通 HTTP 都一样），依赖它的指数、资金流、efinance 会失败，所以各处都要有回退。`scripts/check_sources.py` 可以快速确认各源状态。
- 涨停池：东方财富涨停池 → 东方财富强势股池。强势股池里有没涨停的股票，所以只保留涨幅达到该板块涨停幅度的行。
- 涨停原因（概念题材）来自同花顺涨停池（`src/collectors/limit_up_reasons.py`，普通 HTTP，不需要 Playwright），入库时写入 `limit_up_stock.concepts`（用 + 连接），并优先作为 `reason`。同花顺失败不影响涨停池入库。历史数据用 `fetch_history.py --mode concepts` 补齐。
- 结构不适合改成回退链的内联回退（指数、板块、北向资金、各资讯采集器），直接调用 `source_health.record()` 记录健康状态。
- 个股资金流（`src/collectors/fund_flow.py`）：同花顺即时资金流 → 东方财富（分页，每页最多 100 条），随行情采集每 10 分钟最多一次，15:05 后再取一次收盘快照，写入 `stock_fund_flow`。
- 筹码和业绩（`src/collectors/fundamentals.py`）：筹码分布先用 AKShare，失败时用本地日线按换手率衰减估算（至少 60 根日线）；业绩预告/快报由 `EarningsCache` 按报告期整批缓存。二者只在个股诊断时按需获取。
- 行情里的市盈率、市净率（`stock_daily.pe/pb`）来自腾讯字段 39/46 和东方财富 f9/f23，新浪没有；接口用 0 或 `-` 表示没有数据，入库为空。
- 成交量单位各数据源不一致（腾讯、新浪、历史日线为股，东方财富为手），成交额统一为元。需要量比时用成交额比（策略选股就是这样做的）。
- 按需补齐日线：`ensure_daily_history(code, db_path)`（`src/collectors/daily_history.py`）在本地近 150 天日线少于 60 根时联网下载，只写入缺失的交易日，失败的股票 30 分钟内不重试。个股详情、AI 诊断、问股工具、技术指标提醒都会调用它，相关测试要把它 monkeypatch 掉。
- 个股新闻与公告：`get_stock_news(code)`（`src/collectors/stock_news.py`，东方财富资讯搜索和公告接口，普通 HTTP），进程内缓存 30 分钟。公告标题命中关键词时标注风险，其中立案、退市风险警示等是严重风险。「没有新闻」不算数据源失败（`is_valid` 放行空列表），也不能用数据集级的 `allow_stale`，否则会拿到别的股票的缓存。
- 股票搜索：`StockSearch`（`src/services/stock_search.py`）用 `stock_info` 和最新一天行情建索引，按代码、名称、拼音首字母（`pypinyin`，多音字给出多种组合）匹配，进程内缓存 12 小时。`pypinyin` 自带 PyInstaller hook，打包不用额外配置。
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
- **环境变量覆盖：** `QUANT__LLM__PRIMARY__API_KEY=sk-xxx` 覆盖 `llm.primary.api_key`（层级用双下划线，键名转小写；原值是字符串的项取原文，其余按 YAML 解析；空值忽略）。只作用于 `settings.yaml`（`stock_pool.yaml` 也用 `load_config` 读取，不受影响）。写回文件时 `strip_env_overrides()` 会剔除这些值，保证 Secrets 不落盘。
- 运行时会重写 `settings.yaml` 的代码，都会丢掉文件里的注释：
  - 自学习（`save_config()`）写入完整的合并后配置。
  - `settings_store.save_section()`：Web 设置接口，以及 PyQt6 的 AI 设置、推送设置对话框，按段写回。

### 大盘复盘、AI 研判与策略选股

- `build_market_facts()`（`src/services/market_context.py`）把指数、涨跌家数、成交额、量化大盘环境、题材/行业主线、降温板块和财联社重要快讯汇总成 `MarketFacts`，供各 LLM 提示词共用。`news=False` 时不读快讯。
- `MarketReviewService`（`src/services/market_review.py`）按趋势结构、资金情绪、主线板块复盘，输出次日姿态、仓位、关注与回避方向、观察要点。
  - **护栏：** 姿态不能比量化大盘环境更激进（冰点、防守最多给防守，均衡最多给均衡），被下调时仓位改用对应档位。
  - **存储：** 每个交易日一条（`market_review` 表），重新生成覆盖。已有收盘后生成的复盘时直接复用，盘中生成的会在收盘后重新生成。
  - **日报：** 日报只读取已生成的复盘，不会调用 LLM。
- `TradeAdvisor`（`src/services/trade_advisor.py`）给 Top 评分股写入 `ai_verdict`，观望和回避的信号在 `_is_buy_signal()` 中被拦下，不生成订单。评分没有选中的 Top 股票，只补建 `signal_type="hold"` 的参考记录，AI 不能把它们变成买入；AI 涨停预测（`premarket`）信号的研判也不会被覆盖。
- `StrategyScreener`（`src/strategy/screener.py`）是全市场策略选股：
  - **流程：** 最新交易日行情 → 股票池过滤（`stock_pool.yaml` 的名称关键词、黑名单、股价、流通市值；逐只查库的 `RiskManager._check_stock_pool()` 太慢，这里是批量实现）→ 成交额 3000 万以上的股票取近 60 个交易日日线计算特征 → 各策略规则打分。
  - **策略：** 内置 6 个策略，参数可在 `screening.strategies.<策略>` 覆盖；每个策略标注适配的大盘环境，适配的排在前面；同一只股票被多个策略选中时，取最高分并每多一个策略加 5 分。
  - **存储与次日表现：** 结果按「交易日 + 策略 + 代码」存入 `strategy_pick`，每行存各策略自己的分数；`performance()` 统计各策略选股的次日表现。
  - **预测器接入：** 预测器只在盘前、盘后时段调用选股，因为盘中成交额不完整；策略选股归入「全市场」来源，这样自学习的来源统计不用改。
  - **策略权重：** 最近 30 天内有历史回测时，按回测得出的权重（0.8~1.2）乘到各策略得分上（`screening.adaptive_strategy_weights`）。
- `StrategyBacktester`（`src/strategy/strategy_backtest.py`）对区间内每个全市场交易日（当天行情不少于 1000 只）调用 `StrategyScreener.run(point_in_time=True)` 重新选股，结果存入 `strategy_backtest`。
  - **防止未来数据：** `point_in_time=True` 时策略权重不生效，大盘环境传 `overview={}`，因为 `MarketRegimeAnalyzer.analyze()` 不传 overview 时会读进程内今天的实时指数缓存。其他按日期取数的代码也要注意这一点。
  - **入场与统计：** 次日开盘入场，统计 1/3/5 日收益、次日涨停率、每日等权组合的累计收益和最大回撤。
  - **策略权重：** 样本不少于 30 个的策略按次日平均收益相对全体的差值调整权重。
- `DiagnosisOutcomeService`（`src/services/diagnosis_outcome.py`）对近 60 天的 AI 诊断做事后验证（只读）：以诊断行情日收盘价为基准统计之后 1/3/5 日涨跌，买入/加仓算看多、减仓/卖出/回避算看空，持有/观望不判方向；同一行情日多次诊断只算最后一次。
- 自选股：`WatchlistService`（`src/services/watchlist.py`，`watchlist` 表）负责增删和批量导入。批量导入的表格有「代码」列表头时只读取代码列，避免把数量、金额里的 6 位数字当成代码。
  - **决策仪表盘：** `WatchlistReportService`（`src/services/watchlist_report.py`）并发诊断每只自选股（`watchlist.workers`）。收盘后复用当天收盘后的诊断，盘中复用 30 分钟内的。结果汇总成仪表盘，存入 `watchlist_report` 并推送。
  - **提醒与问股：** 盘中提醒和问股的 `watchlist` 工具都会读取自选股。
- AI 问股 `StockChatSession`（`src/services/stock_chat.py`）：
  - **协议：** 每个问题最多 3 轮工具调用。LLM 用 JSON 返回 `{"tool_calls": [...]}` 或 `{"answer": ...}`，不依赖各家模型的 function calling。
  - **工具：** 工具在 `src/services/chat_tools.py`，全部只读，不能下单；股票参数可以是名称或拼音，会先经 `StockSearch` 解析。
  - **测试：** 通过 `llm=`、`tools=` 注入假对象。

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

- `LLMClient`（`src/analyzers/llm_client.py`）默认通过 LiteLLM 调用（`llm.backend: litellm`）。`build_route()` 把配置转成路由：anthropic、gemini、ollama 用原生的 `提供商/模型`，其他平台按 OpenAI 兼容接口走 `openai/模型` + `api_base`；Key 为空或仍是 `your-` 占位时跳过（ollama 不需要 Key）。`llm.backend: openai` 改用 OpenAI SDK 直连（不支持原生提供商）。平台预设在 `src/analyzers/llm_platforms.py`。
  - 导入 LiteLLM 一律用 `llm_usage.import_litellm()`：它让 LiteLLM 使用本地价格表，否则导入时会联网下载。
  - **用量统计：** 每次调用（含命中缓存和失败）由 `record_usage()` 写进缓存库的 `llm_usage` 表，记录 token 和估算费用（`llm.pricing` 可自定义单价）。功能按调用栈里第一个 `src.`/`api.` 模块归类，映射表是 `FEATURE_LABELS`，新增调用 LLM 的模块要加进去，否则显示为「其他」。`usage_summary()` 供 Web【AI 用量】页使用。
  - 主模型失败时切换到备用模型；备用模型的 Key 仍以 `your-` 开头时会被跳过。
  - 响应缓存在 SQLite 里，缓存键是模型 + 提示词 + 参数的哈希，带 TTL。
  - `reload()` 可热切换模型平台。
  - `chat_json()` 解析失败时依次尝试：提取 markdown 代码块 → 修复截断的 `items` 列表 → 截到最后一个完整对象后交给 `json_repair`。截断的输出不能直接交给 `json_repair`，它会把半截的对象（如半截股票代码）当成有效数据保留下来。
- 操作建议统一用 `src/analyzers/decision.py`：`normalize_action()` 把文本归一为 buy、add、hold、watch、reduce、sell、avoid、alert 八种。否定说法（「不建议买入」）识别为 avoid；多个关键词同时出现时，取最先出现的；无法识别时返回空字符串。下单只接受 `is_bullish()` 为真的建议。
- AI 预测保存前会经过 `_validate_predictions()`：剔除在 `stock_daily` 和 `stock_info` 里都查不到的代码（AI 编造的）、ST/退市股和重复代码，名称以数据库为准。行情库为空时无法校验，直接跳过。
- 个股诊断 `StockDiagnosisService`（`src/services/stock_diagnosis.py`）：
  - **流程：** 先用 `build_context()` 汇总行情、技术面、涨停记录、主线、大盘、资讯、龙虎榜和持仓，再由 LLM 输出决策仪表盘 JSON。
  - **护栏：** `_apply_guardrails()` 负责动作归一化，缺失时按评分推断，价格计划用 `sanitize_price_plan()` 校验。以下情况的买入建议降为观望：
    - 评分低于 50；
    - 大盘冰点；
    - 行情或日线不足 20 根；
    - 数据完整度（行情、日线、技术面、资金流、筹码、大盘、资讯、业绩按权重计分）低于 60%；
    - 资金净流出超过成交额的 5%；
    - 与 3 天内上次诊断方向相反，但评分变化不足 15 分；
    - 近 30 天公告含严重风险（立案、退市风险警示等）。
  - **多智能体：** 由 `diagnosis.mode` 控制，见 `src/services/diagnosis_agents.py`。
    - `standard` 和 `full` 模式下，分析员只拿到按「【标题】」拆出的部分上下文，并发调用；决策员沿用原来的提示词，外加 `DECISION_ADDENDUM`。
    - 分析员观点冲突（看多和看空并存，或评分相差 25 分以上）时，信心最高为「中」。
    - 代码里的默认值是 `single`，示例配置里是 `standard`，所以测试用的最小配置只调用一次 LLM。
  - **历史校准：** 由 `diagnosis.calibration` 控制。`diagnosis_outcome.calibration_stats()` 汇总近 90 天诊断的事后准确率，进程内缓存 30 分钟，写进提示词；看多诊断的 3 日准确率低于 45%（至少 10 次）时，买入信心下调一档。
  - **测试：** 诊断前会补齐日线，并获取筹码、业绩、个股新闻和公告，这些都会联网。测试要 monkeypatch `fundamentals.fetch_chip_summary`、`EarningsCache.get`、`stock_news.get_stock_news` 和 `daily_history.ensure_daily_history`（参考 `tests/test_stock_diagnosis.py` 的 fixture）。
  - **缓存：** 结果存入 `stock_diagnosis` 表，30 分钟内复用。
  - **注入假 LLM：** 测试通过构造函数的 `llm=` 参数注入（`MarketReviewService` 也一样）。
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
- 实盘记账 `RealPortfolioService`（`src/services/real_portfolio.py`）只记账，不连券商、不下单。
  - **成交流水：** 存在 `real_trade` 表，手动录入或导入交割单（`parse_trade_rows()` 按常见列名识别）。导入用 `import_key` 去重：有成交编号时按编号，否则按日期+时间+代码+方向+价格+数量+序号。
  - **持仓：** 按移动平均成本计算，费用计入成本。
  - **可用资金：** 用 `real_cash` 表记录的锚点，加上锚点之后的成交推算。
  - **止损止盈：** `real_position_plan` 可以逐只覆盖止损价、目标价，没设置时按风控比例从成本计算。
  - **使用方：** 实盘持仓（`account="real"`）会进入盘中提醒、组合风险、个股诊断和问股。
- 组合风险 `PortfolioRiskService`（`src/services/portfolio_risk.py`，`account="paper"`/`"real"`）只读计算以下几项：
  - 总仓位与大盘环境建议仓位（`risk.market_regime_position`）的比较；
  - 个股和行业集中度，行业取最近一次涨停时的所属行业；
  - 距止损价的距离；
  - 回撤：按 `trade_fill` 和每日收盘价重放账户净值（模拟盘没有手续费），得出最大回撤和当前回撤。

### 推送（`src/notifier/`、`src/services/daily_report.py`）

- 统一通过 `notifier.broadcast(config, title, content, kind=)` 推送。
  - **消息类型：** `kind` 取值为 `daily_report`、`alert`、`watchlist`、`chat`，按 `notifier.routes` 选择渠道；没配置路由的类型推送到全部已启用渠道。
  - **渠道条件：** `enabled_channels(config, kind)` 只认启用且配置完整的渠道。Webhook 还是示例占位符（含 `your-`）不算完整；邮件至少要有 SMTP 服务器和收件人。
  - **新增推送：** 新增推送时要传 `kind`，测试里替换 `broadcast`、`enabled_channels` 时要接受 `kind` 参数。
- 渠道：企业微信、钉钉、飞书机器人和邮件（`src/notifier/mail.py`，SMTP，同时发送纯文本和 HTML）。`diagnose()` 检查配置，`test_channel()` 发送测试消息（导入测试文件时要改名，否则 pytest 会把它当成测试）。
- 各渠道 `send()` 用 `split_by_bytes()` 按字节上限拆分消息并逐条发送：企业微信 3800、钉钉 18000、飞书 20000。
- 飞书签名比较特殊：用「时间戳\n密钥」作为 HMAC 密钥，对空消息签名。
- `DailyReportService.push()` 在没有任何渠道启用时直接返回，不会生成报告，也不会访问网络。
- 盘中提醒 `AlertService`（`src/services/alert_service.py`）只在交易时段运行（`trading_calendar.in_trade_session()`）。
  - **监控范围：** 今日信号股、模拟盘持仓和 `alerts.watchlist`。
  - **内置提醒：** 封涨停、炸板、跌破止损（紧急）、接近止损、达到目标价、大跌，以及大盘环境比前一交易日降档或评分明显下滑。大盘两天都按 `overview={}` 计算，保证口径一致。
  - **自定义规则：** 可在 `alerts.rules` 设置价格突破、涨跌幅、放量，以及技术指标规则：均线、MACD/KDJ 金叉死叉、RSI 阈值。指标用 `src/analyzers/indicators.py` 计算，当天的行就是最新价。
  - **每天一次：** 指标交叉、接近止损、大盘转弱在 `DAILY_ONCE_TYPES` 中，每天只提醒一次。
  - **降噪：** `NoiseFilter`（`src/notifier/noise.py`）负责冷却期去重和免打扰时段（`notifier.quiet_hours`），紧急提醒不受免打扰限制。同一次检查的多条提醒合并成一条推送，所有提醒都记入 `alert_record` 表。
  - **状态：** 涨停状态和冷却记录是进程级状态，测试前后要调用 `alert_service.reset_state()`。

### 部署与 CI（`docker/`、`.github/workflows/`）

- Docker：多阶段构建（先构建前端），以 UID 1000 的 `quant` 用户运行，默认 `python server.py --host 0.0.0.0`。默认配置放在镜像的 `/app/defaults/config`，`entrypoint.sh` 启动时复制进挂载的 `/app/config`（示例配置覆盖、股票池缺失才复制），并修复挂载目录属主。compose 用环境变量开启 Web 登录，因为容器外的请求不算本机。
- 工作流：`ci.yml`（后端测试、前端 lint/测试/构建、桌面端测试）、`daily-analysis.yml`（工作日 16:40 `main.py --once`，需要仓库变量 `ENABLE_DAILY_ANALYSIS=true`，配置来自 Secret `SETTINGS_YAML` 或映射成 `QUANT__` 环境变量的单独 Secret，数据库用 actions/cache 保留）、`network-smoke.yml`（`check_sources.py`，`ENABLE_NETWORK_SMOKE=true`）、`docker-publish.yml`（`v*` 标签发布 GHCR 镜像）、`desktop-release.yml`（`v*` 标签打包桌面端并上传 Release）。
- Actions 里给布尔配置映射 Secret 时要写成 `${{ secrets.X != '' && 'true' || '' }}`：直接写比较表达式在 Secret 为空时得到字符串 `false`，会覆盖 `SETTINGS_YAML` 里的设置；空字符串才会被忽略。

## 约定

- 文档字符串、注释和日志信息用中文（辅助文档采用中文）。
- 提交信息遵循 Conventional Commits 前缀（`feat:`、`fix:`、`refactor:`、`test:`、`docs:`、`chore:`），一个提交只做一件事。

每次修改提交git
