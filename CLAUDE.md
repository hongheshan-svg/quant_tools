# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A股舆情驱动量化交易系统 — an AI-powered quantitative trading system for Chinese A-shares. It collects multi-source sentiment data (news, social hot lists, market data), analyzes it with an LLM, scores limit-up (涨停) stocks with weighted factors, predicts next-session limit-ups, and can turn signals into paper-trade orders. It was developed on Windows, so the entry scripts force UTF-8 stdout and the build tooling is PowerShell.

## Setup

```bash
pip install -r requirements.txt
playwright install chromium   # required: EastMoney / THS / social collectors drive headless Chromium
cp config/settings.yaml.example config/settings.yaml   # then fill in LLM API keys
```

The SQLite DB is created automatically at `data/quant.db`, and the LLM response cache at `data/llm_cache.sqlite3`. `data/`, `logs/` and `config/settings.yaml` are git-ignored.

## Running

```bash
python main.py                              # Headless APScheduler loop (no UI)
python run_dashboard.py                     # PyQt6 desktop app — the main UI
python run_dashboard.py --headless          # Run PipelineService.run_full() once, no UI
python run_dashboard.py --warmup-before-ui  # Run full pipeline, then open the UI
python run_full.py                          # One-shot on already-collected data: LLM sentiment → topics → global impact → scoring → Top 10
python run_score.py                         # Scoring only, on existing data
python run_demo.py                          # Demo collection + display
python scripts/fetch_history.py --mode daily --start-date 2024-01-01   # History backfill; modes all/daily/limit_up/dragon_tiger; resumes unless --force-full
```

No entry script starts the FastAPI web dashboard (`src/dashboard/app.py`). Run it with `uvicorn src.dashboard.app:app --port 8000`.

## Tests

Most tests use pytest fixtures (`monkeypatch`, `tmp_path`), but pytest is not listed in requirements.txt:

```bash
pip install pytest
python -m pytest -q tests/
python -m pytest -q tests/test_ths_client.py::test_request_json_cookie_fallback   # single test
```

- Run tests from the repo root. They use relative paths (`config/settings.yaml`, `data/test_quant.db`), and `test_fetch_history_daily_fallback.py` imports `scripts.fetch_history`.
- Only `test_basic.py` and `test_self_learning.py` also run as plain scripts (`python tests/test_basic.py`).
- `.gitignore` ignores `test_*.py` everywhere except `tests/`, so new tests must go in `tests/`.
- A test that touches the DB must reset the engine singleton first (copy `_reset_db_engine()` from `tests/test_self_learning.py`).

## Build (Windows EXE)

Run `powershell .\scripts\build_exe.ps1`. It runs PyInstaller with `.venv\Scripts\python.exe` and `AStockQuantQt6.spec`, which is not in the repo because `*.spec` is git-ignored. The script outputs `dist\AStockQuantQt6.exe` and copies `config/settings.yaml` beside it. When frozen, `run_dashboard.py` changes directory to the EXE's folder, so `config/`, `data/` and `logs/` resolve relative to that folder.

## Architecture

**Collectors** (`src/collectors/`) → SQLite (`src/database/`) → **Analyzers** (`src/analyzers/`, LLM) → **Strategy** (`src/strategy/`) → **Trading** (`src/trading/`)

### Two runtimes with separate orchestration

- **`main.py` → `src/scheduler.py`** (BlockingScheduler) has two kinds of jobs:
  - Interval jobs: hot search every 30 min, 财联社 every 5 min, stock data every 15 min, global news every 30 min.
  - Weekday cron jobs: daily analysis at 15:30 (sentiment → limit-up → global impact → `CompositeScorer.score_today()`), signals at 16:00, self-learning at 16:20.
  - All intervals and times come from the `scheduler.*` config keys.
- **Desktop** (`run_dashboard.py` → `src/desktop/main_window.py`) does **not** use `scheduler.py`:
  - Qt timers and buttons drive `PipelineService` (`collect()` → `self_learn()` → `LimitUpPredictor.predict()`).
  - `CollectorOrchestrator` does the parallel collection.
  - `DataQueryService` serves every read the UI displays.
  - Long-running work runs in `QThreadPool` workers.

A new job or data source must be wired into each runtime that should run it.

### Data sources

