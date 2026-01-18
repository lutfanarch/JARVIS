"""Phase 3 validation and parameter sweep helpers.

This module provides utilities to perform deterministic grid searches
over backtest parameters, execute walk‑forward validation folds and
report aggregated statistics.  All functions operate purely on
in‑memory bar data; no database or network access occurs within
these helpers.  Consumers are responsible for loading bar data and
providing appropriate ``BacktestConfig`` instances.

The design prioritizes reproducibility: parameter grids are iterated
in a stable order, tie‑breaks use lexicographically sorted JSON
representations of parameter dictionaries and trading day lists are
derived deterministically from ``splits.trading_days``.

Note: The implementation is intentionally minimal to support the test
coverage in Phase 3; it is not optimized for performance or large
datasets.
"""

from __future__ import annotations

import json
from dataclasses import replace
from itertools import product
from typing import Dict, List, Any, Tuple, Optional
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from .strategy import BacktestConfig
from .engine import BacktestEngine, BacktestResult
from .metrics import compute_summary, compute_regime_breakdown, Trade
from .splits import trading_days


def generate_param_grid(param_spec: Dict[str, List[Any]]) -> List[Dict[str, Any]]:
    """Generate a deterministic list of parameter combinations.

    Parameters
    ----------
    param_spec : dict
        Mapping of parameter names to lists of candidate values.  The
        keys are sorted alphabetically to ensure stable ordering.

    Returns
    -------
    list of dict
        Each dict contains a complete assignment of parameter values.
    """
    if not param_spec:
        return [{}]
    keys = sorted(param_spec.keys())
    values_list = [param_spec[k] for k in keys]
    combos: List[Dict[str, Any]] = []
    for values in product(*values_list):
        combo = {k: values[i] for i, k in enumerate(keys)}
        combos.append(combo)
    return combos


def _filter_bars_by_date_range(
    bars_map: Dict[str, List[Dict[str, Any]]],
    start_date: date,
    end_date: date,
    decision_tz: str,
) -> Dict[str, List[Dict[str, Any]]]:
    """Filter bars_map by end date only, preserving warmup bars.

    Bars are assumed to have timezone‑aware ``ts`` timestamps in UTC.  They
    are converted to the local timezone specified by ``decision_tz``.  All
    bars with a local date **no later than** ``end_date`` are included.  The
    ``start_date`` parameter is accepted for compatibility but ignored to
    allow warmup bars from earlier dates to remain available for indicator
    initialization.  No bars after ``end_date`` are returned to avoid
    look‑ahead bias.
    """
    zone = ZoneInfo(decision_tz)
    new_map: Dict[str, List[Dict[str, Any]]] = {}
    for sym, bars in bars_map.items():
        filtered: List[Dict[str, Any]] = []
        for b in bars:
            ts = b.get("ts")
            if not ts:
                continue
            local_date = ts.astimezone(zone).date()
            # Only apply upper bound; keep all bars up to end_date inclusive
            if local_date <= end_date:
                filtered.append(b)
        new_map[sym] = filtered
    return new_map


