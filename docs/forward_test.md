# Forward Test (Shadow) Mode

The forward‑test or **shadow** mode allows you to run the complete daily
scan pipeline deterministically without sending live trade alerts.  It
executes the same data ingestion, quality checks, feature computation,
packet assembly and decision logic as the live mode, but it will
**never** dispatch Telegram notifications.  Instead, all intermediate
outputs and the final decision artefact are written to a dedicated
directory so that you can audit and evaluate the decision in
hindsight.

## Enabling Shadow Mode

To run a forward‑test (shadow) scan you no longer need Bash or a
shell script.  Instead use the Windows‑friendly `jarvis` CLI with the
`daily-scan` command and specify `--run-mode shadow`.  For example,
from a PowerShell prompt:

```powershell
jarvis daily-scan --run-mode shadow
```

This executes the entire daily scan pipeline deterministically
without sending live trade alerts.  The final decision file is written
to `artifacts/decisions/<run_id>.json`, and a comprehensive set of
artefacts is saved under `artifacts/forward_test/<ny_date>/<run_id>/`.
These artefacts include the run configuration, the packets fed into
the LLM pipeline, the decision, a validator report and the lock
status.  A summary entry is also appended to
`artifacts/forward_test/forward_test_runs.jsonl` for easy querying.

## Forward‑Test CLI

The `forwardtest` command group under the `jarvis` CLI exposes
utilities for inspecting and reporting on recorded runs:

- `jarvis forwardtest record --run-id <id> --as-of <timestamp>`
  records a completed shadow run.  This is normally invoked by
  `jarvis daily-scan --run-mode shadow` when recording forward‑test runs.
- `jarvis forwardtest list --start <YYYY-MM-DD> --end <YYYY-MM-DD>`
  lists runs by New York trade date, showing the decision status and
  selected symbol.
- `jarvis forwardtest report --start <YYYY-MM-DD> --end <YYYY-MM-DD> --out <path>`
  writes a JSON report summarising counts by status and symbol.
  You may either specify `--run-id <RUN_ID>` to look up
  the trade date and symbol from the recorded forward‑test run, or
  provide `--ny-date` and `--symbol` explicitly.  Do not mix run‑id
  with explicit date/symbol in the same invocation.  A realised
  entry price is optional; if omitted, the entry from the decision
  artefact will be used when computing realised PnL and R in the
  report.

See `jarvis forwardtest --help` for details.