#!/usr/bin/env bash

# JARVIS daily scan orchestration script.
#
# This script orchestrates the end‑to‑end workflow for a single trading
# day.  It performs health checks, incremental ingestion, quality
# assurance, feature computation, chart rendering, packet building,
# decision making and optional notification.  It is designed to run
# once per day on a scheduler.  Even if earlier steps fail, the
# decision step will still run to produce an auditable artifact.

# Do not exit on failures; capture errors and continue
set +e

# Determine run ID and as‑of timestamp.  Use environment overrides if provided.
RUN_ID="${RUN_ID:-$(date -u +"%Y%m%d%H%M%S")}"
AS_OF="${AS_OF:-$(date -u +"%Y-%m-%dT%H:%M:%SZ")}"

# Determine repository root relative to this script and change working directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

# Determine run mode: live (default), shadow or backtest.  When run mode is
# shadow the notification step is suppressed and a forward-test run record
# is written after the decision stage.
RUN_MODE="${JARVIS_RUN_MODE:-live}"

# Prepare absolute artifact directories under the repository root.  These
# directories live under /app/artifacts when the script is executed via
# Docker compose.  Using absolute paths ensures that artifacts are
# correctly written regardless of the current working directory.
ARTIFACTS_DIR="${REPO_ROOT}/artifacts"
PACKETS_DIR="${ARTIFACTS_DIR}/packets/${RUN_ID}"
DECISIONS_DIR="${ARTIFACTS_DIR}/decisions"
STATE_DIR="${ARTIFACTS_DIR}/state"
mkdir -p "$PACKETS_DIR" "$DECISIONS_DIR" "$STATE_DIR"

echo "[JARVIS] Starting daily scan run $RUN_ID at $AS_OF"

# 1. Health check (strict).  Run with --strict but continue on failure.
python -m informer healthcheck --strict || echo "[JARVIS] Health check failed"

# 2. Ingestion (incremental).  SYMBOLS env should be configured externally or default whitelist is used.
python -m informer ingest || echo "[JARVIS] Ingestion failed"

# 3. Corporate actions step (optional; safe).  Run if available.
if python -m informer actions --help >/dev/null 2>&1; then
  python -m informer actions || echo "[JARVIS] Actions failed"
fi

# 4. QA step
python -m informer qa || echo "[JARVIS] QA failed"

# 5. Feature computation
python -m informer features || echo "[JARVIS] Features failed"

# 6. Chart rendering
python -m informer charts || echo "[JARVIS] Charts failed"

# 7. Packet building
python -m informer packet --run-id "$RUN_ID" --as-of "$AS_OF" || echo "[JARVIS] Packet build failed"

# 8. Decision making
python -m informer decide --run-id "$RUN_ID" --as-of "$AS_OF" || echo "[JARVIS] Decision failed"

# 9. Notification
DECISION_FILE="${DECISIONS_DIR}/${RUN_ID}.json"

if [ -f "$DECISION_FILE" ]; then
  if [ "$RUN_MODE" = "live" ]; then
    python -m informer notify --decision-file "$DECISION_FILE" || echo "[JARVIS] Notification step failed"
  else
    echo "[JARVIS] Notification suppressed for run mode $RUN_MODE"
  fi
else
  echo "[JARVIS] Decision file not found: $DECISION_FILE"
fi

# 10. Forward-test recording for shadow mode
if [ "$RUN_MODE" = "shadow" ]; then
  python -m informer forwardtest record --run-id "$RUN_ID" --as-of "$AS_OF" --mode "$RUN_MODE" || echo "[JARVIS] Forward test record failed"
fi

echo "[JARVIS] Daily scan completed for run $RUN_ID"