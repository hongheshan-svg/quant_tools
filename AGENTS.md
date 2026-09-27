# 仓库指南

## 项目结构与模块组织
后端核心代码位于 `src/`，测试位于 `tests/`。数据库迁移由 `alembic/` 管理，版本脚本在 `alembic/versions/`。基础设施相关代码和部署辅助工具在 `infra/`。前端应用代码在 `frontend/` 下（UI 模块通常在 `frontend/src/`）。新模块应放在所属功能附近，测试目录也按相同结构组织。

## 构建、测试与开发命令
- `python -m pytest -q -p no:cacheprovider`：以安静模式运行后端测试。
- `alembic upgrade head`：在本地应用全部数据库迁移。
- `alembic revision --autogenerate -m "add_xxx"`：根据模型变更生成迁移脚本。
- `cd frontend && npm install`：安装前端依赖。
- `cd frontend && npm run dev`：启动前端开发服务器。
- `cd frontend && npm run build`：构建前端生产资源。

## 代码风格与命名规范
Python 使用 4 空格缩进，遵循 PEP 8。公共函数和服务边界优先写类型注解。Python 命名：函数和变量用 `snake_case`，类用 `PascalCase`，常量用 `UPPER_SNAKE_CASE`。前端代码中变量和函数用 `camelCase`，组件文件用 `PascalCase`（例如 `TradePanel.tsx`）。

## 测试规范
后端测试使用 `pytest`。测试文件命名为 `test_*.py`，测试函数名应描述行为（例如 `test_rebalance_handles_empty_positions`）。修复 bug 时要补充回归测试；表结构行为变化时要包含迁移测试。

## 提交与 Pull Request 规范
遵循本仓库使用的 Conventional Commits 风格：`feat:`、`fix:`、`refactor:`、`test:`、`docs:`、`chore:`。提交要范围明确、保持原子性（一个提交只做一件事）。PR 应包含：目的、主要改动、测试证据（命令 + 结果）、关联 issue；涉及 `frontend/` 的 UI 改动需附截图。

## 安全与配置建议
不要提交密钥。使用本地环境文件（如 `.env`），并在文档中提供脱敏后的示例。合并前审查迁移脚本，避免破坏性的表结构变更。

## 代码风格
辅助文档采用中文便于理解代码。