- Realtime quotes fall back in order: Tencent `qt.gtimg.cn` (plain HTTP) → EastMoney → AKShare sina (`StockDataCollector._collect_realtime_quotes`).
- EastMoney data goes through `em_client.get_em_client()`, a Playwright singleton that works around TLS fingerprinting and replaces AKShare's `ak.*_em()` functions. Use it rather than calling `ak.*_em()` directly.
- `browser_client.py` keeps one Playwright instance per thread because sync Playwright objects can't cross threads. `ths_client.py` (同花顺) adds retries, a cookie fallback and a stale-cache fallback on top of it.
- `BaseCollector.fetch_url()` and `post_url()` retry with exponential backoff. `safe_collect()` catches every exception and returns `[]`, so a failing collector only shows up in the logs.
- `CollectorOrchestrator.collect_all()` requires every news source (cailianshe, xueqiu, jiuyan, hot_topics, weibo, douyin, toutiao) plus the market, global-news and US-earnings groups. It retries only the missing groups, up to `desktop.collect_max_attempts` times.

### Database (`src/database/`)

- `db.py` keeps a module-level engine and session-factory singleton, so `init_db(path)` and `get_db_session(path)` only honor `path` on the first call. To switch DBs (e.g. in tests), set `db._engine = None` and `db._SessionFactory = None`.
- `get_db_session()` commits on exit and rolls back on exception.
- There is no Alembic. `init_db()` runs `create_all` plus `_auto_migrate`, which only does `ALTER TABLE ... ADD COLUMN` for new model columns. Renames, type changes and dropped columns need a manual migration.

### Config (`src/config_loader.py`)

- `load_config()` deep-merges `settings.yaml` over `settings.yaml.example`, so a new config key needs a default in the example file.
- If `settings.yaml` is missing, `load_config()` falls back to the example file.
- Results are cached per path; `reload_config()` clears the cache.
- Two code paths rewrite `settings.yaml` at runtime, and both drop its comments:
  - Self-learning (`save_config()`) writes the full merged config.
  - The desktop AI settings dialog (`AISettingsDialog._save_to_yaml`) rewrites the `llm` section.

### Scoring and self-learning

- `CompositeScorer` (`src/strategy/scorer.py`) combines 8 factor scores, computed by `src/strategy/*_score.py`, using `strategy.weights`.
  - If `strategy.learning.enabled` is set and `strategy.adaptive_weights` is non-empty, each effective weight is `base*(1-blend_ratio) + adaptive*blend_ratio`, with `blend_ratio` defaulting to 0.35.
  - `generate_signals()` writes `TradeSignal` rows from the top-N `strong_buy`/`buy` scores.
- `SelfLearningService.run_daily_learning()` checks past signals against later `StockDaily` and `LimitUpStock` outcomes and correlates each factor with those outcomes.
  - It writes `strategy.adaptive_weights` and per-news-source `strategy.source_confidence` back into `settings.yaml`, unless `strategy.learning.persist_to_yaml: false`.
  - It also stores a `LearningSnapshot` row.

### LLM

- `LLMClient` (`src/analyzers/llm_client.py`) calls any OpenAI-compatible `base_url` through the OpenAI SDK (DeepSeek, Qwen, GLM, Kimi, SiliconFlow and others).
  - It fails over from the primary provider to the backup; the backup is skipped while its key still starts with `your-`.
  - Responses are cached in SQLite, keyed on a hash of model + prompts + params, with a TTL.
  - `reload()` hot-swaps providers.
- `LimitUpPredictor` (`src/services/premarket_predictor.py`) picks its prompt by time of day. The sessions are `premarket` (before 9:25), `morning`, `noon`, `afternoon` and `aftermarket` (after 15:00).

### Trading (`src/trading/`)

- `ExecutionService` turns `TradeSignal` rows into `TradeOrder` rows through a `BrokerAdapter`. `PaperBrokerAdapter` is the only adapter.
- New orders start as `PENDING_CONFIRM` and are deduplicated with `order_mapper.build_idempotency_key`.
- `RiskManager` applies position limits and the ST/*ST blacklist.

## Conventions

- Docstrings, comments and log messages are written in Chinese (辅助文档采用中文).
- Commits use Conventional Commits prefixes (`feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`), one logical change per commit.
- `AGENTS.md` refers to Alembic, `frontend/` and `infra/`, none of which exist in this repo. Ignore those parts.

每次修改提交git
