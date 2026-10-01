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
python main.py --stocks 600519,000858       # 只诊断指定股票（也可以是 ETF、指数，如 510300、sh000300）并推送决策仪表盘
python main.py --no-notify                  # 运行任务但不推送消息
python main.py --check-notify               # 检查推送配置后退出（退出码 0 为可用，1 为失败）
python main.py --check-config               # 校验 settings.yaml（未知键、类型、格式、语义）后退出（有错误时退出码 1）
python main.py --debug                      # 打印详细日志
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
python scripts/repair_star_volume.py [--apply]                         # 修复科创板成交量单位错误（默认预览）
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
- 测试必须能离线运行，GitHub CI（`.github/workflows/ci.yml`，Python 3.12）会在每次推送 main 时跑全部测试。会联网的地方（交易日历、上市日期、采集器、新闻搜索）要 monkeypatch 掉；交易日历可以直接用 `trading_calendar._set_days({...})` 指定；新闻搜索要 monkeypatch `src/collectors/news_search.search()` 或 monkeypatch httpx 的请求。测试前后调用 `news_search.reset_state()` 清空缓存和冷却。
- 调用 `main()` 函数的测试会写 `os.environ`（如 `QUANT_NO_NOTIFY`），要先 `monkeypatch.setenv("QUANT_NO_NOTIFY", "")` 保证测试结束后环境变量还原，避免影响其他推送测试。
- CI 机器没有 libEGL，`import PyQt6` 抛的是 `ImportError` 而不是 `ModuleNotFoundError`（`pytest.importorskip` 挡不住）。测试不要导入 Qt 模块；需要测的逻辑放到不依赖 Qt 的模块里（如 `src/desktop/markdown_render.py`）。
- API 测试用 `tests/test_api.py` 的 `env` fixture（临时库 + `TestClient` + 临时 `settings.yaml`）；后台任务用 `_wait()` 轮询到结束。

前端和桌面端（CI 的 `web`、`desktop` 任务）：

```bash
cd apps/web && npm run lint && npm test && npm run build   # ESLint、Vitest + Testing Library、tsc + Vite
cd apps/desktop && npm test                                 # node --test，不需要安装 Electron
```

- 前端测试用 `stubFetch()` 伪造接口，或 `vi.spyOn(api, '...')`；页面按 `role="tab"` 找标签页。操作设置类表单前要等表单初始化完成，否则随后的状态同步会覆盖输入。
- 桌面端测试只测 `src/backend.js` 的纯逻辑，`preload.test.js` 通过 `Module._load` 替换 `electron` 模块。
- 涉及当前日期的实现（如基金日线补齐），测试里要屏蔽或用相对日期。
- `tests/conftest.py` 的 autouse fixture 把 `market_phase.current_phase` 固定为盘后（`effective_daily_bar_date=None`，不触发行情过时护栏），避免诊断测试随运行时间漂移；测试阶段逻辑本身用 `market_phase._real_current_phase` 并传入 `now`、用 `trading_calendar._set_days()` 指定日历。
- 报告模板测试要把 `report_templates.templates_dir()`（或 `CUSTOM_DIR`）monkeypatch 到 `tmp_path`，不能往仓库的 `config/templates/` 写文件。

## 打包

- **桌面端安装包：** `python scripts/build_desktop.py`（`--backend-only`、`--skip-web`、`--dir`）。依次构建前端 → 用 PyInstaller 把 `server.py` 打成 onedir 的 `dist/backend/quant_server`（内置 `settings.yaml.example`、`stock_pool.yaml`、策略技能（`src/services/skills`）和前端，排除 PyQt6）→ electron-builder 打包（Windows NSIS、macOS dmg，产物在 `apps/desktop/dist/`）。只能打包当前系统的安装包；推送 `v*` 标签时 `desktop-release.yml` 在 Windows 和 macOS 上打包并上传到 Release。
  - 打包时用 `build_desktop.py` 的 `--add-data` 把 `src/services/skills` 和内置报告模板 `src/services/templates` 内置，用户自定义策略放在 `config/strategies/`、自定义报告模板放在 `config/templates/`（数据目录）。
  - 运行时按字符串导入或带数据文件、原生库的包要加到脚本里的 `HIDDEN_IMPORTS`、`COLLECT_DATA`、`COLLECT_SUBMODULES`、`COLLECT_ALL`（例如 litellm 的价格表、akshare 的数据文件、py_mini_racer 的动态库）。打包后记得实际运行 `quant_server` 冒烟，缺文件只有运行到那段代码才会报错。
  - 打包的后台服务用 `--workdir` 指定数据目录，启动时把内置的示例配置复制进去（每次覆盖），股票池规则只在缺失时复制；没传 `--workdir` 时用可执行文件所在目录。它还会在后台执行 `playwright install chromium`。
  - Playwright 检测到被 PyInstaller 打包时默认到安装包内找浏览器（`PLAYWRIGHT_BROWSERS_PATH=0`），所以打包运行时 `server.use_system_browser_dir()` 先把该变量指向系统缓存目录（`setup_status.default_browsers_dir()`），下载、启动和配置向导检查用同一个目录。
- **旧版 PyQt6 EXE：** `powershell .\scripts\build_exe.ps1`，需要不在仓库里的 `AStockQuantQt6.spec`（`*.spec` 被 git 忽略）。运行时 `run_dashboard.py` 切换到 EXE 所在目录。

## 架构

**采集器**（`src/collectors/`）→ SQLite（`src/database/`）→ **分析器**（`src/analyzers/`，调用 LLM）→ **策略**（`src/strategy/`）→ **交易**（`src/trading/`）

### 三套运行时，各自编排

- **`server.py` → `api/app.py`**：Web 界面和 API。`web.scheduler` 为真（默认）时在 lifespan 里用 `build_scheduler(config, BackgroundScheduler())` 运行和 `main.py` 相同的定时任务，并在后台线程启动聊天机器人；`--no-scheduler` 两者都不运行（用于 `main.py` 已在运行的情况）。Electron 桌面端和 Docker 都运行它。
- **`main.py` → `src/scheduler.py`**（BlockingScheduler，同时启动聊天机器人）有两类任务：
  - 间隔任务：热搜每 30 分钟，财联社每 5 分钟，行情每 15 分钟，国际新闻每 30 分钟；RSS 资讯源按 `intelligence.interval_minutes`（启用且有启用的源时才注册）。
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

新增任务或数据源时，需要接入每一个应该运行它的运行时；新的定时任务还要登记到 `src/scheduler.py` 的 `JOBS`（Web【设置 → 定时任务】据此列出和立即运行）。新增的界面功能要同时提供 API（`api/v1/`）和 Web 页面（`apps/web`）。

### Web API（`api/`）