def run_backtest_for_params(
    bars_map: Dict[str, List[Dict[str, Any]]],
    base_cfg: BacktestConfig,
    overrides: Dict[str, Any],
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> Tuple[BacktestResult, Dict[str, Any]]:
    """Run a backtest with parameter overrides and return result and metrics.

    Parameters
    ----------
    bars_map : dict
        Mapping from symbol to list of bar dictionaries (UTC timestamps).
    base_cfg : BacktestConfig
        Base configuration to clone and override.  The cloned config
        will have its start and end dates replaced if provided.
    overrides : dict
        Parameter overrides (e.g., ``{"k_stop": 1.5}``).  Keys not
        present on ``BacktestConfig`` are ignored.
    start_date, end_date : date or None
        Optional start and end dates for this run.  If not provided,
        the values from ``base_cfg`` are used.

    Returns
    -------
    (BacktestResult, dict)
        The backtest result and a metrics dictionary as returned by
        :func:`compute_summary`.
    """
    # Clone the base configuration and apply overrides
    cfg_kwargs = base_cfg.__dict__.copy()
    cfg_kwargs.update(overrides)
    if start_date is not None:
        cfg_kwargs["start_date"] = start_date
    if end_date is not None:
        cfg_kwargs["end_date"] = end_date
    # Create new BacktestConfig instance.  extra_params are passed through.
    cfg = BacktestConfig(**cfg_kwargs)
    # Filter bars to date range to avoid look‑ahead
    bars_filtered = _filter_bars_by_date_range(bars_map, cfg.start_date, cfg.end_date, cfg.decision_tz)
    # Run engine
    engine = BacktestEngine(config=cfg)
    result = engine.run(bars_filtered)
    # Compute metrics summary (already includes extended metrics)
    metrics = result.summary.copy()
    return result, metrics


def run_parameter_sweep(
    bars_map: Dict[str, List[Dict[str, Any]]],
    base_cfg: BacktestConfig,
    param_spec: Dict[str, List[Any]],
    objective: str,
    start_date: date,
    end_date: date,
    top_n: int = 10,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any], BacktestResult]:
    """Run a deterministic grid search over the parameter space.

    Parameters
    ----------
    bars_map : dict
        Bar data mapped by symbol.
    base_cfg : BacktestConfig
        Base configuration to use for all runs; start_date and end_date
        parameters will be replaced by the supplied values.
    param_spec : dict
        Mapping of parameter names to lists of candidate values.
    objective : str
        Metric name to optimize.  For ``max_drawdown`` and
        ``max_drawdown_pct`` the objective is minimized; all other
        objectives are maximized.
    start_date, end_date : date
        Date range for all runs.
    top_n : int
        Number of top results to return in the result list.  The
        result list will still be fully sorted by objective value.

    Returns
    -------
    (list, dict, BacktestResult)
        A list of results (each containing params and metrics), the
        best result's params and metrics dictionary, and the
        corresponding ``BacktestResult`` instance for the best run.
    """
    grid = generate_param_grid(param_spec)
    results: List[Dict[str, Any]] = []
    best_entry = None
    # Determine whether objective should be minimized
    minimize = objective in {"max_drawdown", "max_drawdown_pct"}
    for params in grid:
        # Run backtest for this parameter set
        result, metrics = run_backtest_for_params(
            bars_map,
            base_cfg,
            params,
            start_date=start_date,
            end_date=end_date,
        )
        obj_val = metrics.get(objective)
        # For None objective values (e.g., profit_factor when infinite),
        # treat as positive infinity for maximization and negative infinity for minimization
        if obj_val is None:
            obj_sort_val = float("inf") if not minimize else float("-inf")
        else:
            obj_sort_val = obj_val
        entry = {
            "params": params,
            "metrics": metrics,
            "objective_value": obj_val,
            "_sort_val": obj_sort_val,
            "_result": result,
        }
        results.append(entry)
        # Track best entry deterministically
        if best_entry is None:
            best_entry = entry
        else:
            # Compare objective values; break ties via lexicographically sorted JSON of params
            if minimize:
                better = entry["_sort_val"] < best_entry["_sort_val"]
            else:
                better = entry["_sort_val"] > best_entry["_sort_val"]
            if better:
                best_entry = entry
            elif entry["_sort_val"] == best_entry["_sort_val"]:
                # Tie – compare sorted JSON strings
                current_json = json.dumps(entry["params"], sort_keys=True)
                best_json = json.dumps(best_entry["params"], sort_keys=True)
                if current_json < best_json:
                    best_entry = entry
    # Sort results by objective (desc or asc) and tie-break by JSON
    def sort_key(e: Dict[str, Any]) -> Tuple:
        return (
            e["_sort_val"],
            json.dumps(e["params"], sort_keys=True),
        )
    results_sorted = sorted(results, key=sort_key, reverse=not minimize)
    # Trim the list if top_n specified and smaller than total
    top_results = results_sorted[:top_n] if top_n and top_n < len(results_sorted) else results_sorted
    best_params = best_entry["params"] if best_entry else {}
    best_metrics = best_entry["metrics"] if best_entry else {}
    best_result = best_entry["_result"] if best_entry else None
    # Remove internal keys before returning
    cleaned_results = []
    for e in top_results:
        cleaned_results.append({
            "params": e["params"],
            "metrics": e["metrics"],
            "objective_value": e["objective_value"],
        })
    return cleaned_results, {"params": best_params, "metrics": best_metrics}, best_result


