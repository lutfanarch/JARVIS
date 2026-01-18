# Governance and Rule Change Protocol

The JARVIS trading system operates under a strict governance model to
ensure that rule changes are deliberate, validated and auditable.  The
following principles apply to all parameter and threshold updates:

1. **Version Bumping** – Every change to the strategy logic, risk
   limits or selection criteria must be accompanied by a version
   increment.  The `UNIVERSE_VERSION` and other constants in
   `config.py` document the current configuration.

2. **Validation before Deployment** – Proposed changes must be
   evaluated via the Phase 3 validation harness, including a
   walk‑forward test and an out‑of‑sample holdout.  Only
   configurations that improve key metrics (expectancy, profit factor,
   drawdown control) without violating constraints should be advanced.

3. **Forward‑Test Trial** – After Phase 3 validation, new rules must
   be run in shadow mode for a predetermined forward‑testing period.
   The forward‑test runs are recorded under `artifacts/forward_test`
   and can be inspected via the `forwardtest` CLI.  Trades must not
   be executed during this trial.

4. **Review and Approval** – At the conclusion of the forward‑test
   period the results are reviewed against the existing live policy.
   Outcomes (wins, losses, rule efficacy) are compared to the baseline
   to determine whether the change should be promoted to live mode.

5. **Documentation** – All changes and their rationale must be
   documented.  Update this governance document and the runbook with
   the new version, decision logic and validation results.

These guidelines help maintain discipline in strategy evolution and
reduce the risk of overfitting or inadvertent live deployment of
untested rules.