- `create_app(config, *, pipeline=, start_scheduler=, static_dir=, auth=)`：测试可注入 `PipelineService`、关掉定时任务、指定前端目录和 `AuthStore`。`app.state` 上有 `pipeline`、`tasks`（`TaskManager`）、`auth`、`chat_store`、`background`（本进程是否运行定时任务和机器人）。
- 路由在 `api/v1/`（system、market、stocks、screening、chat、watchlist、trading），统一前缀 `/api/v1`。其他路径托管 `apps/web/dist`（单页应用，找不到的路径返回 `index.html`；`/docs` 等 FastAPI 自带路由优先）。`DEFAULT_STATIC_DIR` 按仓库位置解析，与工作目录无关。
- `DataQueryService` 的不少方法是给旧版 PyQt 界面写的，返回显示用格式（如统一资讯流的 `tags` 是「美股 | 头部企业」字符串、`level` 是中文）。Web 接口要转换成前端类型声明的格式（`/news` 由 `_web_news()` 把 `tags` 转为列表并加 `important`），不要直接透传。
- **耗时操作走后台任务：** `tasks.submit(kind, fn, *args, dedupe_key=, label=)` 立即返回任务字典，前端轮询 `/api/v1/tasks/{id}`。被调用的函数如果有 `progress` 参数会自动传入，`progress(done, total)` 或 `progress(文字)` 都可以；结果经 `jsonable_encoder` 转换。同一 `dedupe_key` 的任务在执行中时直接返回已有任务。
- **访问控制**（`api/auth.py`，`check_request()`）：没开 `web.auth_enabled` 时只允许本机（含 TestClient 的 `testclient`）；开了以后要登录 Cookie（`qt_session`，HMAC 签名，密码 PBKDF2 存在 `data/web_auth.json`，首次登录即设置密码）或 `Authorization: Bearer <web.api_token>`。`/api/v1/health` 和 `/api/v1/auth/*` 公开。
- **设置接口：** 返回时把密钥替换成 `******`（大模型 Key 保留后 4 位），保存时掩码原样回传就保留原值（`_merge_llm`、`_merge_notifier`、`_merge_bot`）；用 `settings_store.save_section()` 写回，再 `apply_config(app, reload_config())` 让 pipeline 和问股会话使用新配置。新增配置备份/恢复：`GET /settings/export?include_secrets=` 导出（默认掩码 secret_keys 和 webhook URL），`POST /settings/import` 导入（`******` 从当前配置同一路径还原，还原不了的删除并给出警告；原子写入，与 `save_section()` 共用锁；导入会丢失文件注释）。
- **新 API：**
  - **诊断历史：** `GET /stocks/diagnoses`（code、action、days、limit 1~200、offset）、`GET/DELETE /stocks/diagnoses/{id}`、`GET /stocks/diagnoses/{id}/markdown`、`GET /stocks/diagnoses/{id}/image`。查询在 `DataQueryService.list_diagnoses/get_diagnosis/delete_diagnosis`（`PipelineService` 同名委托）。
  - **定时任务：** `GET /system/scheduler`（任务列表和状态）、`POST /system/scheduler/{job_id}/run`（立即运行，非交易日行情和分析类任务照常跳过）。任务 id → 中文名和函数映射在 `src/scheduler.py` 的 `JOBS` 和 `describe_jobs()`；`app.state.scheduler` 仅在本进程运行定时任务时存在。
  - **图片导入：** `POST /watchlist/import-image`（后台任务），识别出的股票经校验后勾选加入。
  - **资讯源配置：** `GET/PUT /settings/intelligence`、`POST /settings/intelligence/test`、`POST /pipeline/collect-rss`。
  - **选股历史：** `GET /screening/dates?limit=`（1~250，附策略列表）、`GET /screening/picks?trade_date=&strategy=`（日期格式不对 422）。
  - **分享图：** `GET /market/review/image?trade_date=`（大盘复盘）、`GET /watchlist/report/image`（最近一份自选股仪表盘）；没有数据 404，渲染失败 503。
  - **重新评估：** `POST /stocks/diagnoses/{id}/reassess`（body `{profile, persist}`），按其他决策风格重算该诊断，persist 为真时保存为该风格的决策信号。
  - **其他设置段：** `GET/PUT /settings/report`（AI 输出语言）、`/settings/watchlist`（仪表盘设置）、`/settings/diagnosis`（决策风格、多智能体模式、股东数据等，body 外层有 `diagnosis` 键）、`/settings/templates` 系列（报告模板，见下文）。
  - **配置校验：** `GET /system/config-check`（`src/services/config_check.py` 的 `check_config()`），配置导入的返回也附带校验结果（只提示，不拦截）。
  - **配置向导：** `GET /system/setup`（`src/services/setup_status.py`）检查大模型、数据、交易日历、推送、自选股、浏览器，以及对外监听时是否开启登录；只查本地、不联网、出错视为未完成。首页据此提示，`/setup` 为向导页，设置页支持 `?tab=`，帮助内容在 `utils/settingsHelp.ts`（中英文两份）。
- AI 问股的多会话存在 `chat_session` 表（`src/services/chat_sessions.py` 的 `ChatSessionStore`），每个会话一把锁。

### Web 前端（`apps/web`）

- React 19 + TypeScript + Vite + Tailwind 4 + react-router + zustand + recharts。`@/` 指向 `src/`。
- 接口：`src/api/client.ts`（`http.get/post/put/del/upload`，401 时派发 `auth:required` 事件）、`src/api/endpoints.ts`（`api` 对象）、`src/api/types.ts`。新增接口时三处一起改。
- 数据加载用 `useApi(fn, deps)`；后台任务用 `useTask().run(() => api.xxx(), { success })`，它会轮询到任务结束并显示进度，任务中心（右上角）列出全部任务。很多任务失败时返回 `{error: ...}` 且状态仍为 done，`useTask` 发现结果带非空 `error` 时提示错误而不是 success，结果照常返回。
- 内容区有错误边界（`components/ErrorBoundary.tsx`，`resetKey` 为当前路径，切换页面清除错误）：页面渲染出错只替换内容区，侧栏照常可用。不要改成 `key={pathname}`，那会在 `/chat` → `/chat/:id` 这类跳转时重新挂载页面、丢掉进行中的状态。
- effect 不能有返回值（除清理函数）：新版 Chromium 的 `scrollIntoView()` 返回 Promise，`useEffect(() => el.scrollIntoView())` 会被 React 当清理函数调用而整页报错；jsdom 没有 scrollIntoView，测试覆盖不到，要写成带花括号的函数体。
- 前端测试的假数据要和接口实际返回一致（如资讯流的 `tags` 是列表），不一致的假数据会掩盖白屏问题。
- 通用组件在 `src/components/ui.tsx`（Button、Card、Tabs、Modal、Field…）和 `DataTable.tsx`、`CandlestickChart.tsx`（SVG K 线）。`Modal` 用 portal 渲染到 `document.body`，叠放时只有最上层弹窗对辅助技术可见（下层 `aria-hidden`），Esc 只关最上层。颜色用 `index.css` 里的 CSS 变量（深色/浅色主题），A 股习惯红涨绿跌：`text-up` 红、`text-down` 绿。
- **国际化**（`src/stores/lang.ts`、`src/i18n/index.ts`）：顶栏和登录页可切换中文/英文（保存在 localStorage `quant-lang`，默认中文）。中文原文作为 i18n key，英文词典分页面放在 `src/i18n/en/*.ts`（`import.meta.glob` 自动合并），组件内用 `const t = useT()`、非组件代码用 `t()`；只翻译界面文字，AI 回答/报告/新闻不翻译（AI 输出语言是后端的 `report.language`，与界面语言分开设置）。每个工作流的新译文放在自己的 `en/*.ts` 文件里。前端测试 `apps/web/src/__tests__/i18n-coverage.test.ts` 检查所有 `t()` 字面量都有译文、译文不含中文、占位符一致——新增文字时要同步补词典。
- 在 Electron 里运行时 `window.quantDesktop`（`src/utils/desktop.ts`）可用，设置页据此显示「桌面端」页。

### Electron 桌面端（`apps/desktop`）

- `main.js`：单实例；在 8000–8100 找空闲端口启动后台服务，轮询 `/api/v1/health` 就绪后加载 Web 界面；启动失败或运行中崩溃显示 `renderer/loading.html` 的错误页（可重试）；退出时停止后台服务（Windows 用 `taskkill /T` 结束进程树）；站外链接用系统浏览器打开。日志写到数据目录的 `logs/desktop.log`。
- `src/backend.js`：不依赖 Electron 的纯逻辑（找端口、启动命令、健康检查、停止进程），测试只测这里。
- 开发模式用仓库 `.venv` 里的 Python 运行 `server.py`，数据就是仓库的 `config/`、`data/`；打包后运行 `resources/backend/quant_server`，数据目录为系统应用数据目录。环境变量：`QUANT_HOME`（数据目录）、`QUANT_PYTHON`（开发时的 Python）、`QUANT_BACKEND_PATH`（指定后台程序）。
- `preload.js` 通过 `contextBridge` 暴露 `quantDesktop`（version、info、openDataDir、openLogDir、retry）。

### 聊天机器人（`src/bot/`）

