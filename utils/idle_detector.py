"""Explainable rule-based idle-resource detection for AWS-style resource data.

Supports three cost-data scenarios:
  1. Full CUR data: ``lineItem/UnblendedCost`` column present with real values.
  2. Alias cost column: ``UnblendedCost``, ``cost``, or ``monthly_cost`` variants.
  3. Live AWS inventory (no CUR): cost column absent or all-NaN.

In scenario 3, idle detection still works based on resource utilisation and state.
Cost values are reported as NaN (not zero) so callers can distinguish "free" from
"cost unavailable".  The ``cost_data_available`` metadata column is added so the UI
can surface a clear warning to the user.
"""
from __future__ import annotations

import logging
import warnings as _warnings

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

DEFAULT_THRESHOLDS = {
    "ec2_cpu_p95_pct": 5.0,
    "ec2_min_days_observed": 7,
}

# Ordered list of column names to try when looking for cost data.
_COST_COLUMN_CANDIDATES = [
    "lineItem/UnblendedCost",
    "UnblendedCost",
    "unblended_cost",
    "cost",
    "Cost",
    "monthly_cost",
    "unblended_cost_usd",
]

# Required columns for the groupby aggregation; mapped to sensible defaults when absent.
_REQUIRED_COLUMNS: dict[str, object] = {
    "lineItem/ResourceId": "",
    "lineItem/UsageAccountId": "000000000000",
    "product/servicecode": "",
    "product/region": "",
    "product/instanceType": "",
    "resource_state": "unknown",
    "identity/TimeInterval": "",
    "cpu_utilization_avg": np.nan,
    "cpu_utilization_p95": np.nan,
    "network_usage_gb": np.nan,
    "size_gb": 0,
    "resourceTags/user:Owner": "",
    "resourceTags/user:Environment": "",
    "resourceTags/user:Cost-Center": "",
}


def _resolve_cost_column(df: pd.DataFrame) -> tuple[str | None, bool]:
    """Return (column_name, has_real_values) for the best available cost column.

    Returns (None, False) when no cost column exists at all.
    Returns (col, False) when the column is present but entirely NaN (live-inventory
    snapshot with no CUR billing data attached).
    Returns (col, True) when real numeric values are present.
    """
    for candidate in _COST_COLUMN_CANDIDATES:
        if candidate in df.columns:
            numeric = pd.to_numeric(df[candidate], errors="coerce")
            has_real = bool(numeric.notna().any() and (numeric.dropna() != 0).any())
            return candidate, has_real
    return None, False


def _safe_prepare(df: pd.DataFrame) -> tuple[pd.DataFrame, bool, list[str]]:
    """Defensive copy + column-existence checks before the aggregation.

    Returns:
        (prepared_df, cost_data_available, warning_messages)
    """
    diag: list[str] = []

    if df.empty:
        # Return a minimal empty frame with all required columns so the caller
        # doesn't crash on a KeyError in the groupby.
        empty = pd.DataFrame(columns=list(_REQUIRED_COLUMNS.keys()) + ["_cost_col"])
        return empty, False, ["Input DataFrame is empty; no resources to analyse."]

    out = df.copy()

    # Inject any missing required columns with their defaults.
    for col, default in _REQUIRED_COLUMNS.items():
        if col not in out.columns:
            diag.append(
                f"Column '{col}' not found in input DataFrame; "
                f"defaulting to {default!r}.  "
                f"Present columns: {list(out.columns)}"
            )
            out[col] = default

    # Resolve cost column and add a unified _cost_col.
    cost_col, has_real_cost = _resolve_cost_column(out)
    if cost_col is None:
        diag.append(
            "No cost column found in the input DataFrame "
            f"(tried: {_COST_COLUMN_CANDIDATES}).  "
            "Idle detection will proceed using resource state and utilisation only.  "
            "Waste USD values will be reported as NaN."
        )
        out["_cost_col"] = np.nan
    else:
        numeric_cost = pd.to_numeric(out[cost_col], errors="coerce")
        out["_cost_col"] = numeric_cost
        if not has_real_cost:
            diag.append(
                f"Cost column '{cost_col}' is present but contains no real values "
                "(all NaN or zero — this is expected for live AWS inventory data "
                "without a Cost and Usage Report).  "
                "Waste USD values will be reported as NaN.  "
                "Idle detection continues using resource state and utilisation."
            )

    for msg in diag:
        logger.warning("idle_detector: %s", msg)

    return out, has_real_cost, diag


