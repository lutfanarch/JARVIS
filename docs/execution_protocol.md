# Execution Protocol

This document outlines the mechanical steps for placing and managing
trades identified by JARVIS.  Adherence to this protocol ensures
consistent execution and risk control.

## Entry

1. **Confirm Decision** – Review the decision artefact in
   `artifacts/decisions/<run_id>.json`.  Verify the `symbol`,
   `entry`, `stop` and `targets` fields.  Ensure the rationale and
   confidence align with your risk tolerance.

2. **Place Order** – Enter a long position using the specified
   `entry` price and `shares` count.  Only whole shares are allowed;
   do not scale up beyond the recommended risk.

3. **Record Trade** – Log the order details, including timestamp,
   price and order identifier.  For forward‑test runs use
   `jarvis forwardtest log-outcome` once the trade is completed.

## Management

1. **Stop Loss** – Place a stop order at the `stop` price
   immediately after entry.  Do not widen the stop under any
   circumstances.

2. **Targets** – Optionally set limit orders at each target price.
   Partial exits are allowed but must respect the overall risk limit.

3. **Daily Review** – At the end of each trading day verify that the
   position has been closed (EOD flat rule).  If the trade is still
   open, exit at the market price before the close.

## Logging Outcomes

Use the `jarvis forwardtest log-outcome` command to record realised entry
and exit prices for forward‑tested trades.  This data contributes to
post‑mortem analysis and strategy improvement.  Provide any
observations or execution notes via the `--notes` option.  You may
also specify `--duration-seconds` to log the time you held the
position (in seconds).  Logging the duration enables the evaluation
status command to distinguish **valid** profits—those that meet the
minimum hold time and profit-per-share rules—from invalid or
unknown profits.  When the duration is omitted, profitable
outcomes are considered to have unknown validity and are excluded
from the valid profit tally.