- `router.CommandRouter`：与平台无关的命令分发，返回 Markdown。不带参数的命令（大盘、持仓…）要整句匹配，带参数的（诊断、自选、策略、批量、历史）按前缀匹配，参数无法解析为策略或股票时整句交给 AI 问股（按 `平台:会话:用户` 保存 `StockChatSession`，闲置 30 分钟后重建）。`bot.allowed_users` 非空时只允许名单内的用户。测试通过 `pipeline=`、`chat_factory=` 注入假对象。
- **策略技能系统**（`src/services/strategy_skills.py`）：19 个内置分析策略（YAML 格式），支持自定义覆盖（`config/strategies/*.yaml`），按修改时间动态缓存。机器人「策略」命令、Web 问股页、API `/chat/skills` 都可获取和使用策略；AI 按选定策略的判断标准回答问题。
- **机器人新命令（从 `HELP_TEXT` 的实际格式）：**
  - `诊断 茅台` / `分析 茅台` / `/analyze 茅台`：个股 AI 诊断。
  - `批量 茅台 宁德时代`：批量诊断最多 5 只股票，逐行返回「操作建议 评分｜一句话结论」。
  - `历史 茅台`：该股票最近 5 次诊断记录。
  - `策略`：列出全部问股策略说明。
  - `策略 龙回头 宁德时代能买吗`：按指定策略提问，格式为「策略 策略名 问题」。
  - `策略 龙回头`：仅策略名时返回该策略的说明、适配环境、用法。
- `dispatcher.Dispatcher`：平台回调先确认收到，消息放进线程池处理；按消息 ID 去重（平台会重发）；回复按字节拆分。
- `dingtalk.py`（Stream 模式，回复走消息里的 sessionWebhook）、`feishu.py`（长连接，回复消息卡片，标题 `#` 转成加粗）、`discord.py`（Gateway 长连接，不需要公网地址）。消息解析 `parse_message()` 是纯函数，不依赖 SDK。
  - 飞书 SDK 的长连接客户端用模块级事件循环，在 uvicorn 里导入会拿到正在运行的循环，所以在自己的线程里换成新循环。
  - 两个 SDK 的重连间隔都改成逐次加长（`retry_delay()`，最多 10 分钟），凭证填错时不刷日志。
  - Discord 需要 requirements 增加 `discord.py`，配置 `bot.discord`（token、allowed_channels、guild_mode 为 mention/all）；需在开发者后台开启 Message Content Intent。
- `manager.start_bots(config, pipeline)`：同一进程每个平台只启动一次，占位或空的凭证跳过；SDK 导入较慢（打包后十几秒），服务里在后台线程调用。改了已在运行的机器人的凭证要重启服务。

### 交易日历（`src/trading_calendar.py`）

- `load(db_path, refresh=True)` 把新浪交易日历（缓存在 `trade_calendar` 表）读进内存；缓存超过 7 天或覆盖不到今天之后 30 天时联网更新，失败沿用缓存，6 小时内不重复尝试。`refresh=False` 只读本地缓存。
- `is_trade_day()` / `next_trade_day()` 只查内存，可在界面线程高频调用；没有日历或日期超出日历范围时退化为周一至周五。
- 调度器、预测器负责联网刷新；桌面端启动时先读缓存再在后台刷新；自学习只读缓存。判断交易日一律用这个模块，不要再写跳过周末的简易规则。
- `market_data_ready()`：交易日且已过 9:25。在此之前（包括节假日），行情接口返回的是上一个交易日的数据，所以按当天日期入库的行情和涨停池采集会直接跳过。
  - 此时「采集数据」改为调用 `StockDataCollector.fill_last_session()`：最近一个交易日（`prev_trade_day()`）的行情不足全市场样本（`FALLBACK_OVERVIEW_SAMPLE_SIZE`，1000 只）时，用腾讯行情补齐，逐条核对行情时间（字段 30，`行情日期` 列），只写属于该交易日的行并按该交易日入库，同时按日期补该日涨停池；交易日 9:15 集合竞价开始后不补。新装程序遇到节假日因此也有行情可用。
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
  - **成交量单位统一：** 实时行情腾讯普通板块是手、科创板（688/689）是股；日线由 `normalize_volume_unit()` 按「成交额/(收盘价×成交量)」判断单位并转换为股；`scripts/repair_star_volume.py` 修复已入库的错误数据。