def _resource_summary(df: pd.DataFrame) -> tuple[pd.DataFrame, bool, list[str]]:
    """Aggregate per-resource metrics safely.

    Returns:
        (summary_df, cost_data_available, warnings)
    """
    prepared, has_real_cost, diag = _safe_prepare(df)

    if prepared.empty:
        empty_cols = [
            "lineItem/ResourceId", "lineItem/UsageAccountId",
            "resource_type", "region", "instance_type", "resource_state",
            "monthly_cost", "cost_data_available", "observed_days",
            "cpu_p95", "avg_cpu", "network_gb", "owner", "environment",
            "cost_center", "size_gb",
        ]
        return pd.DataFrame(columns=empty_cols), False, diag

    grouped = prepared.groupby(
        ["lineItem/ResourceId", "lineItem/UsageAccountId"], dropna=False
    )

    summary = grouped.agg(
        resource_type=("product/servicecode", "first"),
        region=("product/region", "first"),
        instance_type=("product/instanceType", "first"),
        resource_state=("resource_state", "last"),
        monthly_cost=("_cost_col", "sum"),
        observed_days=("identity/TimeInterval", "nunique"),
        cpu_p95=(
            "cpu_utilization_avg",
            lambda s: s.dropna().quantile(0.95) if s.notna().any() else float("nan"),
        ),
        avg_cpu=("cpu_utilization_avg", "mean"),
        network_gb=("network_usage_gb", "sum"),
        owner=("resourceTags/user:Owner", "first"),
        environment=("resourceTags/user:Environment", "first"),
        cost_center=("resourceTags/user:Cost-Center", "first"),
        size_gb=("size_gb", "max"),
    ).reset_index()

    # When cost is unavailable, replace 0.0 sum (from NaN-only groups) with NaN
    # so callers can distinguish "genuinely free" from "cost not known".
    if not has_real_cost:
        summary["monthly_cost"] = np.nan

    summary["cost_data_available"] = bool(has_real_cost)
    return summary, bool(has_real_cost), diag


def detect_idle_resources(
    df: pd.DataFrame,
    ec2_cpu_p95_threshold: float = 5.0,
    min_days_observed: int = 7,
) -> pd.DataFrame:
    """Return all resources that match idle-resource rules.

    Works correctly when:
    - ``df`` contains full CUR data with real ``lineItem/UnblendedCost`` values.
    - ``df`` is a live AWS inventory snapshot without any cost column.
    - ``df`` is empty.
    - Required resource-identifier or utilisation columns are absent.

    When cost data is unavailable, ``waste_usd`` is set to NaN (not zero) and
    ``cost_data_available`` is False so dashboards can show an appropriate warning.
    """
    if not isinstance(df, pd.DataFrame):
        logger.error("detect_idle_resources: expected a DataFrame, got %s", type(df))
        return pd.DataFrame()

    s, has_real_cost, diag = _resource_summary(df)

    if s.empty:
        base_cols = list(s.columns) + ["finding_type", "rule", "waste_usd"]
        return pd.DataFrame(columns=base_cols)

    results = []

    # --- Idle EC2 ---
    ec2_mask = (
        (s["resource_type"] == "EC2")
        & (s["resource_state"] == "running")
        & (s["observed_days"] >= min_days_observed)
        & (s["cpu_p95"].fillna(100) < ec2_cpu_p95_threshold)
    )
    ec2 = s[ec2_mask].copy()
    if not ec2.empty:
        ec2["finding_type"] = "Idle EC2"
        ec2["rule"] = (
            f"p95 CPU < {ec2_cpu_p95_threshold:.1f}% "
            f"for >= {min_days_observed} observed days"
        )
        ec2["waste_usd"] = ec2["monthly_cost"]   # NaN when cost unavailable
        results.append(ec2)

    # --- Unattached EBS ---
    ebs_mask = (s["resource_type"] == "EBS") & (s["resource_state"] == "available")
    ebs = s[ebs_mask].copy()
    if not ebs.empty:
        ebs["finding_type"] = "Unattached EBS"
        ebs["rule"] = "EBS state = available"
        ebs["waste_usd"] = ebs["monthly_cost"]
        results.append(ebs)

    # --- Unused Elastic IP ---
    eip_mask = (
        (s["resource_type"] == "ElasticIP")
        & (s["resource_state"] == "unassociated")
    )
    eip = s[eip_mask].copy()
    if not eip.empty:
        eip["finding_type"] = "Unused Elastic IP"
        eip["rule"] = "Elastic IP is unassociated"
        eip["waste_usd"] = eip["monthly_cost"]
        results.append(eip)

    if not results:
        base_cols = list(s.columns) + ["finding_type", "rule", "waste_usd"]
        return pd.DataFrame(columns=base_cols)

    combined = pd.concat(results, ignore_index=True)
    # Sort by waste_usd descending; NaN rows go last.
    combined = combined.sort_values("waste_usd", ascending=False, na_position="last")
    return combined


def benchmark_idle_detector(findings: pd.DataFrame, source_df: pd.DataFrame) -> pd.DataFrame:
    """Compare detected idle findings with generator ground truth by resource."""
    ids = source_df.groupby("lineItem/ResourceId").agg(
        gt_idle_ec2=("ground_truth_idle_ec2", "max"),
        gt_idle_ebs=("ground_truth_idle_ebs", "max"),
        gt_unused_eip=("ground_truth_unused_eip", "max"),
    ).reset_index()
    detected = set(findings["lineItem/ResourceId"].astype(str)) if not findings.empty else set()
    rows = []
    for _, r in ids.iterrows():
        rid = str(r["lineItem/ResourceId"])
        ground_truth = bool(r.gt_idle_ec2 or r.gt_idle_ebs or r.gt_unused_eip)
        rows.append({"resource_id": rid, "ground_truth": ground_truth, "detected": rid in detected})
    out = pd.DataFrame(rows)
    return out
