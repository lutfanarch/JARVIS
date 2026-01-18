# JARVIS Operations Runbook

This runbook provides guidance for operators and developers running the
JARVIS system in production.  It describes required environment
variables, a daily operational checklist, healthcheck usage and
troubleshooting tips keyed by healthcheck codes.

## Local Windows Quickstart

JARVIS is designed to run end‑to‑end on a Windows laptop without Docker
or Bash.  Follow this sequence in a Windows PowerShell terminal to
get started with a local SQLite database and verify your installation:

```powershell
# Set required environment variables (SQLite by default)
$env:DATABASE_URL = "sqlite:///./jarvis.db"
# Set run mode to shadow to suppress live notifications during testing
$env:JARVIS_RUN_MODE = "shadow"

# Initialize the database schema
jarvis db-init

# Run the offline smoke test to verify migrations, healthchecks,
# the daily scan and the scheduler.  This command writes a
# decision file under artifacts/decisions and prints a summary.
jarvis smoke-test

# Compute the next scheduled run time (10:15 America/New_York)
jarvis scheduler --dry-run
```

The smoke test runs in shadow mode, requires no Alpaca or Telegram
credentials and always emits a decision artifact under
`artifacts/decisions/<run_id>.json`.  Use this as your first
verification after installation.  When ready to run the pipeline
automatically every weekday, remove the `--dry-run` flag from the
scheduler command (leaving it running in its own window or service).

## Required Environment Variables

The following environment variables must be defined for JARVIS to
operate correctly.  Values are not included here; consult your
deployment documentation for the correct secrets.

| Variable | Purpose |
|---------|---------|
| `DATABASE_URL` | SQLAlchemy connection string pointing to a SQLite file or a Postgres/TimescaleDB instance.  For local development use `sqlite:///./jarvis.db`. |
| `ALPACA_API_KEY_ID` | Alpaca API key ID for data provider authentication. |
| `ALPACA_API_SECRET_KEY` | Alpaca API secret key for data provider authentication. |
| `SYMBOLS` | Comma‑separated list of allowed trading symbols.  Defaults to the halal universe defined by `UNIVERSE_VERSION` (`universe_v2_2026-01-14`) if unset.  The current universe comprises: AAPL, MSFT, NVDA, GOOGL, GOOG, AVGO, META, TSLA, LLY, XOM, CVX, JNJ, ABBV, MRK, ABT, TMO, ISRG, PG, PEP, HD, ORCL, CSCO, CRM, AMD, MU, INTC, KLAC, QCOM, LRCX, AMAT, LIN. |
| `TIMEFRAMES` | Comma‑separated list of timeframes to ingest and analyse (e.g. `15m,1h,1d`).  Defaults are used if unset. |
| `FEATURE_VERSION` | Version tag used when writing feature snapshots; allows multiple feature sets to coexist. |
| `CHART_VERSION` | Version tag used when writing chart PNGs. |
| `SCHEMA_VERSION` | Version tag representing the database schema. |
| `RUN_ID` | Optional run identifier used by the CLI; a timestamp is generated if unset. |
| `LLM_MODE` | Set to `fake` (default) to disable live LLM calls or `live` to enable OpenAI/Gemini integration. |
| `TELEGRAM_BOT_TOKEN` | Token used by the Telegram bot for dispatching trade alerts. Optional unless notifications are desired. |
| `TELEGRAM_CHAT_ID` | Numeric chat or user ID where trade alerts will be sent. Must be present in the allowlist. |
| `TELEGRAM_CHAT_ID_ALLOWLIST` | Comma‑separated list of allowed chat IDs for notification. A security measure to prevent accidental sends. |
| `TELEGRAM_STATE_DIR` | Optional directory where idempotency keys for Telegram notifications are persisted (default: `artifacts/state`). |
| `JARVIS_SCAN_TZ` | Timezone identifier (e.g. `America/New_York`) used by the scheduler service. |
| `JARVIS_SCAN_CRON` | Cron expression controlling when the daily scan runs. Defaults to `15 10 * * 1-5` (10:15 ET weekdays). |

| `OPENAI_API_KEY` | Secret for OpenAI GPT calls used in Phase 2. Required only in production when using live GPT models. |
| `GEMINI_API_KEY`/`GOOGLE_API_KEY` | Secret for Google’s Gemini calls used in Phase 2. Required only in production when using live Gemini models. |

## Phase 1B: Universe & Opportunity Coverage

The default halal trading universe used by JARVIS has been expanded in
`UNIVERSE_VERSION` `universe_v2_2026-01-14` to reduce concentration
and correlation risk while improving opportunity coverage.  The
expanded universe includes 31 equities spanning technology, consumer,
healthcare and industrial sectors (see the `SYMBOLS` entry above for
the exact list).  This long‑only universe increases the likelihood of
identifying actionable setups on any given day.