- **ETF 与指数：** 日线来自腾讯 K 线（`src/collectors/fund_data.py`，不走个股日线回退链），单独存入 `fund_daily`、`fund_info`，不写 `stock_daily`，否则会影响涨跌家数、大盘环境和选股。指数与个股代码会冲突（000001 上证指数与平安银行、000016、000688 等），所以指数的规范代码带交易所前缀（如 `sh000300`），任何地方都不能对指数调用 `bare_code`，统一用 `stock_code.diagnosis_code()`；ETF 用 6 位代码。内置指数在 `fund_registry.INDEXES`；ETF 列表来自新浪，7 天刷新一次，只在 `server.py` 运行定时任务或 `main.py` 常驻时后台刷新。支持搜索、个股页 K 线、AI 诊断（`FundDiagnosisService`，结果字段与个股相同）、问股工具、机器人诊断、加入自选股（决策仪表盘和 `--stocks` 对基金走基金诊断，盘中提醒跳过基金）；不能交易，批量导入、图片导入和实盘记账只识别个股。腾讯 K 线不含成交额，ETF 按成交量×均价估算，指数记 0。
- **数据源能力总览**（`src/services/data_capabilities.py`，`GET /system/capabilities`）：实时行情、个股日线、涨停池、涨停原因、个股资金流、股东数据、联网搜索、RSS 资讯源八个数据集，按实际回退顺序列出各来源是否已配置和健康状态（ok / failing / open / unknown，读自进程级的 source_health 与熔断器）。
- 财联社电报用签名的 `/v1/roll/get_roll_list`（`cailianshe.sign_params()`：参数按键排序后 SHA1 再 MD5），每次最多 50 条（超过返回空列表）；旧的 `nodeapi` 接口已 404，只作兜底。
- 东方财富 `push2`/`push2his` 行情接口有时直接断开连接（2026-09 观察到，浏览器和普通 HTTP 都一样），依赖它的指数、资金流、efinance 会失败，所以各处都要有回退。`scripts/check_sources.py` 可以快速确认各源状态。
- 涨停池：东方财富涨停池 → 东方财富强势股池。强势股池里有没涨停的股票，所以只保留涨幅达到该板块涨停幅度的行。
- 涨停原因（概念题材）来自同花顺涨停池（`src/collectors/limit_up_reasons.py`，普通 HTTP，不需要 Playwright），入库时写入 `limit_up_stock.concepts`（用 + 连接），并优先作为 `reason`。同花顺失败不影响涨停池入库。历史数据用 `fetch_history.py --mode concepts` 补齐。
- 结构不适合改成回退链的内联回退（指数、板块、北向资金、各资讯采集器），直接调用 `source_health.record()` 记录健康状态。
- 个股资金流（`src/collectors/fund_flow.py`）：同花顺即时资金流 → 东方财富（分页，每页最多 100 条），随行情采集每 10 分钟最多一次，15:05 后再取一次收盘快照，写入 `stock_fund_flow`。
- 筹码和业绩（`src/collectors/fundamentals.py`）：筹码分布先用 AKShare，失败时用本地日线按换手率衰减估算（至少 60 根日线）；业绩预告/快报由 `EarningsCache` 按报告期整批缓存。二者只在个股诊断时按需获取。
- 股东数据（`src/collectors/shareholders.py`）：东方财富 F10 `ShareholderResearch/PageAjax`（普通 HTTP，一次返回股东户数、十大股东、十大流通股东、机构持仓），`fetch_shareholders(code)` 成功缓存 12 小时、失败缓存 30 分钟（`reset_cache()` 清空），ETF/指数直接返回 None 不请求。只在个股诊断（`diagnosis.shareholders`，代码缺省关闭、示例配置开启）和问股 `shareholders` 工具里按需获取。
- 行情里的市盈率、市净率（`stock_daily.pe/pb`）来自腾讯字段 39/46 和东方财富 f9/f23，新浪没有；接口用 0 或 `-` 表示没有数据，入库为空。
- 成交量单位各数据源不一致（腾讯、新浪、历史日线为股，东方财富为手），成交额统一为元。需要量比时用成交额比（策略选股就是这样做的）。
- 按需补齐日线：`ensure_daily_history(code, db_path)`（`src/collectors/daily_history.py`）在本地近 150 天日线少于 60 根时联网下载，只写入缺失的交易日，失败的股票 30 分钟内不重试。个股详情、AI 诊断、问股工具、技术指标提醒都会调用它，相关测试要把它 monkeypatch 掉。
- 个股新闻与公告：`get_stock_news(code)`（`src/collectors/stock_news.py`，东方财富资讯搜索和公告接口，普通 HTTP），进程内缓存 30 分钟。公告标题命中关键词时标注风险，其中立案、退市风险警示等是严重风险。「没有新闻」不算数据源失败（`is_valid` 放行空列表），也不能用数据集级的 `allow_stale`，否则会拿到别的股票的缓存。
- 股票搜索：`StockSearch`（`src/services/stock_search.py`）用 `stock_info` 和最新一天行情建索引，按代码、名称、拼音首字母（`pypinyin`，多音字给出多种组合）匹配，进程内缓存 12 小时（索引里还没有个股时 1 分钟后重建）。股票列表由常驻服务启动时的 `refresh_etf_list_background()` 一并刷新（为空或超过 1 天才联网，有更新即 `StockSearch.reset()`），节假日新装也能搜到个股。索引名称和查询都先经 `normalize_name()`，所以「万科」「万 科」都能找到「万科A」。`pypinyin` 自带 PyInstaller hook，打包不用额外配置。
- 股票代码统一用 `src/utils/stock_code.py`（`bare_code`、`code_candidates`、`exchange_of`、`board_of`、`daily_limit_pct`），不要再手写代码前缀规则。北交所新代码以 92 开头，不是上交所。
- **股票名称规范化：** 深交所列表和腾讯行情的简称含空格和全角字母（如「万  科Ａ」）。写入名称一律用 `stock_code.normalize_name()`（NFKC 转半角并删除空白），行情、股票列表、涨停池、龙虎榜、日线入库都已处理；按名称匹配资讯用 `name_variants()`（去掉 XD/XR/DR/N/C 标记、ST 前缀、结尾的 A/B）。`init_db()` 会一次性规范化 `stock_info` 和 `watchlist` 的旧名称，`stock_daily` 的旧行不迁移，读取处兜底。
- 东方财富数据统一走 `em_client.get_em_client()`：这是一个 Playwright 单例，用来绕过 TLS 指纹检测，替代 AKShare 的 `ak.*_em()` 系列函数。不要直接调用 `ak.*_em()`。
- `browser_client.py` 每个线程持有一个 Playwright 实例，因为同步 Playwright 对象不能跨线程使用。`ths_client.py`（同花顺）在它之上加了重试、Cookie 回退和过期缓存回退。
- `BaseCollector.fetch_url()` 和 `post_url()` 带指数退避重试。`safe_collect()` 会吞掉所有异常并返回 `[]`，所以采集器出错只会体现在日志里。
- `CollectorOrchestrator.collect_all()` 要求所有新闻源（cailianshe、xueqiu、jiuyan、hot_topics、weibo、douyin、toutiao）以及行情、国际新闻、美股财报这几组都有数据。缺哪组就只重试哪组，最多 `desktop.collect_max_attempts` 次。
- **联网新闻搜索**（`src/collectors/news_search.py`）：博查、Tavily、SerpAPI、Brave、SearXNG 五种搜索服务，按 `search.providers` 的顺序回退；每个 provider 可配置多个 Key（或多个 SearXNG 地址），在自己的 Key 之间轮询；某个 Key 返回 401/403/429 时进入 10 分钟冷却（进程级），同一次调用里换下一个 Key。多 provider 之间用 `fetch_with_fallback("news_search", ...)` 回退，出错或无结果都换下一个。结果按（查询词、条数、天数）缓存 `search.cache_minutes` 分钟（进程级），只缓存非空结果。`api_keys` 可以是列表或逗号分隔字符串；环境变量覆盖时是字符串，需要 `_as_list()` 处理。`search.enabled` 为假时完全不请求，所以个股诊断的已有测试不受影响。测试前后调用 `news_search.reset_state()` 清空缓存和冷却。
- **资讯相关度**（`src/collectors/news_relevance.py`，纯函数）：`score_news()` 按代码/名称变体命中位置、公司事件词、权威来源打 0~100 分，分为 direct（直接相关，代码/名称信号 ≥ 38）、sector（行业相关）、macro（宏观市场），附最多 3 条依据；`junk_reason()` 识别股吧、问答、行情模板页、荐股广告、垃圾内容（官方/权威来源豁免，广告词只用「带你赚」「翻倍牛股」之类组合词，避免误伤正常标题）；`rank_news()` 去垃圾并按类别、分数排序。`search_stock_news(code, name, config, limit, sector_terms=())` 用它代替原来的名称硬过滤，结果的 `SearchResult.relevance` 带分级。
- **RSS 资讯源**（`src/collectors/rss.py`）：支持 RSS 2.0、Atom、RSS 1.0（用 xml.etree 和 beautifulsoup4 解析；含 `<!ENTITY` 的内容拒绝）。配置 `intelligence` 段（启用、间隔分钟数最少 5、每源最多条数、保留天数、订阅源 URL 列表）。入库 `finance_news`，`source="rss"`、`category` 为订阅源名称，按 URL（没有时按标题）在 `keep_days` 内去重。接入：定时任务 id="rss"（启用且有启用的源时注册）、`main.py --once` 的 collect 步骤、PyQt6 的 `CollectorOrchestrator`（可选组）。与交易日无关；舆情分析在近 24 小时最多 20 条 RSS 消息前标注 `[RSS·源名称]`。

### 数据库（`src/database/`）

- `db.py` 在模块级保存引擎和会话工厂单例，所以 `init_db(path)` 和 `get_db_session(path)` 只有第一次调用时传入的 `path` 生效。要切换数据库（比如测试里），先把 `db._engine` 和 `db._SessionFactory` 设为 `None`。
- `get_db_session()` 退出时自动提交，出异常时回滚。会话工厂是默认的 `expire_on_commit=True`，退出 `with` 后 ORM 对象已过期，再读属性会抛 `DetachedInstanceError`（首页交易焦点曾因此 500），要在会话内把需要的值取出来。
- 没有 Alembic。`init_db()` 执行 `create_all` 加 `_auto_migrate`，后者只会给模型里新增的列执行 `ALTER TABLE ... ADD COLUMN`。改列名、改类型、删列都要手动迁移。

### 配置（`src/config_loader.py`）

- `load_config()` 把 `settings.yaml` 深度合并到 `settings.yaml.example` 之上，所以新增配置项要在 example 文件里给默认值。
- `settings.yaml` 不存在时，`load_config()` 会回退到 example 文件。
- 结果按路径缓存，`reload_config()` 会清掉缓存。
- **环境变量覆盖：** `QUANT__LLM__PRIMARY__API_KEY=sk-xxx` 覆盖 `llm.primary.api_key`（层级用双下划线，键名转小写；原值是字符串的项取原文，其余按 YAML 解析；空值忽略）。只作用于 `settings.yaml`（`stock_pool.yaml` 也用 `load_config` 读取，不受影响）。写回文件时 `strip_env_overrides()` 会剔除这些值，保证 Secrets 不落盘。
- 运行时会重写 `settings.yaml` 的代码，都会丢掉文件里的注释：
  - 自学习（`save_config()`）写入完整的合并后配置。
  - `settings_store.save_section()`：Web 设置接口，以及 PyQt6 的 AI 设置、推送设置对话框，按段写回。
- **search 段配置：** `enabled`（启用联网新闻搜索）、`providers`（优先级列表）、`cache_minutes`（缓存时间）、各 provider 的 Key 配置。Web 设置接口 `/settings/search` 返回时 API Key 显示为 `******` 加后 4 位，保存时按后 4 位还原为原值（找不到对应原值的丢弃）；含 `your-` 的占位 Key 视为未配置。
- **分享图片**（`src/services/report_image.py`）：`build_share_html()` 纯函数生成 HTML，`render_png()` 用 Playwright 同步 API 每次启动 headless chromium 截图（2 倍清晰度）。结果按标题、内容和品牌参数缓存最近 20 张；渲染失败接口返回 503。测试不能真的启动浏览器，要 monkeypatch `render_png`。Docker 镜像加了 `fonts-noto-cjk`。
  - **品牌：** `notifier.image` 的 `brand`（顶部品牌名）、`footer`（底部文字，默认「仅供学习研究，不构成投资建议」）、`qr_url`（底部二维码，只接受 http/https，用 `qrcode[png]` 的纯 Python 工厂生成内嵌 data URI）由 `share_options(config)` 读取，推送分享图、诊断/复盘/仪表盘分享图接口都使用。文本一律 HTML 转义。
