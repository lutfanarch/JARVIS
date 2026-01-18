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

Set the environment variable `JARVIS_RUN_MODE` to `shadow` when
invoking the daily scan script.  For example:

```sh
JARVIS_RUN_MODE=shadow ./scripts/daily_scan.sh
```

In shadow mode the final decision file is still written to
`artifacts/decisions/<run_id>.json` and a comprehensive set of
artefacts is saved under `artifacts/forward_test/<ny_date>/<run_id>/`.
These artefacts include the run configuration, the packets fed into
the LLM pipeline, the decision, a validator report and the lock
status.  A summary entry is also appended to
`artifacts/forward_test/forward_test_runs.jsonl` for easy querying.

## Forward‑Test CLI

The `forwardtest` command group exposes utilities for inspecting and
reporting on recorded runs:

- `informer forwardtest record --run-id <id> --as-of <timestamp>`
  records a completed shadow run.  This is normally invoked by
  `scripts/daily_scan.sh` when `JARVIS_RUN_MODE=shadow`.
- `informer forwardtest list --start <YYYY-MM-DD> --end <YYYY-MM-DD>`
  lists runs by New York trade date, showing the decision status and
  selected symbol.
- `informer forwardtest report --start <YYYY-MM-DD> --end <YYYY-MM-DD> --out <path>`
  writes a JSON report summarising counts by status and symbol.
- `informer forwardtest log-outcome --ny-date <YYYY-MM-DD> --symbol <SYM> --entry <price> --exit <price> [--notes <text>]`
  appends a realised trade outcome to the forward test outcomes
  registry.

See `informer forwardtest --help` for details.