If you need to limit ingestion, feature computation or chart rendering
to a smaller set of symbols, set the `SYMBOLS` environment variable
to a comma‑separated subset (e.g. `SYMBOLS=AAPL,MSFT`).  CLI commands
and healthchecks will always enforce that the supplied symbols are a
subset of the configured universe.

## Phase 3: Notification and Always‑On Scheduling

JARVIS includes a simple notification subsystem that dispatches
Telegram messages only when the daily decision yields a `TRADE`.  The
notification is skipped for `NO_TRADE` or `NOT_READY` outcomes.  To
enable notifications set `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` and
`TELEGRAM_CHAT_ID_ALLOWLIST`; the chat ID must be present in the
allowlist or the message will not be sent.  A deduplication key
derived from the trade date, symbol, entry and stop is written to the
directory defined by `TELEGRAM_STATE_DIR` (default: `artifacts/state`)
to avoid sending duplicate alerts across reruns.

### Live mode failure semantics

In ``live`` mode the daily scan sends a Telegram alert only when the
decision action is ``TRADE`` and the notification configuration is
valid.  If the notification step fails—because the bot token or
chat ID is missing, the chat ID is not in the allowlist or another
error occurs—the pipeline does **not** abort.  It still writes
the decision and run record artefacts and exits successfully.  The
run record marks the ``notify`` step as ``FAIL`` and includes a
short error describing the problem.  No message is sent.

To catch configuration errors before running in live mode, use the
configuration check:

```powershell
jarvis config-check --mode live
```

This command reports missing keys, allowlist mismatches and other
issues so that you can resolve them prior to enabling the live
scheduler.

For unattended 24/7 operation JARVIS no longer relies on Bash.  Use
the built‑in `jarvis daily-scan` command to orchestrate the full
pipeline—healthcheck, ingestion, corporate actions, quality
checks, feature computation, chart rendering, packet assembly,
decision making and optional notification.  This command always
produces a decision file under `artifacts/decisions/<run_id>.json`
even if earlier stages fail.

To schedule the daily scan without Docker, use the built‑in
`jarvis scheduler` command.  It computes the next run time at
10:15 AM in the timezone specified by `JARVIS_SCAN_TZ` (default
`America/New_York`), sleeps until that moment and then executes
`jarvis daily-scan`.  The scheduler loops forever by default; supply
`--once` to run a single scan or `--dry-run` to print the next
scheduled run without sleeping or executing.  When using Docker, the
compose file under `deploy/docker/` provisions a scheduler service
that reads the `JARVIS_SCAN_TZ` and `JARVIS_SCAN_CRON` variables
(default schedule `15 10 * * 1-5` which corresponds to 10:15 AM
New York time on weekdays) to trigger the scan.  Adjust these
variables to suit your deployment environment and market hours.

## Phase 2: Multi‑LLM Provider Policy and Role Routing

Phase 2 introduces a multi‑LLM decision pipeline that combines the
strengths of OpenAI’s GPT models and Google’s Gemini models.  Only
these two providers are permitted; any attempt to configure
additional providers will fail fast.  The pipeline assigns each
decision role to a fixed provider:

- **Screener** – uses OpenAI GPT to shortlist candidates based on
  trending regime and quality checks.  This stage operates on
  structured data only (no charts).
- **Analyst** – uses OpenAI GPT to generate entry, stop and target
  proposals for each candidate.
- **Critic** – uses Google Gemini to perform risk‑oriented reviews,
  such as rejecting high volatility or failed QA candidates.  If the
  critic call fails (e.g. API timeout), the pipeline returns
  `NO_TRADE` with reason code `CRITIC_UNAVAILABLE` rather than
  silently substituting GPT.  A fallback flag can be enabled to
  override this behaviour, but it is disabled by default to avoid
  unexpected substitutions.
- **Arbiter** – uses OpenAI GPT to select a single trade from the
  evaluated candidates, subject to risk gating and whitelist
  enforcement.

Operators must set `OPENAI_API_KEY` and `GEMINI_API_KEY` (or
`GOOGLE_API_KEY`) in environments where live LLM calls are performed.
For unit tests the system uses a deterministic `FakeLLMClient` and
does not require network connectivity.

## Daily Operator Checklist

Perform the following steps every trading day to maintain data
freshness and availability:

1. **Verify Environment** – ensure all required environment variables
   are set (see table above) and that database connectivity is
   available.
2. **Ingest OHLCV Data** – run the ingest command to fetch new bars
   and insert them into the database.  Use incremental ingestion
   without a start date when running throughout the day.
3. **Run QA Checks** – execute the QA command to evaluate data
   quality.  Address any errors reported in the `data_quality_events`
   table before proceeding.