- **AI 输出语言**（`report.language: zh|en`，`src/services/report_language.py`）：`report_language(config)`、`language_directive(lang, enums)`（en 时追加英文输出指令，并要求代码判断用的枚举值保持原样，如 action、信心 高/中/低、观点 看多/中性/看空、姿态 进攻/均衡/防守）、`tr(lang, zh, en)`（护栏说明和报告标签）、`display(lang, value)`（枚举的英文显示名）。zh 输出与原来逐字相同。诊断、基金诊断、多智能体、策略会诊、大盘复盘、问股、深度研究都接入；诊断与复盘结果带 `language`，缓存只复用同语言的结果；日报只翻译标题和主要节标题。
- **配置校验**（`src/services/config_check.py`）：`check_config(config, raw, example)` 返回 `{ok, errors, warnings, issues}`。检查用户 `settings.yaml` 原文里 example 没有的键（`strategy.adaptive_weights`、`screening.strategies`、`llm.pricing`、`notifier.routes`、各搜索 provider 子键、列表元素等动态键除外）、与 example 默认值类型不符、`scheduler.*_time` 必须是两位小时的 `HH:MM`、`*_minutes`/`*interval` 必须大于 0（example 默认为 0 的项允许 0）、若干取值范围和枚举，以及语义问题（主模型没有可用 Key、启用的推送渠道不完整、对外监听未开登录、搜索启用但没有可用服务、机器人缺凭证、`trading.auto_confirm`）。单项检查出错降级为 warning。**新增配置项要先加到 example**，否则会被报成未知键。
- **自定义报告模板**（`src/services/report_templates.py`）：`render_report(name, data, fallback)` 在 `config/templates/{name}.md.j2`（`templates_dir()`，相对工作目录）存在时用 Jinja2 `ImmutableSandboxedEnvironment` 渲染（模板不能访问内部属性，也不能修改传入数据；变量 `default` 是内置格式全文），否则或出错/结果为空白时返回 fallback。name 只能是 diagnosis、watchlist、market_review、daily_report。接入点：机器人诊断和复盘回复、诊断 Markdown 下载与分享图、自选股仪表盘、大盘复盘 `result["markdown"]`、日报正文；PyQt6 界面显示不接入。内置示例模板在 `src/services/templates/`。

### 大盘复盘、AI 研判与策略选股

- `build_market_facts()`（`src/services/market_context.py`）把指数、涨跌家数、成交额、量化大盘环境、题材/行业主线、降温板块和财联社重要快讯汇总成 `MarketFacts`，供各 LLM 提示词共用。`news=False` 时不读快讯。
- 市场概况 `StockDataCollector.collect_market_overview()` 存在进程内缓存（首页读它）：今天没有行情时按最近一个有全市场行情的交易日统计涨跌家数和成交额，结果带 `trade_date`（没有统计时为空）。`collect_all()` 里在行情采集完成后才计算，定时行情采集后也刷新，`server.py` 运行定时任务时启动后后台先算一次。大盘环境比较成交额时也要求前一天有全市场样本。
- `MarketReviewService`（`src/services/market_review.py`）按趋势结构、资金情绪、主线板块复盘，输出次日姿态、仓位、关注与回避方向、观察要点。
  - **护栏：** 姿态不能比量化大盘环境更激进（冰点、防守最多给防守，均衡最多给均衡），被下调时仓位改用对应档位。
  - **存储：** 每个交易日一条（`market_review` 表），重新生成覆盖。已有收盘后生成的同语言复盘时直接复用，盘中生成的会在收盘后重新生成。`result["markdown"]` 经 `render_report("market_review", ...)`，有自定义模板时用模板。
  - **日报：** 日报只读取已生成的复盘，不会调用 LLM。
- `TradeAdvisor`（`src/services/trade_advisor.py`）给 Top 评分股写入 `ai_verdict`，观望和回避的信号在 `_is_buy_signal()` 中被拦下，不生成订单。评分没有选中的 Top 股票，只补建 `signal_type="hold"` 的参考记录，AI 不能把它们变成买入；AI 涨停预测（`premarket`）信号的研判也不会被覆盖。
- `StrategyScreener`（`src/strategy/screener.py`）是全市场策略选股：
  - **流程：** 最新交易日行情 → 股票池过滤（`stock_pool.yaml` 的名称关键词、黑名单、股价、流通市值；逐只查库的 `RiskManager._check_stock_pool()` 太慢，这里是批量实现）→ 成交额 3000 万以上的股票取近 60 个交易日日线计算特征 → 各策略规则打分。
  - **策略：** 内置 6 个策略，参数可在 `screening.strategies.<策略>` 覆盖；每个策略标注适配的大盘环境，适配的排在前面；同一只股票被多个策略选中时，取最高分并每多一个策略加 5 分。
  - **存储与次日表现：** 结果按「交易日 + 策略 + 代码」存入 `strategy_pick`，每行存各策略自己的分数；`performance()` 统计各策略选股的次日表现。`picks(trade_date, strategy)` 查某天（默认最近一天）的结果并可按策略筛选（`latest()` 等于 `picks()`），`history_dates(limit)` 给出最近有选股的交易日及入选数、次日表现，Web 选股页据此浏览历史。
  - **预测器接入：** 预测器只在盘前、盘后时段调用选股，因为盘中成交额不完整；策略选股归入「全市场」来源，这样自学习的来源统计不用改。
  - **策略权重：** 最近 30 天内有历史回测时，按回测得出的权重（0.8~1.2）乘到各策略得分上（`screening.adaptive_strategy_weights`）。
- `StrategyBacktester`（`src/strategy/strategy_backtest.py`）对区间内每个全市场交易日（当天行情不少于 1000 只）调用 `StrategyScreener.run(point_in_time=True)` 重新选股，结果存入 `strategy_backtest`。
  - **防止未来数据：** `point_in_time=True` 时策略权重不生效，大盘环境传 `overview={}`，因为 `MarketRegimeAnalyzer.analyze()` 不传 overview 时会读进程内今天的实时指数缓存。其他按日期取数的代码也要注意这一点。
  - **入场与统计：** 次日开盘入场，统计 1/3/5 日收益、次日涨停率、每日等权组合的累计收益和最大回撤。
  - **策略权重：** 样本不少于 30 个的策略按次日平均收益相对全体的差值调整权重。
- `DiagnosisOutcomeService`（`src/services/diagnosis_outcome.py`）对近 60 天的 AI 诊断做事后验证（只读）：以诊断行情日收盘价为基准统计之后 1/3/5 日涨跌，买入/加仓算看多、减仓/卖出/回避算看空，持有/观望不判方向；同一行情日多次诊断只算最后一次。
- 自选股：`WatchlistService`（`src/services/watchlist.py`，`watchlist` 表）负责增删和批量导入。批量导入的表格有「代码」列表头时只读取代码列，避免把数量、金额里的 6 位数字当成代码。
  - **ETF 和指数：** `resolve(text, include_funds=True)` 也识别 ETF 和指数，表里存规范代码（指数带前缀）；纯 6 位数字优先当个股（000001 是平安银行），加指数要输入带前缀的代码或名称（sh000001、上证指数）。`list()`/`overview()` 每行带 `kind`（stock/etf/index），基金行情取自 `fund_daily`、诊断取自 `FundDiagnosisService`；`contains()`/`remove()` 用 `diagnosis_code`。批量导入、图片导入、实盘记账传 `include_funds=False`。
  - **决策仪表盘：** `WatchlistReportService`（`src/services/watchlist_report.py`）并发诊断每只自选股（`watchlist.workers`），基金走基金诊断，仪表盘里标注「ETF」「指数」。收盘后复用当天收盘后的诊断，盘中复用 30 分钟内的（只复用同语言的）。结果汇总成仪表盘，存入 `watchlist_report` 并推送。
    - `watchlist.timeout_minutes`（0 不限，可为小数）：总时长上限，超时后未完成的记入 failed，`shutdown(wait=False, cancel_futures=True)` 不等卡住的线程，仍保存并推送已完成部分，返回 `timed_out`。
    - `watchlist.single_notify`：每只成功后立即推送一条简版（`render_item_brief()`），最后仍推汇总。
    - 邮件分组 `notifier.email.groups`（`[{name, stocks, to}]`，`notifier.email_groups()` 解析并规范化代码）：汇总照常 `broadcast`，每组再用 `notifier.send_email()` 收到只含本组股票的仪表盘（子集为空跳过），逐只推送时属于分组的股票也发组邮件。`send_email()` 只要求邮件渠道启用且有 SMTP 服务器，默认收件人可以为空；`EmailNotifier.send/send_image` 的 `to=` 覆盖收件人。
    - Web：`GET/PUT /settings/watchlist`（daily_report、max_stocks 1~500、workers 1~10、single_notify、timeout_minutes 0~600），自选股页「仪表盘设置」；推送设置的邮件表单可编辑分组。
  - **提醒与问股：** 盘中提醒和问股的 `watchlist` 工具都会读取自选股。
