# Proprietary Trading Firm Profiles

JARVIS includes optional support for sizing trades according to the
rules of specific proprietary trading firm evaluation programs.  When
enabled, these profiles constrain the risk per trade, enforce minimum
profit per share and cap the maximum realised profit on a position.
Violations fail closed: a proposed trade is vetoed into a `NO_TRADE`
decision with a `PROP_RULE_VIOLATION` reason code and the offending
condition recorded in the decision’s `audit` section.  When a trade
passes the gates, the validator attaches a `prop` block to the
decision summarising the active profile and budgets.

## Enabling a Profile

To activate prop firm gates, set the `PROP_PROFILE` environment
variable to the name of a supported profile before running the
decision pipeline.  Only recognised names have any effect; if
`PROP_PROFILE` is unset or refers to an unknown profile the validator
behaves as in previous versions and does not apply these gates.  At
present the following profile is available:

| Name | Account Size | Per‑Trade Risk | Daily Pause | Max Loss | Profit Target | Min Profit/Share | Min Hold | Profit Cap |
|------|--------------|---------------|------------|----------|---------------|-----------------|----------|------------|
| `trade_the_pool_25k_beginner` | $25,000 | **0.20% ($50)** | 2.0% | 4.0% | 6.0% | $0.10 | 30 s | 1.5% ($375) |

Set `PROP_PROFILE=trade_the_pool_25k_beginner` to size and vet
trades according to the rules of Trade The Pool’s \$25k beginner
evaluation.  The risk budgets and caps are deterministic and derive
directly from the account size; for example the per‑trade risk budget
is **0.20%** of \$25,000, or **\$50**.

## Enforced Defaults

When `PROP_PROFILE` is active, the validator applies the following
checks on each proposed trade:

* **Risk budget capping** – the number of shares is sized using the
  per‑trade risk budget.  If a larger `max_risk_usd` is supplied via
  CLI or environment variable it is capped to the profile’s risk
  budget.  For the TTP 25k profile the budget is **\$50**.  Trades
  that cannot be sized within the risk budget (e.g. because the
  stop is too tight) are vetoed as before.

* **Cash‑only share sizing** – share counts are capped by the
  available cash balance.  When `cash_usd` is omitted, the validator
  uses the full account size to compute the maximum number of whole
  shares.  Only whole (integer) shares are allowed; fractional or
  margin sizing is not permitted.  This prevents oversizing and
  ensures positions fit within the account value.

* **Minimum profit per share** – all take‑profit levels must offer at
  least the configured minimum per‑share profit; otherwise the trade
  is rejected.  For the TTP profile this threshold is \$0.10 per
  share.

* **Profit cap** – the total profit implied by the furthest
  take‑profit level is capped.  When the profit exceeds the cap the
  validator reduces the outermost target to fit within the cap or
  drops it entirely if necessary.  The stop price is never changed.
  For the TTP profile the cap is 1.5% of \$25,000, or \$375.

* **Default take‑profit multiple** – some profiles define a default
  take‑profit in risk units.  When ``default_take_profit_r`` is
  non‑``None`` the validator ignores the LLM‑proposed targets and
  sets the primary target to ``entry + default_take_profit_r * (entry - stop)``.
  The R multiple in the final decision is updated accordingly.  For
  the TTP 25k beginner profile the default take‑profit is **1.5R**, so
  each trade aims for a 1.5× risk reward before any profit cap
  enforcement.

Trades that pass these gates remain valid and include a `prop` block
in the final decision with the following fields:

```json
{
  "prop_profile_name": "trade_the_pool_25k_beginner",
  "risk_budget_usd": 50.0,
  "profit_cap_usd": 375.0,
  "min_profit_per_share_usd": 0.10,
  "min_trade_duration_seconds": 30
}
```

If a take‑profit level is adjusted due to the profit cap, the
original and adjusted targets are recorded under the `audit` key as
`prop_target_adjustment`.

## Configuration Missing or Unknown Profiles

If `PROP_PROFILE` is set to an unknown name the validator simply
ignores it and processes trades without any prop firm gates.  Missing
configuration does not abort the pipeline; it is treated as if no
profile was selected.

## Additional Notes

* The daily risk cap and maximum loss cap defined in the profile are
  currently advisory only; JARVIS does not aggregate risk across
  trades in a day or evaluation.  Future phases may extend the
  scheduler or state tracking to enforce these budgets.

* The minimum trade duration (30 seconds for TTP) is an execution
  note.  JARVIS does not currently monitor or enforce hold times.

* These gates are deterministic and do not depend on any API
  credentials.  They can be exercised offline using `LLM_MODE=fake`.

## Evaluation Status

Once trades have been recorded in forward‑test mode and realised outcomes
have been logged via `forwardtest-log-outcome`, you can monitor your
progress towards passing the evaluation using the `jarvis prop
eval-status` command.  This operator‑facing CLI reads the
forward‑test registry and outcomes logs, aggregates your realised
P&L and highlights potential rule risks such as concentration,
drawdown and daily loss breaches.

### Usage

```
jarvis prop eval-status [--profile PROFILE] [--start YYYY-MM-DD] [--out PATH]
```

