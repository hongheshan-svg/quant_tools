# 仓库指南

## 项目结构与模块组织
代码位于 `src/`，按功能分包：
- 数据流主线：`collectors/`（数据采集：基类、新闻搜索、RSS 订阅）→ `analyzers/`（LLM 分析）→ `strategy/`（评分与风控）→ `trading/`（订单执行）。
- 编排：`services/` 串联流水线、自学习、涨停预测、诊断、决策信号、深度研究等功能。
- 存储：`database/` 存放 SQLAlchemy 模型与 SQLite 会话管理。
- 界面：`desktop/` 是 PyQt6 桌面端（旧版）；Web 界面见下方 `api/` 和 `apps/web/`。
- 其他：`bot/`（钉钉、飞书、Discord 聊天机器人）、`notifier/`（14 个推送渠道与降噪）、`backtest/`（回测）。

仓库根目录的其他部分：
- `api/`：FastAPI 接口（`/api/v1`）、登录、后台任务，并托管前端构建产物。
- `apps/web/`：Web 前端（React + TypeScript + Vite + Tailwind）；`apps/desktop/`：Electron 桌面端。
- `docker/`：Dockerfile 和 docker-compose；`.github/workflows/`：CI、定时分析、数据源检查、镜像和安装包发布。
- `tests/` 测试，`scripts/` 独立脚本（历史数据回补、数据源检查、打包），`config/` 配置。`main.py`、`server.py`、`run_*.py` 是入口脚本。

新模块放在所属功能的包里。新的界面功能要同时提供 API（`api/v1/`）和 Web 页面（`apps/web/src/pages/`）。

## 构建、测试与开发命令
- `pip install -r requirements.txt`：安装依赖。
- `playwright install chromium`：安装无头浏览器；东方财富、同花顺和社交平台采集器都依赖它。
- `cp config/settings.yaml.example config/settings.yaml`：生成本地配置，然后填入 LLM API Key。
- `cd apps/web && npm ci && npm run build`：构建前端（Node.js 22+），`server.py` 托管构建产物。
- `python server.py`：启动 Web 界面和 API（默认 http://127.0.0.1:8000），同时运行定时任务和聊天机器人；`--no-scheduler` 只提供接口。
- `python main.py`：启动无界面的 APScheduler 定时任务。
  - `--once [--steps collect,analysis]`：执行一遍收盘后任务后退出；仅执行部分步骤。
  - `--stocks 600519,000858`：只诊断指定股票并推送决策仪表盘（跳过采集、分析等步骤）。
  - `--no-notify`：运行任务但不推送消息。
  - `--check-notify`：检查推送配置后退出（退出码 0 为可用，1 为失败）。
  - `--check-config`：检查 settings.yaml 的配置项后退出（退出码 0 为通过，1 为失败）；检查内容包括未知键、类型匹配、取值范围、时间格式和语义合法性。
  - `--debug`：打印详细日志。
- `cd apps/web && npm run dev`：前端开发服务器（:5173，接口代理到 8000）。`cd apps/desktop && npm run dev`：Electron 开发模式。
- `python run_dashboard.py`：启动 PyQt6 桌面端（旧版）。加 `--headless` 只执行一次完整流程，不打开界面。
- `python scripts/check_sources.py`：联网检查各数据源是否可用（不写数据库）。
- `pip install pytest`：安装测试框架；requirements.txt 里没有它。
- `python -m pytest -q -p no:cacheprovider tests/`：以安静模式运行全部测试。
- `python -m pytest -q tests/test_ths_client.py::test_request_json_cookie_fallback`：运行单个测试。
- `cd apps/web && npm run lint && npm test`：前端检查和测试；`cd apps/desktop && npm test`：桌面端测试。
- `python scripts/build_desktop.py`：打包 Electron 桌面端安装包（前端 → PyInstaller 后台服务 → electron-builder）。
- `powershell .\scripts\build_exe.ps1`：打包旧版 PyQt6 EXE。需要 `AStockQuantQt6.spec`，该文件不在仓库中（`*.spec` 被 git 忽略）。

## 代码风格与命名规范
Python 使用 4 空格缩进，遵循 PEP 8。公共函数和服务边界优先写类型注解。命名：函数和变量用 `snake_case`，类用 `PascalCase`，常量用 `UPPER_SNAKE_CASE`。
前端用 TypeScript 严格模式和 ESLint（`npm run lint`），2 空格缩进、不加分号；组件用 `PascalCase`，新增接口时同时修改 `src/api/endpoints.ts` 和 `src/api/types.ts`。

## 测试规范
- **命名与位置：** 使用 `pytest`。测试文件命名为 `test_*.py`，且必须放在 `tests/` 下，因为 `.gitignore` 会忽略其他位置的 `test_*.py`。测试函数名应描述行为（例如 `test_collect_all_retry_until_required_sources_ready`）。
- **运行位置：** 在仓库根目录运行测试，因为测试使用了 `config/settings.yaml` 等相对路径。
- **不访问网络：** 测试不要真实访问网络，用 `monkeypatch` 替换采集器和数据客户端。
- **不导入 Qt：** CI 机器缺少 libEGL，导入 PyQt6 会报错；需要测试的逻辑放在不依赖 Qt 的模块里。
- **前端与桌面端：** 前端用 Vitest + Testing Library（`apps/web/src/__tests__/`），桌面端用 `node --test`（`apps/desktop/tests/`，替换 `electron` 模块，不需要安装 Electron）。
- **数据库：** 数据库引擎是模块级单例。涉及数据库的测试要先重置单例（参考 `tests/test_self_learning.py` 里的 `_reset_db_engine()`），再在 `tmp_path` 下创建临时库。
- **市场阶段：** `tests/conftest.py` 自动把 `market_phase.current_phase` 固定为盘后（不涉及当前时间，`effective_daily_bar_date=None`），真实获取时间的函数是 `market_phase._real_current_phase`。涉及诊断、复盘等时间敏感功能的测试，可以通过 monkeypatch 该函数覆盖时段。
- **报告模板：** 测试自定义 Jinja2 模板时，要把模板目录 monkeypatch 到临时目录，不能写到 `config/templates/`。
- **配置校验：** 新增配置项时必须先在 `config/settings.yaml.example` 里给出脱敏后的默认值，否则配置校验会报「未知键」。
- **回归测试：** 修复 bug 时要补充回归测试。

## 提交与 Pull Request 规范
遵循本仓库使用的 Conventional Commits 风格：`feat:`、`fix:`、`refactor:`、`test:`、`docs:`、`chore:`。提交要范围明确、保持原子性（一个提交只做一件事）。PR 应包含：目的、主要改动、测试证据（命令 + 结果）、关联 issue；涉及 `apps/web/`、`apps/desktop/` 或 `src/desktop/` 的界面改动需附截图。

## 安全与配置建议
- **密钥：** 不要提交密钥。API Key、Webhook 等放在 `config/settings.yaml` 或 `.env`（都已被 git 忽略），也可以用 `QUANT__` 开头的环境变量覆盖任意配置（如 `QUANT__LLM__PRIMARY__API_KEY`），这些值不会被写回文件。
- **新增配置项：** 在 `config/settings.yaml.example` 里给出脱敏后的默认值，因为加载配置时会先读 example 作为默认值，再用本地配置覆盖。
- **表结构变更：** 项目没有 Alembic 迁移脚本。表结构由 `src/database/models.py` 定义，启动时 `init_db()` 只会自动补上新增的列。尽量避免改列名、改类型、删列这类破坏性变更；确实需要时要手动迁移已有数据库。

## 代码风格
辅助文档采用中文便于理解代码。