- AI 问股 `StockChatSession`（`src/services/stock_chat.py`）：
  - **协议：** 每个问题最多 3 轮工具调用。LLM 用 JSON 返回 `{"tool_calls": [...]}` 或 `{"answer": ...}`，不依赖各家模型的 function calling。
  - **工具：** 工具在 `src/services/chat_tools.py`，全部只读，不能下单；股票参数可以是名称或拼音，会先经 `StockSearch` 解析。新增 `web_search` 工具从博查、Tavily、SerpAPI、Brave、SearXNG 联网搜索资讯（需配置 `search.enabled` 和各源 API Key；参数带 `code` 且是个股时按资讯相关度过滤并标注类别）；`shareholders` 工具查股东数据。
  - **策略选择：** `perspective` 参数可传策略名称、中文名或别名；`PERSPECTIVES` 是模块导入时的策略快照。
  - **流式输出**（`POST /chat/sessions/{id}/ask/stream`）：SSE，每个事件一行 `data: JSON`，type 为 status、tool、tool_result、delta、error、done，done 总是最后一个；`StockChatSession.ask_stream()` 边接收边解析 JSON 里的 answer 字符串产出 delta；`POST /chat/sessions/{id}/cancel` 取消；前端可停止，流式失败回退任务轮询。模型没有 `chat_stream` 时退化为一次性回答。
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
  - **多 API Key 支持：** primary、backup、vision 各角色的 `api_key`支持列表或逗号/换行分隔字符串，按顺序轮询。单个 Key 返回 401/403/429 时进入 10 分钟冷却（进程级），同一次调用里换下一个 Key；冷却期内该 Key 跳过。设置接口返回掩码列表，保存时按后 4 位还原（找不到对应原值的丢弃）。
  - 导入 LiteLLM 一律用 `llm_usage.import_litellm()`：它让 LiteLLM 使用本地价格表，否则导入时会联网下载。
  - **用量统计：** 每次调用（含命中缓存和失败）由 `record_usage()` 写进缓存库的 `llm_usage` 表，记录 token 和估算费用（`llm.pricing` 可自定义单价）。功能按调用栈里第一个 `src.`/`api.` 模块归类，映射表是 `FEATURE_LABELS`，新增调用 LLM 的模块要加进去，否则显示为「其他」。`usage_summary()` 供 Web【AI 用量】页使用。
  - 主模型失败时切换到备用模型；备用模型的 Key 仍以 `your-` 开头时会被跳过。两者都没有可用 Key 时报错附带 `NO_MODEL_HINT`（提示去【设置 → AI 模型】填写），大盘复盘和 AI 问股遇到这种情况直接显示该说明。
  - **错误分类与参数恢复：** `classify_llm_error(exc)` 按异常类名、状态码和错误文本把失败分为 auth、quota、rate_limit、model_not_found、context_length、content_filter、unsupported_param、timeout、network、server、unknown，给出中文说明（`LLMErrorInfo`）。模型不支持 `response_format`/`temperature`/`max_tokens` 时，记入进程级 `_PARAM_FIXES`（按路由，`reset_key_state()` 一并清空），去掉该参数或改用 `max_completion_tokens` 后立即重试（不计重试次数、不等待，一次调用最多恢复 3 次）；auth、quota、model_not_found、context_length、content_filter 不在同一路由重试，直接切备用。`LLMClient.last_error` 记最近一次失败，全部失败时异常文案带分类说明；`/settings/llm/test` 失败返回 `kind`，发生参数调整时返回 `note`。
  - 响应缓存在 SQLite 里，缓存键是模型 + 提示词 + 参数的哈希，带 TTL。
  - `reload()` 可热切换模型平台。
  - `chat_json()` 解析失败时依次尝试：提取 markdown 代码块 → 修复截断的 `items` 列表 → 截到最后一个完整对象后交给 `json_repair`。截断的输出不能直接交给 `json_repair`，它会把半截的对象（如半截股票代码）当成有效数据保留下来。
  - **流式接口：** `LLMClient.chat_stream(user_message, system_message=...)` 逐块产出文本，主模型出错且还没产出文本时切备用模型；不走响应缓存。
  - **模型列表：** `llm_client.list_models(role_cfg)` 按平台请求模型列表接口（OpenAI 兼容的 /models、Anthropic、Gemini、Ollama /api/tags），设置页「获取模型列表」调用 `POST /settings/llm/models`。
  - **图片识别**（`chat_vision(prompt, images, ...)`）：按 `llm.vision` → 主模型 → 备用模型的顺序尝试，相同路由只试一次，不走响应缓存。`llm.vision` 留空时用主模型（需支持图片输入）。用于自选股图片导入（`src/services/image_import.py` 的 `extract_stocks()`，支持 PNG/JPEG/WebP/GIF，最大 5MB）；FEATURE_LABELS 记录为「图片识别」。
