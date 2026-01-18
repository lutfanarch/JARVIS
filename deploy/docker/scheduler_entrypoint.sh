#!/usr/bin/env bash

# Entrypoint for the scheduler service.
#
# This script sets up a cron job to run the JARVIS daily scan at a
# specified schedule and timezone.  It writes the cron definition
# into /etc/cron.d/jarvis, ensures logs are persisted under
# /app/artifacts/logs and runs the Debian cron daemon in the
# foreground.  The schedule and timezone are controlled via the
# environment variables JARVIS_SCAN_CRON and JARVIS_SCAN_TZ.

set -euo pipefail

# Default values
JARVIS_SCAN_TZ=${JARVIS_SCAN_TZ:-America/New_York}
JARVIS_SCAN_CRON=${JARVIS_SCAN_CRON:-"15 10 * * 1-5"}

# Create log directory if it does not exist
mkdir -p /app/artifacts/logs

# Write cron file.  Use CRON_TZ to anchor schedule to the desired timezone.
# Debian cron format requires a user field after the schedule.  The
# command is executed by the root user and wrapped in a login shell
# that changes to /app before invoking the daily scan.  Output is
# appended to the daily scan log.  Ensure the file ends with a newline.
CRON_FILE=/etc/cron.d/jarvis
{
  echo "CRON_TZ=$JARVIS_SCAN_TZ"
  echo "SHELL=/bin/bash"
  echo "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
  # Specify the root user and cd into /app before running the script.
  echo "$JARVIS_SCAN_CRON root bash -lc 'cd /app && /app/scripts/daily_scan.sh' >> /app/artifacts/logs/daily_scan.log 2>&1"
} > "$CRON_FILE"

# Ensure proper permissions
chmod 0644 "$CRON_FILE"

# Apply cron job and run cron in foreground
cron -f