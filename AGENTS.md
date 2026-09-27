# Repository Guidelines

## Project Structure & Module Organization
Core backend code lives in `src/`, with tests in `tests/`. Database migrations are managed in `alembic/` and version scripts in `alembic/versions/`. Infrastructure-related code and deployment helpers are in `infra/`. Frontend application code is under `frontend/` (typically `frontend/src/` for UI modules). Keep new modules close to the feature they belong to, and mirror that layout in tests.

## Build, Test, and Development Commands
- `python -m pytest -q -p no:cacheprovider`: Run backend tests in quiet mode.
- `alembic upgrade head`: Apply all database migrations locally.
- `alembic revision --autogenerate -m "add_xxx"`: Create a migration from model changes.
- `cd frontend && npm install`: Install frontend dependencies.
- `cd frontend && npm run dev`: Start frontend development server.
- `cd frontend && npm run build`: Build frontend production assets.

## Coding Style & Naming Conventions
Use 4-space indentation for Python and follow PEP 8. Prefer type hints for public functions and service boundaries. Python names: `snake_case` for functions/variables, `PascalCase` for classes, and `UPPER_SNAKE_CASE` for constants. Frontend code should use `camelCase` for variables/functions and `PascalCase` for component files (for example, `TradePanel.tsx`).

## Testing Guidelines
Use `pytest` for backend tests. Name files `test_*.py` and keep test names behavior-focused (for example, `test_rebalance_handles_empty_positions`). Add regression tests for bug fixes and include migration tests when schema behavior changes.

## Commit & Pull Request Guidelines
Follow Conventional Commits style used in this repository: `feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`. Keep commits scoped and atomic (one logical change per commit). PRs should include: purpose, key changes, test evidence (command + result), related issue, and screenshots for UI updates in `frontend/`.

## Security & Configuration Tips
Do not commit secrets. Use local environment files (such as `.env`) and provide sanitized examples in docs. Review migration scripts before merge to avoid destructive schema changes.

## 代码风格
辅助文档采用中文便于理解代码。