- 操作建议统一用 `src/analyzers/decision.py`：`normalize_action()` 把文本归一为 buy、add、hold、watch、reduce、sell、avoid、alert 八种。否定说法（「不建议买入」）识别为 avoid；多个关键词同时出现时，取最先出现的；无法识别时返回空字符串。下单只接受 `is_bullish()` 为真的建议。
- AI 预测保存前会经过 `_validate_predictions()`：剔除在 `stock_daily` 和 `stock_info` 里都查不到的代码（AI 编造的）、ST/退市股和重复代码，名称以数据库为准。行情库为空时无法校验，直接跳过。
- 个股诊断 `StockDiagnosisService`（`src/services/stock_diagnosis.py`）：
  - **流程：** 先用 `build_context()` 汇总市场阶段、行情、技术面、资金流、筹码、业绩、股东（开启时）、涨停记录、主线、大盘、资讯、龙虎榜和持仓，再由 LLM 输出决策仪表盘 JSON（含 `phase_decision` 和 `signal_attribution`）。
  - **资讯：** 本地资讯按 `name_variants(name)` 匹配；本地、东方财富个股新闻和联网结果都经 `junk_reason()` 过滤、`score_news()` 分级，【相关资讯】每行带 `[直接]`/`[行业]`/`[宏观]` 前缀并按此排序；另取所属主线和近期涨停题材词（`sector_terms`，最多 3 个）在本地资讯里找最多 3 条不含本股名称的行业背景。context 有 `news_relevance` 计数。基金诊断调用 `_web_search_lines(..., tagged=False)`，格式不变。
  - **市场阶段**（`src/services/market_phase.py`）：`current_phase(now)` 按交易日历判断 premarket（9:30 前）、intraday、lunch_break（11:30–13:00）、closing_auction（14:57–15:00）、postmarket、non_trading，并给出 `effective_daily_bar_date`（最近一根完整日线：盘后为今天，其余为上一交易日）。`phase_prompt_section()` 生成提示词里的【市场阶段】行；`phase_guardrails()` 规范化 LLM 的 `phase_decision`（操作窗口、立即行动、观察条件、下次检查、数据限制），盘前/非交易日把未被否定的「立即买入/卖出」类表述（en 下也识别 buy now 等）改为开盘后确认并把高信心降为中，盘中且行情是当天时注明 K 线未走完，行情日期早于最近完整交易日时信心降为中、买入降为观望。`build_context` 通过模块属性调用 `market_phase.current_phase()`（便于测试替换，见测试一节的 conftest）。
  - **信号归因**（`src/analyzers/attribution.py`）：`normalize_attribution()` 把技术面/资讯/基本面/大盘四项贡献度解析为 0~100 的数（「40%」也可），四项都有效时按最大余数法缩放为合计 100，结果为 `signal_attribution`。
  - **股东：** `diagnosis.shareholders` 开启时加【股东】段，户数环比下降 ≥10% 和机构新进写进利好、户数环比上升 ≥10% 写进风险（前缀「股东：」）；数据完整度权重不变。
  - **护栏：** `_apply_guardrails()` 负责动作归一化，缺失时按评分推断，价格计划用 `sanitize_price_plan()` 校验；判定本身在 `src/services/decision_profile.py` 的纯函数 `decide()`/`decide_with_phase()`（输入是可 JSON 序列化的快照，见下文决策风格）。均衡风格下以下情况的买入建议降为观望：
    - 评分低于 50；
    - 大盘冰点；
    - 行情或日线不足 20 根；
    - 数据完整度（行情、日线、技术面、资金流、筹码、大盘、资讯、业绩按权重计分）低于 60%；
    - 资金净流出超过成交额的 5%；
    - 与 3 天内上次诊断方向相反，但评分变化不足 15 分；
    - 近 30 天公告含严重风险（立案、退市风险警示等）；
    - 行情日期早于最近完整交易日（阶段护栏）。
  - **决策风格**（`diagnosis.decision_profile`，保守 conservative / 均衡 balanced / 进取 aggressive，`diagnose(..., profile=)` 可覆盖）：`PROFILE_RULES` 定义买入最低评分 65/50/45、最低数据完整度 75/60/50%、资金净流出降级阈值 3/5/8%；保守在大盘防守时不开新仓、信心低不买并追加「单只不超过 2 成」，保守和进取的买入必须有失效条件或止损价。冰点、核心数据不足、严重公告、行情过时、方向反复这些安全护栏不随风格放松，风格专属规则在它们之后判断。均衡与原来的常量和文案逐字一致。结果保存 `decision_profile` 和 `decision_inputs` 快照，缓存只复用同语言、同风格的结果；`reassess(diagnosis_id, profile)` 只用快照按其他风格重算（不调用模型、不联网、不读当前行情），旧记录没有快照时返回错误。诊断结果和 `latest()` 带 `diagnosis_id`。
  - **多智能体：** 由 `diagnosis.mode` 控制，见 `src/services/diagnosis_agents.py`。
    - `standard` 和 `full` 模式下，分析员只拿到按「【标题】」拆出的部分上下文，并发调用；决策员沿用原来的提示词，外加 `DECISION_ADDENDUM`。
    - 分析员观点冲突（看多和看空并存，或评分相差 25 分以上）时，信心最高为「中」。
    - 代码里的默认值是 `single`，示例配置里是 `standard`，所以测试用的最小配置只调用一次 LLM。
  - **历史校准：** 由 `diagnosis.calibration` 控制。`diagnosis_outcome.calibration_stats()` 汇总近 90 天诊断的事后准确率，进程内缓存 30 分钟，写进提示词；看多诊断的 3 日准确率低于 45%（至少 10 次）时，买入信心下调一档。
  - **测试：** 诊断前会补齐日线，并获取筹码、业绩、个股新闻和公告，这些都会联网。测试要 monkeypatch `fundamentals.fetch_chip_summary`、`EarningsCache.get`、`stock_news.get_stock_news` 和 `daily_history.ensure_daily_history`（参考 `tests/test_stock_diagnosis.py` 的 fixture）。股东数据只在配置 `diagnosis.shareholders` 为真时获取（要测时 monkeypatch `shareholders.fetch_shareholders`）；本地资讯只取近 3 天，测试数据的 `collected_at` 要用当前时间附近。
  - **缓存：** 结果存入 `stock_diagnosis` 表，30 分钟内复用（同语言、同风格）。
  - **多策略会诊**（`src/services/skill_consult.py`，表 `skill_opinion`）：个股诊断时可选择多个策略（`diagnosis.skill_consult`，示例配置 `enabled: true`、`max_skills: 2`；代码缺省关闭）并发咨询各策略给观点后汇总；结果含 `skill_opinions` 和 `skill_consensus`；5 个交易日后评估命中，样本≥20 后按命中率调整权重到 0.8~1.2；决策信号页「策略表现」（`GET /chat/skills/performance`）查看；每次诊断多约 `max_skills` 次模型调用。
  - **运行记录**（`src/services/run_log.py`，存 `stock_diagnosis.run_log`）：记录各数据步骤的结果与耗时、模型调用（名称与耗时，不含 token）和护栏调整；`build_context(code, run_log=None)` 通过参数传入记录器，不能存在服务实例上（自选股诊断多线程共用一个实例）。诊断历史详情可查看；`GET /stocks/{code}/diagnosis-trend` 提供评分与收盘价趋势。
  - **历史查询：** `DataQueryService.list_diagnoses(code, action, days, limit, offset)` 按股票、操作建议、天数筛选诊断记录；`get_diagnosis(id)` 获取单条；`delete_diagnosis(id)` 删除。诊断历史 Web 页面支持下载 Markdown 或分享图（`/stocks/diagnoses/{id}/markdown`、`/stocks/diagnoses/{id}/image`）。
  - **注入假 LLM：** 测试通过构造函数的 `llm=` 参数注入（`MarketReviewService` 也一样）。
- `LimitUpPredictor`（`src/services/premarket_predictor.py`）按时段选择提示词，时段为 `premarket`（9:25 前）、`morning`、`noon`、`afternoon`、`aftermarket`（15:00 后）。非交易日一律按 `premarket` 处理。预测写入 `TradeSignal` 时，`signal_date` 存的是目标交易日：交易日盘前和盘中是当天，盘后和非交易日是下一个交易日。
- **决策信号**（`src/services/decision_signals.py`，表 `decision_signal`）：AI 诊断的 buy/add/reduce/sell/avoid 建议转为决策信号，带观察期（1~20 日，默认 5）和失效条件。相反建议使旧信号失效（invalidated），同方向的旧信号被替代（replaced）；收盘后定时任务 `signal_lifecycle`（默认 16:25）评估 1/3/5 日收益、最大不利/有利波动、止损止盈、过期；单票历史复盘（样本≥3）进诊断提示词；Web「决策信号」页（`/signals`）可查看和反馈；API `/signals` 系列。信号带 `profile`（决策风格，旧数据为空，按 balanced 处理），失效和替代只在同代码同风格之间发生；`save_reassessed(diagnosis_id, profile)` 把重新评估结果保存为该风格的信号（返回 created/existing/skipped，旧记录 error）；`list()`/`stats()` 支持 `profile`（`unknown` 表示旧数据）。
- **深度研究**（`src/services/research.py`，表 `research_report`）：按选定议题自动拆解问题 → 联网搜索 + 本地新闻/行情/技术面/主线/大盘取证（证据编号 E1…，每条≤400 字、总计≤12000 字）→ LLM 生成带引用的报告。Web「深度研究」页（`/research`），API `/research` 系列，机器人命令「研究」（别名：深度研究、research）。

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
  - **多账户：** `real_account` 表记账户（名称唯一），四张实盘表都有 `account` 列，空值即默认账户「默认」（`DEFAULT_ACCOUNT`）。`RealPortfolioService(config, account=None)`：None 时只读方法汇总全部账户（同一代码合并：数量相加、成本加权，每行带 `accounts`），写方法写入默认账户；指定账户时成交重放、资金锚点、止损计划、公司行为都只看该账户。非默认账户的导入去重键加「账户|」前缀（默认账户保持原格式）。`accounts()`、`add_account()`、`rename_account()`（同步四张表）、`delete_account()`（默认账户和有记录的账户不能删）。`real_position_plan` 唯一索引是 (account, code)，旧库由 `db._migrate_real_plan_index()` 迁移。盘中提醒、个股诊断、问股、机器人用全部账户汇总。
  - **成交流水：** 存在 `real_trade` 表，手动录入或导入交割单（`parse_trade_rows()` 按常见列名识别）。导入用 `import_key` 去重：有成交编号时按编号，否则按日期+时间+代码+方向+价格+数量+序号。
  - **公司行为**（`real_corporate_action` 表）：分红（现金到账摊薄成本）、送转股（只增加数量）、红利税补缴（计入成本）。同一天先处理公司行为再处理成交；可按每 10 股方案（`add_corporate_action_by_plan`，按除权日前持仓计算）或按到账金额逐笔录入。交割单导入识别「红利入账」「红股入账」「红利税补缴」行并转换为公司行为记录（`parse_import_rows`，`parse_trade_rows` 三元组签名不变）。Web 页面导入先预览后确认（`POST /real/trades/import?preview=true`）。API 支持 `GET/POST/DELETE /real/actions`。**已知限制：** 公司行为（dividend/tax）暂不进 `PortfolioRiskService` 的 fills 重放。
  - **持仓：** 按移动平均成本计算，费用计入成本。
  - **可用资金：** 用 `real_cash` 表记录的锚点，加上锚点之后的成交推算。
  - **止损止盈：** `real_position_plan` 可以逐只覆盖止损价、目标价，没设置时按风控比例从成本计算。
  - **使用方：** 实盘持仓（`account="real"`）会进入盘中提醒、组合风险、个股诊断和问股。
  - **接口：** `/real` 系列都有 `account` 参数（读为空表示全部账户，写为空表示默认账户），`GET/POST /real/accounts`、`PUT/DELETE /real/accounts/{name}`、`GET /real/risk?account=`；Web 实盘页的账户选择保存在 localStorage `quant-real-account`，只有默认账户时界面与原来一致。
