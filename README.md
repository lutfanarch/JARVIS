# JARVIS

JARVIS is an AI-assisted trading data ingestion and analysis tool.  It
provides a deterministic pipeline for ingesting market data, storing it in a
relational database (SQLite or Postgres/TimescaleDB), and emitting canonical data packets for downstream
analysis.  The project is modular and testable; it uses SQLAlchemy and
Alembic for database interactions, Click for the command-line interface, and
Pydantic for type-safe models.

## Quickstart (Local, Windows-friendly)

JARVIS runs end‑to‑end without Docker.  The following steps walk through a typical setup on Windows or any local machine:

1. **Install dependencies** – create a Python 3.11 virtual environment and install JARVIS in editable mode with development extras:

   ```sh
   pip install -e '.[dev]'
   ```

2. **Configure your environment** – copy `env.example` to `.env` and adjust variables as needed.  For a quick start set:

   ```env
   DATABASE_URL=sqlite:///./jarvis.db
   ```

   This uses a local SQLite file and does not require TimescaleDB/Postgres.

3. **Initialize the database** – run the built‑in command to apply Alembic migrations:

   ```sh
   jarvis db-init
   ```

4. **Verify installation** – run the offline smoke test to exercise migrations, health checks, the daily scan and the scheduler.  This command writes a decision file under ``artifacts/decisions`` and prints a concise summary.  It requires no network access or API keys.

   ```sh
   jarvis smoke-test
   ```

5. **Ingest historical bars** – fetch OHLCV data into the database:

   ```sh
   jarvis ingest --symbols AAPL,MSFT --start 2025-01-01 --end 2025-01-10
   ```

6. **Explore the CLI** – run `jarvis --help` to see all available commands.  Executing `jarvis <command> --help` provides detailed usage.

## Configuration check

JARVIS includes a lightweight configuration checker to validate that your
environment variables are set appropriately for the desired mode of
operation.  Running `jarvis config-check` does not perform any
network calls and can be used at any time to sanity‑check your setup.

- Use `jarvis config-check --mode shadow` to verify that the CLI is
  installed correctly.  In shadow mode the checker does not require any
  API keys and always exits successfully.  This is useful for offline
  development and testing.
- Use `jarvis config-check --mode live` to ensure that all mandatory
  API keys are present for live operation.  These include
  `ALPACA_API_KEY_ID`, `ALPACA_API_SECRET_KEY`, `OPENAI_API_KEY` and
  either `GEMINI_API_KEY` or `GOOGLE_API_KEY`.  If any are missing the
  command exits with a non‑zero status and lists the missing variable names.


For development sanity checking, ensure your local sources are used by setting `PYTHONPATH=src` when running commands directly from the repository.

## Live LLM Mode and Notification

By default JARVIS operates in a deterministic, offline mode using a
`FakeLLMClient` for all large language model calls.  To enable live
integration with OpenAI GPT and Google Gemini, set the environment
variable `LLM_MODE=live` and provide the required API keys
(`OPENAI_API_KEY` and `GEMINI_API_KEY` or `GOOGLE_API_KEY`).  In live
mode the decision pipeline will contact the providers using short
timeouts and fall back to a NO_TRADE decision when the critic stage
fails.  Offline unit tests always run with `LLM_MODE=fake`.

JARVIS includes a minimal Telegram notification system.  When the
`notify` command is executed with the path to a decision file, it
parses the JSON and sends a formatted message only when the action is
`TRADE`.  To configure notifications, set the following environment
variables:

* `TELEGRAM_BOT_TOKEN` – token for your Telegram bot.
* `TELEGRAM_CHAT_ID` – numeric chat ID or user ID where messages
  should be sent.
* `TELEGRAM_CHAT_ID_ALLOWLIST` – comma‑separated list of chat IDs
  permitted to receive notifications.  The target chat ID must be
  present in this list.
* `TELEGRAM_STATE_DIR` – optional directory for idempotency keys to
  prevent duplicate sends (defaults to `artifacts/state`).

Use the `jarvis notify --decision-file artifacts/decisions/<run_id>.json`
command to dispatch a trade alert.  No message is sent for
NO_TRADE decisions.

## Phase 3: 24/7 Automation and Scheduling

For unattended operation JARVIS no longer relies on a shell script.  It
ships with two cross‑platform commands that orchestrate and schedule
the daily trading workflow entirely in Python.  Use the
`jarvis daily-scan` command to run the full pipeline once.  This
command wraps all stages—health checks, ingestion, corporate actions,
quality assurance, feature computation, chart rendering, packet
assembly, decision making, optional notification and forward testing.
Failures in intermediate steps do not abort the run; a decision JSON is
always written under `artifacts/decisions/<run_id>.json`.

To run the scan automatically at a fixed time on weekdays, use
`jarvis scheduler`.  The scheduler computes the next run time at
10:15 AM in the timezone configured by the `JARVIS_SCAN_TZ`
environment variable (default `America/New_York`) and sleeps until
then before invoking `jarvis daily-scan`.  It loops indefinitely by
default; supply `--once` to run a single cycle or `--dry-run` to
print the next scheduled run without sleeping or executing the scan.  A
similar Docker‑based scheduler service remains available in
`deploy/docker/` for containerised deployments, but local usage never
requires Docker.

## Deploy with Docker (optional)