* **`--profile`** – Name of the prop firm profile to evaluate.  When
  omitted, the active profile is taken from the `PROP_PROFILE`
  environment variable.  For example, to evaluate a Trade The Pool
  25k beginner account, pass `--profile trade_the_pool_25k_beginner` or
  set `PROP_PROFILE=trade_the_pool_25k_beginner`.

* **`--start`** – Optional start date (YYYY‑MM‑DD in New York) for
  including runs and outcomes.  If not supplied, the earliest
  recorded run or outcome date is used.  This allows you to
  evaluate progress from a specific point in time.

* **`--out`** – Optional path to write the report as a JSON file.  When
  specified, the command writes a deterministic JSON report to the
  given location instead of just printing the summary to the
  console.  The JSON contains all computed metrics and warnings with
  stable key ordering for reproducibility.

### Metrics and Warnings

The evaluation status report includes:

| Metric | Description |
|---|---|
| **`progress_to_target_usd`** | The total realised net P&L since the start date (sum of wins and losses).  This value drives progress towards the profit target. |
| **`progress_to_target_pct`** | Progress towards the profit target, computed as net realised P&L divided by the profile’s profit target (e.g. 6% of account size for Trade The Pool). |
| **`realised_total_pnl_usd`** | Same as `progress_to_target_usd`; included for clarity. |
| **`realised_best_trade_pnl_usd`** | The P&L of the single most profitable trade (zero when no realised profit). |
| **`best_trade_ratio`** | The ratio of the largest trade’s profit to total realised net profit.  Provided for information; concentration warnings are based on valid profit below. |
| **`positions_taken`** | Number of forward‑test runs in the date range with a `TRADE` decision.  This counts how many positions were planned. |
| **`outcomes_logged`** | Number of realised outcomes matched to those runs.  Not all planned trades may have a logged outcome yet. |
| **`valid_profit_usd`** | Sum of profits from trades that satisfy both the minimum profit per share and minimum duration rules of the active profile. |
| **`invalid_profit_usd`** | Sum of profits from trades that fail either rule (e.g. profit per share below $0.10 or duration below 30 s). |
| **`unknown_validity_profit_usd`** | Sum of profits from trades missing the `duration_seconds` field.  These are not counted as valid and trigger a warning. |
| **`best_trade_valid_profit_usd`** | The largest profit among valid trades.  Used to compute the valid profit concentration ratio. |
| **`best_trade_ratio_valid_profit`** | The ratio of the best valid trade’s profit to total valid profit.  A warning is raised if this exceeds the profile’s `max_position_profit_ratio` (30% for TTP). |
| **`max_drawdown_usd` / `max_drawdown_pct`** | The maximum drawdown of the equity curve across daily realised P&L.  A warning is raised when the drawdown percentage exceeds the profile’s `max_loss_pct` (4% for TTP). |
| **`daily_loss_violations`** | A list of dates where the realised daily loss exceeds the profile’s `daily_pause_pct` (2% for TTP).  These days are highlighted to caution the operator against further trading on those days. |

### Valid vs. Invalid Profit

Trade The Pool evaluates consistency by considering only **valid** profits.  A winning trade is counted as valid when:

* The profit per share is at least the profile’s `min_profit_per_share_usd` (e.g. \$0.10 for TTP), and
* The trade was held for at least the profile’s `min_trade_duration_seconds` (e.g. 30 seconds).

Profitable outcomes missing a `duration_seconds` field are treated as having **unknown** validity.  These are excluded from valid profit and reported separately.  Invalid profits occur when either the profit per share is too small or the duration is below the minimum.  To ensure your wins are counted as valid, supply the `--duration-seconds` option when logging the outcome via ``jarvis forwardtest log-outcome``.  Warnings are emitted when invalid or unknown profits are present to remind the operator that only valid profits count towards passing the evaluation.

These metrics and warnings help the operator adjust risk management and trade sizing to remain within the evaluation rules.  The command is deterministic and can be run repeatedly to track progress without side effects.

## References (Official)

The prop firm rules implemented in JARVIS are based on the official
documentation published by Trade The Pool.  Operators and
governance reviewers should consult these sources to understand the
rationale behind each gate:

- **Program Terms**: <https://tradethepool.com/program-terms/>
  – defines the core evaluation rules, including the
  profit target, maximum position profit ratio (30%) and overall
  drawdown limits.  The `max_position_profit_ratio` gate in JARVIS
  enforces this rule by warning when the best valid trade profit
  exceeds 30% of total valid profit.
- **The Program Overview**: <https://tradethepool.com/the-program/>
  – summarises account tiers and required trading range.  For the
  25k beginner account this table shows the minimum hold time
  (30 seconds) and the minimum trade range (≈10 cents per share).
  JARVIS encodes these as `min_trade_duration_seconds=30` and
  `min_profit_per_share_usd=0.10`.
- **Evaluation Guidance**: <https://tradethepool.com/fundamental/mastering-funded-trading-evaluation/>
  – provides additional context on how funded evaluations are scored
  and emphasises consistency.  The locked evaluation policy in
  JARVIS (1R = \$50 risk budget, default take‑profit of 1.5R and
  cash‑only sizing) follows these guidelines to encourage consistent
  results without oversized winners or speculative holds.

Each of the enforced defaults described above maps directly to a rule
or recommendation from these sources.  By referencing the official
documentation we ensure that the prop firm integration remains
transparent and aligned with Trade The Pool’s evaluation criteria.