- 组合风险 `PortfolioRiskService`（`src/services/portfolio_risk.py`，`account="paper"`/`"real"`（全部实盘账户）/`"real:<账户名>"`）只读计算以下几项：
  - 总仓位与大盘环境建议仓位（`risk.market_regime_position`）的比较；
  - 个股和行业集中度，行业取最近一次涨停时的所属行业；
  - 距止损价的距离；
  - 回撤：按 `trade_fill` 和每日收盘价重放账户净值（模拟盘没有手续费），得出最大回撤和当前回撤。

### 推送（`src/notifier/`、`src/services/daily_report.py`）

- 统一通过 `notifier.broadcast(config, title, content, kind=)` 推送。
  - **消息类型：** `kind` 取值为 `daily_report`、`alert`、`watchlist`、`chat`、`system_error`；按 `notifier.routes` 选择渠道；没配置路由的类型推送到全部已启用渠道。系统错误（定时任务出错时 `report_error`）同一来源冷却 `notifier.system_error.cooldown_minutes`（默认 60 分钟）。
  - **渠道条件：** `enabled_channels(config, kind)` 只认启用且配置完整的渠道。Webhook 还是示例占位符（含 `your-`）不算完整；邮件至少要有 SMTP 服务器和收件人。
  - **新增推送：** 新增推送时要传 `kind`，测试里替换 `broadcast`、`enabled_channels` 时要接受 `kind` 参数。
  - **QUANT_NO_NOTIFY：** 环境变量 `QUANT_NO_NOTIFY` 非空时，`broadcast()` 不执行推送（用于测试或调试）。
- **渠道列表：** 企业微信、钉钉、飞书、邮件、Telegram、Discord、Slack、PushPlus、Server酱、ntfy、Gotify、Pushover、Bark、自定义 Webhook。
  - **CHANNEL_FIELDS：** 新增的 10 个渠道（Telegram、Discord 等）用 `CHANNEL_FIELDS` 字典描述配置字段（如 bot_token、webhook_url、api_key），Web 设置页通用渲染。密钥字段（`secret` 标记）在 `/settings/notifier` 返回时整体替换为 `******`，保存时掩码原样回传会保留原值。大模型 Key 和联网搜索 Key 显示为 `******` 加后 4 位。旧的企业微信、钉钉、飞书、邮件有专用表单。
  - 各渠道 `send()` 用 `split_by_bytes()` 按字节上限拆分消息并逐条发送，上限见各模块 `MAX_CONTENT_BYTES`。
  - 飞书签名特殊：用「时间戳\n密钥」作为 HMAC 密钥，对空消息签名。
  - `diagnose()` 检查配置，`test_channel()` 发送测试消息（导入测试文件时要改名，否则 pytest 会把它当成测试）。
- `DailyReportService.push()` 在没有任何渠道启用时直接返回，不会生成报告，也不会访问网络。
- **盘中提醒规则**（`src/services/alert_service.py`）：
  - **监控范围：** 今日信号股、模拟盘持仓和 `alerts.watchlist`。
  - **内置提醒：** 封涨停、炸板、跌破止损（紧急）、接近止损、达到目标价、大跌，以及大盘环境比前一交易日降档或评分明显下滑。大盘两天都按 `overview={}` 计算，保证口径一致。
  - **自定义规则：** Web 页面可直观编辑，支持启用开关和备注。`validate_rule()` 校验规则格式；`RULE_TYPES` 定义支持的规则类型（价格、涨跌幅、放量、均线、MACD、KDJ、RSI）。保存前逐条校验，指标用 `src/analyzers/indicators.py` 计算，当天的数据就是最新价。本地日线不足时会先自动补齐。
  - **规则试算：** `AlertService.test_rule()` 不受交易时段和冷却限制，试算结果不写记录不推送，用于 Web 页面「立即试算」功能；当天无行情时用最近交易日。
  - **每天一次：** 指标交叉、接近止损、大盘转弱在 `DAILY_ONCE_TYPES` 中，每天只提醒一次。
  - **降噪：** `NoiseFilter`（`src/notifier/noise.py`）负责冷却期去重和免打扰时段（`notifier.quiet_hours`），紧急提醒不受免打扰限制。同一次检查的多条提醒合并成一条推送，所有提醒都记入 `alert_record` 表。
  - **日报与分享图：** `alerts.daily_digest` 和 `digest_time` 启用盘中提醒日报（定时任务 `alert_digest`），统计当天全部提醒；`notifier.image` 配置把日报和自选股仪表盘渲染成分享图推送（支持企业微信、Telegram、邮件、Discord、ntfy，失败回退文字）；`min_severity` 低于该级别的提醒记录但不推送。
  - **状态：** 涨停状态和冷却记录是进程级状态，测试前后要调用 `alert_service.reset_state()`。
  - **交易时段检查：** `AlertService` 只在交易时段运行（`trading_calendar.in_trade_session()`）。

### 部署与 CI（`docker/`、`.github/workflows/`）

- Docker：多阶段构建（先构建前端），以 UID 1000 的 `quant` 用户运行，默认 `python server.py --host 0.0.0.0`。默认配置放在镜像的 `/app/defaults/config`，`entrypoint.sh` 启动时复制进挂载的 `/app/config`（示例配置覆盖、股票池缺失才复制），并修复挂载目录属主。compose 用环境变量开启 Web 登录，因为容器外的请求不算本机。因为 `COPY src/` 会自然包含 `src/services/skills`，所以 Docker 镜像内置有策略技能。
- **桌面端自动更新：** 使用 electron-updater，检查 GitHub Release（hongheshan-svg/quant_tools）；启动时检查（可在设置禁用，偏好文件 `desktop-prefs.json`），菜单「帮助 → 检查更新」手动触发；Windows 下载后重启安装，macOS 未签名只提示前往下载。发布工作流需上传 `latest*.yml` 和 `*.blockmap`（旧版本发布没有这些文件时检查不到更新）。
- 工作流：
  - `ci.yml`（后端测试、前端 lint/测试/构建、桌面端测试）。
  - `daily-analysis.yml`（工作日 16:40 `main.py --once`）：需要仓库变量 `ENABLE_DAILY_ANALYSIS=true`。配置来自 Secret `SETTINGS_YAML` 或按段映射为 `QUANT__` 环境变量的单独 Secret（LLM、各推送渠道、搜索源都支持）。支持 workflow_dispatch 新增 `stocks` 输入诊断指定股票、`no_notify` 不推送。数据库用 actions/cache 保留到下一次运行。
  - `network-smoke.yml`（`check_sources.py`，`ENABLE_NETWORK_SMOKE=true`）。
  - `docker-publish.yml`（`v*` 标签发布 GHCR 镜像）。
  - `desktop-release.yml`（`v*` 标签打包桌面端并上传 Release，需要 electron-builder 生成 latest*.yml 和 *.blockmap）。
- Actions 里给布尔配置映射 Secret 时要写成 `${{ secrets.X != '' && 'true' || '' }}`：直接写比较表达式在 Secret 为空时得到字符串 `false`，会覆盖 `SETTINGS_YAML` 里的设置；空字符串才会被忽略。

## 约定

- 文档字符串、注释和日志信息用中文（辅助文档采用中文）。
- 提交信息遵循 Conventional Commits 前缀（`feat:`、`fix:`、`refactor:`、`test:`、`docs:`、`chore:`），一个提交只做一件事。

每次修改提交git
