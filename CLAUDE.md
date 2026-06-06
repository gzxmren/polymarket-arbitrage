# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Polymarket 智能监控系统 — a prediction-market **monitoring / arbitrage-discovery / whale (大户) behavior-tracking** platform. It is **read-only / advisory**: there is no order-execution layer (`06-tools/trading/` is a README only). Comments, docs, and Telegram output are in Chinese.

## Three subsystems

1. **Offline monitoring engine** (Python) — entry `06-tools/monitoring/polymarket_monitor_v2.py`. Scans every 5 minutes via cron/nohup. Pulls market + trade + position data, runs Pair-Cost and cross-market arbitrage scans plus whale tracking, scores risk, and pushes Telegram alerts. Writes JSON artifacts to `07-data/`.
2. **Web Dashboard** — Flask backend `dashboard/backend/` (entry `run.py`, port **5000**, blueprints under `app/api/`, SocketIO realtime, APScheduler) + React/TypeScript/AntD frontend `dashboard/frontend/` (port 3000).
3. **Shared data layer** — the engine writes `07-data/*.json`; the dashboard reads SQLite. `dashboard/backend/app/services/data_sync.py` bridges JSON → DB, scheduled every 5 min.

### Critical architectural facts (read before changing data flow)
- **Optional-import + boolean-flag graceful degradation**: the monitor wraps most feature imports in `try/except ImportError`, setting flags like `NOTIFICATIONS_ENABLED`, `RISK_REVIEW_AVAILABLE`, `MARKET_MAKING_NOTIFY_ENABLED`. A missing module disables a feature instead of crashing. Preserve this pattern when adding features — never make a new import unconditional at module top.
- **Config single source of truth**: `06-tools/analysis/config.py`. `PROJECT_ROOT`, `DATA_DIR=07-data/`, all thresholds (`Thresholds.PAIR_COST`, currently `0.90`), API config, and `DBTables` table-name constants live here. The monitor *also* reads runtime feature toggles from env vars (`RISK_REVIEW_ENABLED`, `MARKET_MAKING_NOTIFY`, `NOTIFY_IMMEDIATELY`).
- **The live database is `dashboard/backend/database/polymarket.db`** (~138 MB). Other `*.db` files in the tree are/were symlinks to it, and root `polymarket_data.db` is a stale 0-byte leftover. Note `config.py` defines `DASHBOARD_DB_DIR = dashboard/database` which does **not** match the actual runtime path — verify which DB a script opens before trusting it.
- **Multi-source data fallback**: `06-tools/analysis/hybrid_data_source.py` resolves whale data by priority: rebuild-from-Trades → JSON → Leaderboard → no-data. Sources include Polymarket (Gamma/Data/CLOB), Manifold, Metaculus, Kalshi, news RSS, and the Leaderboard.
- **Two sync implementations coexist** (`data_sync.py` and `data_sync_v2.py`); authority is ambiguous. Check which one the scheduler actually invokes before editing sync logic.

## Common commands

Dashboard (run from `dashboard/`):
```bash
make install      # pip install backend + npm install frontend
make dev          # backend (run.py, :5000) + frontend (npm start, :3000)
make build        # frontend production build
make deploy       # docker-compose up -d --build  (also: make down, make logs)
make backup       # snapshot the sqlite DB
make sync         # run data_sync.py once
make lint         # py_compile the backend
```

Monitoring engine:
```bash
./start_monitor.sh                                    # nohup the 5-min monitor, logs to /tmp/monitor.log
PYTHONPATH=06-tools/analysis python3 06-tools/monitoring/polymarket_monitor_v2.py
./run_sync_changes.sh                                 # one-shot changes-table sync
```
The monitor and most analysis scripts require `06-tools/analysis` (and often `06-tools/monitoring`) on `PYTHONPATH` because modules import each other by bare name (e.g. `from pair_cost_scanner import ...`).

Tests (pytest, config in `10-tests/conftest.py` which puts the analysis/monitoring dirs on `sys.path`):
```bash
pip install -r 10-tests/requirements-test.txt
python3 -m pytest 10-tests/ -v                        # unit / integration / e2e subdirs
python3 -m pytest 10-tests/unit/test_x.py::test_name  # single test
cd dashboard && make test                             # backend pytest (dashboard/backend/tests)
```

## Conventions
- **Test isolation is enforced**: test code must run with a `--test` flag and write to `/tmp/`; production data dirs (`07-data/`) must never receive test fixtures. Honor this when adding scripts.
- Database schema changes are done via hand-written `dashboard/backend/migrate_db*.py` scripts (no migration framework). There is no CI.
- `.bak` / timestamped backup files and `backfill_*.py` / `cleanup_*.py` one-off scripts are scattered in the source tree — these are operational scripts, not part of the import graph.

## Known data-integrity issues (state, not aspiration)

A 2026-06 review flagged data corruption. **Re-verified against the live DB on 2026-06-06 — most items are already remediated.** Authoritative check: `python3 scripts/data_health_check.py` (junk 0.0% / dead-space 0.5% / has_activity-mislabel 0 as of 2026-06-06).

RESOLVED (verified):
- ~~`whales` table ~98% garbage~~ → **junk rate now 0.0%** (only 1 truly-empty row of 3035). The original "98% garbage" used a flawed predicate (`total_value=0 AND position_count=0`): that catches *trade-flow whales* (real `total_volume`/`changes_count`, just no current position snapshot — these are the follow-whale signal source and are **not** garbage). `data_sync.py::sync_whales` now has a value gate (`[P0-2]`, ~line 137); `scripts/cleanup_whales.py` deletes only true empties (`changes_count=0 AND total_volume=0`).
- ~~~47% freelist dead space~~ → **0.5% now** (DB VACUUMed; ~51 MB, was ~138 MB).
- ~~`has_activity` set-once bug~~ → main `data_sync.py` path now direct-assigns (`has_activity = excluded.has_activity`, can reset to 0). `sync_changes.py` hardcodes `=1` but only for wallets with a change this batch (correct, not a bug — see inline comment).
- ~~`config.DBTables.SIGNALS` name drift~~ → fixed: `SIGNALS='signals'`, added `SEMANTIC_SIGNALS='semantic_signals'`. NB: the whole `DBTables` class has **0 references** project-wide (reserved constants only).

STILL OPEN:
- Phase-3 "quality & automation" tables (`signals`, `signal_results`, `whale_performance`, `strategy_performance`, `quality_reports`, `threshold_history`, `opportunity_history`) are all empty — code merged but the scheduling chain isn't running. (This is an unfinished feature, not corruption.)
- No file/archive rotation/TTL: `07-data` ≈ 286 MB / ~2200 files; `monitor_report_*.json`, `whale_states/*.json`, and `positions_archive` (~38.6k rows, 3.4× `positions`) accumulate. Housekeeping only — disk, not correctness.

Full analysis: `PROJECT_REVIEW.md` (+ 2026-06-06 correction note at top) and `PROJECT_OVERVIEW.md`.