def run_walkforward(
    bars_map: Dict[str, List[Dict[str, Any]]],
    base_cfg: BacktestConfig,
    start_date: date,
    end_date: date,
    train_days: int,
    test_days: int,
    param_spec: Dict[str, List[Any]],
    objective: str,
    step_days: Optional[int] = None,
    holdout_start: Optional[date] = None,
    holdout_days: Optional[int] = None,
) -> Dict[str, Any]:
    """Run a walk‑forward validation over sequential trading day folds.

    Parameters
    ----------
    bars_map : dict
        Bar data per symbol.
    base_cfg : BacktestConfig
        Base configuration containing symbols and risk settings.
    start_date, end_date : date
        Global evaluation window.  Folds that exceed end_date are skipped.
    train_days : int
        Number of trading days in the training window for each fold.
    test_days : int
        Number of trading days in the test window for each fold.
    param_spec : dict
        Parameter grid specification.
    objective : str
        Name of the metric to optimize during the sweep.
    step_days : int or None
        Optional stride between fold start dates.  Defaults to test_days.
    holdout_start : date or None
        If provided, all days on or after this date are treated as holdout
        and are excluded from parameter selection.
    holdout_days : int or None
        Length of the holdout period (in trading days) if holdout_start
        is not supplied.

    Returns
    -------
    dict
        A dictionary containing fold rows, combined OOS trades and summary,
        and optional holdout trades and summary.
    """
    # Determine trading days between start and end
    day_list = trading_days(start_date, end_date)
    step = step_days if step_days is not None else test_days
    fold_rows: List[Dict[str, Any]] = []
    oos_trades: List[Trade] = []
    oos_start_idx = 0
    fold_id = 0
    total_days = len(day_list)
    idx = 0
    # Determine end index for holdout period if holdout_start or holdout_days provided
    holdout_start_idx: Optional[int] = None
    holdout_end_idx: Optional[int] = None
    if holdout_start:
        # Find index of the trading day matching holdout_start
        for i, d in enumerate(day_list):
            if d >= holdout_start:
                holdout_start_idx = i
                break
    elif holdout_days:
        # holdout period starts after the last fold.  Compute number of non-holdout days
        if holdout_days > 0 and total_days - holdout_days > 0:
            holdout_start_idx = total_days - holdout_days
    # Determine end index of holdout
    if holdout_start_idx is not None:
        holdout_end_idx = total_days - 1
    # Process folds until there is no room for a full test window or we reach holdout
    while idx < total_days:
        train_start_idx = idx
        train_end_idx = train_start_idx + train_days - 1
        test_start_idx = train_end_idx + 1
        test_end_idx = test_start_idx + test_days - 1
        # Stop if test window exceeds available days or crosses into holdout
        if test_end_idx >= total_days:
            break
        if holdout_start_idx is not None:
            # Stop if any part of the fold intersects the holdout period
            # Prevent parameter selection or test evaluation overlapping holdout
            if (
                train_end_idx >= holdout_start_idx
                or test_start_idx >= holdout_start_idx
                or test_end_idx >= holdout_start_idx
            ):
                break
        train_start = day_list[train_start_idx]
        train_end = day_list[train_end_idx]
        test_start = day_list[test_start_idx]
        test_end = day_list[test_end_idx]
        # Run sweep on training window to select best params
        _, best_info, _ = run_parameter_sweep(
            bars_map,
            base_cfg,
            param_spec,
            objective,
            start_date=train_start,
            end_date=train_end,
            top_n=0,
        )
        best_params = best_info.get("params", {})
        best_metrics_train = best_info.get("metrics", {})
        # Run backtest on test window with selected params
        test_result, test_metrics = run_backtest_for_params(
            bars_map,
            base_cfg,
            best_params,
            start_date=test_start,
            end_date=test_end,
        )
        # Append OOS trades
        oos_trades.extend(test_result.trades)
        # Record fold row
        fold_rows.append(
            {
                "fold_id": fold_id,
                "train_start": train_start.isoformat(),
                "train_end": train_end.isoformat(),
                "test_start": test_start.isoformat(),
                "test_end": test_end.isoformat(),
                "params": best_params,
                "train_objective": best_metrics_train.get(objective),
                "test_metrics": test_metrics,
            }
        )
        fold_id += 1
        idx += step
    # Compute OOS summary and regime breakdown
    oos_curve = []
    oos_summary: Dict[str, Any] = {}
    holdout_trades: List[Trade] = []
    holdout_summary: Dict[str, Any] = {}
    if oos_trades:
        # Build curve over the entire evaluation date range for OOS trades
        from .metrics import equity_curve
        date_strings = [d.isoformat() for d in day_list]
        oos_curve = equity_curve(oos_trades, base_cfg.initial_cash, date_strings)
        oos_summary = compute_summary(oos_trades, oos_curve)
        # Include regime breakdown in summary
        oos_summary["regime_breakdown"] = compute_regime_breakdown(oos_trades)
    else:
        # No OOS trades: build curve over the entire evaluation date range and compute summary
        from .metrics import equity_curve
        date_strings = [d.isoformat() for d in day_list]
        oos_curve = equity_curve([], base_cfg.initial_cash, date_strings)
        oos_summary = compute_summary([], oos_curve)
        oos_summary["regime_breakdown"] = compute_regime_breakdown([])
    # Handle holdout if configured
    if holdout_start_idx is not None:
        h_start_idx = holdout_start_idx
        h_end_idx = holdout_end_idx if holdout_end_idx is not None else total_days - 1
        # Determine holdout dates
        h_start = day_list[h_start_idx]
        h_end = day_list[h_end_idx]
        # Select best params using all data before holdout
        if h_start_idx > 0:
            in_sample_start = day_list[0]
            in_sample_end = day_list[h_start_idx - 1]
            _, best_info, _ = run_parameter_sweep(
                bars_map,
                base_cfg,
                param_spec,
                objective,
                start_date=in_sample_start,
                end_date=in_sample_end,
                top_n=0,
            )
            best_params_holdout = best_info.get("params", {})
        else:
            best_params_holdout = {}
        # Run backtest on holdout period
        holdout_result, holdout_metrics = run_backtest_for_params(
            bars_map,
            base_cfg,
            best_params_holdout,
            start_date=h_start,
            end_date=h_end,
        )
        holdout_trades = holdout_result.trades
        # Build curve for holdout
        if holdout_trades:
            from .metrics import equity_curve
            date_strings_h = [d.isoformat() for d in trading_days(h_start, h_end)]
            h_curve = equity_curve(holdout_trades, base_cfg.initial_cash, date_strings_h)
            holdout_summary = compute_summary(holdout_trades, h_curve)
            holdout_summary["regime_breakdown"] = compute_regime_breakdown(holdout_trades)
        else:
            # No holdout trades but holdout enabled: build curve over holdout period and compute summary
            from .metrics import equity_curve
            date_strings_h = [d.isoformat() for d in trading_days(h_start, h_end)]
            h_curve = equity_curve([], base_cfg.initial_cash, date_strings_h)
            holdout_summary = compute_summary([], h_curve)
            holdout_summary["regime_breakdown"] = compute_regime_breakdown([])
    return {
        "folds": fold_rows,
        "oos_trades": oos_trades,
        "oos_summary": oos_summary,
        "holdout_trades": holdout_trades,
        "holdout_summary": holdout_summary,
    }