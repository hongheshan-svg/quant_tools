# 仓库指南

## 项目结构与模块组织
代码位于 `src/`，按功能分包：
- 数据流主线：`collectors/`（数据采集）→ `analyzers/`（LLM 分析）→ `strategy/`（评分与风控）→ `trading/`（订单执行）。
- 编排：`services/` 串联流水线、自学习和涨停预测。
- 存储：`database/` 存放 SQLAlchemy 模型与 SQLite 会话管理。
- 界面：`desktop/` 是 PyQt6 桌面端；`dashboard/` 是 FastAPI Web 仪表盘，页面为单个 Jinja 模板。
- 其他：`notifier/`（企业微信、钉钉推送）、`backtest/`（回测）。

其余目录：测试在 `tests/`，独立脚本（如历史数据回补、打包）在 `scripts/`，配置在 `config/`。仓库根目录的 `main.py`、`run_*.py` 是入口脚本。新模块放在所属功能的包里。

## 构建、测试与开发命令
- `pip install -r requirements.txt`：安装依赖。
- `playwright install chromium`：安装无头浏览器；东方财富、同花顺和社交平台采集器都依赖它。
- `cp config/settings.yaml.example config/settings.yaml`：生成本地配置，然后填入 LLM API Key。
- `python run_dashboard.py`：启动 PyQt6 桌面端（主界面）。加 `--headless` 只执行一次完整流程，不打开界面。
- `python main.py`：启动无界面的 APScheduler 定时任务。
- `uvicorn src.dashboard.app:app --port 8000`：启动 Web 仪表盘，没有入口脚本会自动启动它。
- `pip install pytest`：安装测试框架；requirements.txt 里没有它。
- `python -m pytest -q -p no:cacheprovider tests/`：以安静模式运行全部测试。
- `python -m pytest -q tests/test_ths_client.py::test_request_json_cookie_fallback`：运行单个测试。
- `powershell .\scripts\build_exe.ps1`：打包 Windows EXE。需要 `AStockQuantQt6.spec`，该文件不在仓库中（`*.spec` 被 git 忽略）。

## 代码风格与命名规范
Python 使用 4 空格缩进，遵循 PEP 8。公共函数和服务边界优先写类型注解。命名：函数和变量用 `snake_case`，类用 `PascalCase`，常量用 `UPPER_SNAKE_CASE`。

## 测试规范
- **命名与位置：** 使用 `pytest`。测试文件命名为 `test_*.py`，且必须放在 `tests/` 下，因为 `.gitignore` 会忽略其他位置的 `test_*.py`。测试函数名应描述行为（例如 `test_collect_all_retry_until_required_sources_ready`）。
- **运行位置：** 在仓库根目录运行测试，因为测试使用了 `config/settings.yaml` 等相对路径。
- **不访问网络：** 测试不要真实访问网络，用 `monkeypatch` 替换采集器和数据客户端。
- **数据库：** 数据库引擎是模块级单例。涉及数据库的测试要先重置单例（参考 `tests/test_self_learning.py` 里的 `_reset_db_engine()`），再在 `tmp_path` 下创建临时库。
- **回归测试：** 修复 bug 时要补充回归测试。

## 提交与 Pull Request 规范
遵循本仓库使用的 Conventional Commits 风格：`feat:`、`fix:`、`refactor:`、`test:`、`docs:`、`chore:`。提交要范围明确、保持原子性（一个提交只做一件事）。PR 应包含：目的、主要改动、测试证据（命令 + 结果）、关联 issue；涉及 `src/desktop/` 或 `src/dashboard/` 的界面改动需附截图。

## 安全与配置建议
- **密钥：** 不要提交密钥。API Key、Webhook 等放在 `config/settings.yaml`（已被 git 忽略）。
- **新增配置项：** 在 `config/settings.yaml.example` 里给出脱敏后的默认值，因为加载配置时会先读 example 作为默认值，再用本地配置覆盖。
- **表结构变更：** 项目没有 Alembic 迁移脚本。表结构由 `src/database/models.py` 定义，启动时 `init_db()` 只会自动补上新增的列。尽量避免改列名、改类型、删列这类破坏性变更；确实需要时要手动迁移已有数据库。

## 代码风格
辅助文档采用中文便于理解代码。