4. **Compute Features** – compute indicators, candlestick patterns and
   regimes via the features command.  Ensure the `feature_version`
   parameter is consistent across runs.
5. **Render Charts** – generate updated candlestick charts for all
   symbols and timeframes.  Verify that the PNG files are written
   under the charts directory.
6. **Ingest Corporate Actions** – fetch and upsert recent corporate
   actions.  Running this step daily ensures that packets include
   upcoming ex‑dates for dividends, splits and other actions.
7. **Build Packets** – assemble the canonical JARVIS packets.  Packets
   should contain the latest bars, features, regimes, events and
   chart references.  A status of `OK` indicates readiness for
   downstream analysis and signalling.
8. **Run Healthcheck** – execute the healthcheck command to produce a
   deterministic report summarising system status.  Review any
   errors or warnings and address issues before market open.
9. **Review Logs** – monitor logs (if configured) for unexpected
   errors or performance bottlenecks.

## Healthcheck Codes and Troubleshooting

The healthcheck produces a list of named checks.  Use the table
below to interpret their meanings and common remediation steps:

| Code | Severity | Meaning | Resolution |
|------|----------|---------|------------|
| `PYTHON_VERSION` | ERROR | Python interpreter version is below 3.11. | Upgrade the runtime to Python 3.11 or newer. |
| `DEPENDENCIES_PRESENT` | ERROR | One or more required Python packages are missing. | Install missing packages (pandas, numpy, sqlalchemy, requests, click, pydantic, mplfinance, matplotlib). |
| `TALIB_OPTIONAL` | INFO | Indicates whether TA‑Lib is installed.  Missing TA‑Lib does not block operations. | Install TA‑Lib if candlestick pattern recognition is needed. |
| `ENV_DATABASE_URL` | ERROR | `DATABASE_URL` is not set. | Set the environment variable to a valid Postgres/TimescaleDB connection string. |
| `ENV_ALPACA_KEYS` | WARN | One or both Alpaca API keys are missing. | Set `ALPACA_API_KEY_ID` and `ALPACA_API_SECRET_KEY` for live data ingestion. |
| `DB_CONNECT` | ERROR | Unable to establish a database connection. | Check database availability, network connectivity and credentials. |
| `DB_SCHEMA_TABLES` | ERROR | One or more required tables are missing from the database. | Initialize the database schema (e.g. run `jarvis db-init` or `make init-db`). |
| `ARTIFACTS_WRITABLE` | ERROR | JARVIS cannot write to the configured artifacts directories. | Verify filesystem permissions and disk space. |
| `WHITELIST_ENFORCED` | ERROR | Symbols passed to a command are not in the configured whitelist. | Adjust the `SYMBOLS` environment variable or the CLI arguments. |

## Additional Notes

* **Strict Mode** – When the `--strict` flag is supplied to the
  healthcheck command, the process exits with status 2 if the
  health report contains any errors.  Warnings do not alter the
  report’s status but can be used to trigger non‑zero exit codes in
  strict environments.
* **Report Files** – Each healthcheck run writes a JSON report to
  disk.  By default reports are placed under `artifacts/health/<run_id>.json`.
  You can override this location with the `--out` CLI option.
* **Timestamps** – All timestamps in the report are emitted as
  UTC‑aware ISO strings without microseconds for consistency.

Refer to the main `README.md` for details on each CLI command and the
pipeline stages.

## Local Daily Scan and Scheduler

For Windows and other local deployments without Docker, use the built‑in
Python orchestrator instead of the Bash script.  The `jarvis
daily-scan` command runs the entire end‑to‑end workflow (health
checks, ingestion, corporate actions, quality assurance, feature
computation, chart rendering, packet assembly, decision making,
optional notification and forward testing) without relying on a shell.
Failures in intermediate steps are logged but do not abort the run, and
a decision JSON file is always produced under
`artifacts/decisions/<run_id>.json`.

To perform a one‑off run on Windows PowerShell:

```powershell
$env:DATABASE_URL = "sqlite:///./jarvis.db"
jarvis db-init
jarvis daily-scan --run-mode shadow
```

To schedule the daily scan to run automatically at 10:15 AM New York time
on weekdays without Docker, use `jarvis scheduler`.  The scheduler is
DST‑aware and anchors to the `America/New_York` timezone by default.
It computes the next run time and sleeps until then before invoking
`jarvis daily-scan`.  You can preview the next scheduled run without
sleeping using the `--dry-run` flag:

```powershell
jarvis scheduler --dry-run
```

By default the scheduler loops forever.  Use `--once` to perform a
single run and then exit.

The scheduler respects the environment variable `JARVIS_SCAN_TZ`,
allowing you to override the scheduling timezone if necessary.