Docker deployment is optional and primarily intended for production or
server environments.  The Dockerfile and compose configuration live under
`deploy/docker/` to keep them separate from the local development flow.
When running inside Docker a TimescaleDB container and a lightweight
scheduler service are provisioned alongside JARVIS.  The scheduler
triggers the daily scan according to the cron‑style schedule defined by
the `JARVIS_SCAN_CRON` and `JARVIS_SCAN_TZ` environment variables
(defaults: 10:15 AM New York time on weekdays).  Adjust these variables
to suit your market timings.  To stand up the stack run:

```sh
cd deploy/docker
docker compose up -d
```

Logs can be obtained via `docker compose logs`.  Local operation never
requires Docker.

## Typical Phase 1 Daily Flow

A typical operational day for JARVIS involves a sequence of deterministic
tasks.  Operators should run the commands in the following order to
maintain data freshness and integrity:

1. **Ingest** – fetch new OHLCV bars and upsert into the database:

   ```sh
   jarvis ingest --symbols AAPL,MSFT --start 2025-01-01 --end 2025-01-10
   ```

2. **Quality Assurance (QA)** – evaluate data quality on the stored bars and
   record any anomalies:

   ```sh
   jarvis qa --symbols AAPL,MSFT --timeframes 15m,1h,1d --start 2025-01-01 --end 2025-01-10
   ```

3. **Compute Features** – calculate indicators, candlestick patterns and
   regimes and persist them into the `features_snapshot` table:

   ```sh
   jarvis features --symbols AAPL,MSFT --timeframes 15m,1h,1d --start 2025-01-01 --end 2025-01-10
   ```

4. **Render Charts** – generate deterministic candlestick PNGs for each
   symbol/timeframe:

   ```sh
   jarvis charts --symbols AAPL,MSFT --timeframes 15m,1h,1d --start 2025-01-01 --end 2025-01-10 --out-dir artifacts/charts
   ```

5. **Ingest Corporate Actions** – retrieve corporate actions and upsert
   them into the database:

   ```sh
   jarvis actions --symbols AAPL,MSFT --start 2025-01-01 --end 2025-03-31
   ```

6. **Build Packets** – assemble canonical packets combining bars, features,
   charts and events:

   ```sh
   jarvis packet --symbols AAPL,MSFT --as-of 2025-01-10T00:00:00Z --out-dir artifacts/packets --charts-dir artifacts/charts
   ```

7. **Healthcheck** – run a deterministic health report to verify
   environment, dependencies, database and filesystem state:

   ```sh
   jarvis healthcheck --symbols AAPL,MSFT --timeframes 15m,1h,1d
   ```

## Phase 1B: Universe & Opportunity Coverage

JARVIS operates on a deterministic, strictly allowlisted set of equities
defined in the configuration module.  As of `UNIVERSE_VERSION`
`universe_v2_2026-01-14`, the default halal universe has been expanded
to reduce concentration risk and broaden opportunity coverage while
staying long‑only.  When the `SYMBOLS` environment variable is unset,
CLI commands operate on the following symbols:

AAPL, MSFT, NVDA, GOOGL, GOOG, AVGO, META, TSLA, LLY, XOM, CVX, JNJ,
ABBV, MRK, ABT, TMO, ISRG, PG, PEP, HD, ORCL, CSCO, CRM, AMD, MU,
INTC, KLAC, QCOM, LRCX, AMAT, LIN.

If you wish to limit daily ingestion or compute to a subset, set
`SYMBOLS` to a comma‑separated list (for example, `SYMBOLS=AAPL,MSFT`).

## Phase 2: Multi‑LLM Provider Policy

In Phase 2 JARVIS introduces a multi‑LLM architecture to evaluate
candidate trades using both OpenAI’s GPT models and Google’s Gemini
models.  Only these two providers are supported; attempts to
configure other providers will result in an error.  The provider
routing is deterministic and tied to the role of each stage in the
decision pipeline:

- **Stage A Screener** → OpenAI (GPT)
- **Stage B Analyst** → OpenAI (GPT)
- **Stage B Critic** → Google (Gemini)
- **Stage B Arbiter** → OpenAI (GPT)

This routing ensures diversity of perspectives while keeping the
pipeline deterministic.  The critic stage serves as a risk gate; if
the critic call fails (e.g. API timeout or invalid response), the
pipeline will produce a NO_TRADE decision with a reason code of
`CRITIC_UNAVAILABLE`.  You can optionally configure the system to
fall back to GPT when Gemini is unavailable, but this is disabled by
default.  To use these providers in production you must set the
following environment variables (secrets are never committed to
version control):

- `OPENAI_API_KEY` – API key for OpenAI GPT models.
- `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) – API key for Google Gemini models.

These variables are not required for unit tests, which use the
deterministic `FakeLLMClient`.  The CLI and healthcheck commands
always enforce that the configured providers are a subset of the
allowed set `{openai, google}`.

## Healthcheck Examples

The `healthcheck` command validates runtime prerequisites and produces
both a human summary and a JSON report.  By default it writes the JSON
file under `artifacts/health/<run_id>.json`.  You can override the
output path with `--out` and print the JSON to stdout with `--json`.

Generate a report for the default symbol whitelist and timeframes,
writing the report into the default artifacts directory:

```sh
jarvis healthcheck
```

Specify an explicit run identifier and write the report to a custom
location while also printing the JSON to stdout:

```sh
jarvis healthcheck --symbols AAPL --run-id 20260116T220000Z --out reports/health.json --json
```

Enforce strict mode, which causes the command to exit with code 2
when any errors are present (warnings are not elevated to errors in
the report but can affect exit status):

```sh
jarvis healthcheck --strict
```
