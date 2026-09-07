"""Explainable rule-based idle-resource detection for AWS-style resource data."""
from __future__ import annotations

import pandas as pd

DEFAULT_THRESHOLDS = {
    "ec2_cpu_p95_pct": 5.0,
    "ec2_min_days_observed": 7,
}


def _resource_summary(df: pd.DataFrame) -> pd.DataFrame:
    x = df.copy()
    x["cost"] = pd.to_numeric(x["lineItem/UnblendedCost"], errors="coerce").fillna(0.0)
    grouped = x.groupby(["lineItem/ResourceId", "lineItem/UsageAccountId"], dropna=False)
    summary = grouped.agg(
        resource_type=("product/servicecode", "first"),
        region=("product/region", "first"),
        instance_type=("product/instanceType", "first"),
        resource_state=("resource_state", "last"),
        monthly_cost=("cost", "sum"),
        observed_days=("identity/TimeInterval", "nunique"),
        cpu_p95=("cpu_utilization_avg", lambda s: s.dropna().quantile(0.95) if s.notna().any() else float("nan")),
        avg_cpu=("cpu_utilization_avg", "mean"),
        network_gb=("network_usage_gb", "sum"),
        owner=("resourceTags/user:Owner", "first"),
        environment=("resourceTags/user:Environment", "first"),
        cost_center=("resourceTags/user:Cost-Center", "first"),
        size_gb=("size_gb", "max"),
    ).reset_index()
    return summary


def detect_idle_resources(
    df: pd.DataFrame,
    ec2_cpu_p95_threshold: float = 5.0,
    min_days_observed: int = 7,
) -> pd.DataFrame:
    """Return all resources that match the idle-resource rules."""
    s = _resource_summary(df)
    results = []

    ec2 = s[
        (s["resource_type"] == "EC2")
        & (s["resource_state"] == "running")
        & (s["observed_days"] >= min_days_observed)
        & (s["cpu_p95"].fillna(100) < ec2_cpu_p95_threshold)
    ].copy()
    ec2["finding_type"] = "Idle EC2"
    ec2["rule"] = f"p95 CPU < {ec2_cpu_p95_threshold:.1f}% for >= {min_days_observed} observed days"
    ec2["waste_usd"] = ec2["monthly_cost"]
    results.append(ec2)

    ebs = s[(s["resource_type"] == "EBS") & (s["resource_state"] == "available")].copy()
    ebs["finding_type"] = "Unattached EBS"
    ebs["rule"] = "EBS state = available"
    ebs["waste_usd"] = ebs["monthly_cost"]
    results.append(ebs)

    eip = s[(s["resource_type"] == "ElasticIP") & (s["resource_state"] == "unassociated")].copy()
    eip["finding_type"] = "Unused Elastic IP"
    eip["rule"] = "Elastic IP is unassociated"
    eip["waste_usd"] = eip["monthly_cost"]
    results.append(eip)

    if not results:
        return pd.DataFrame(columns=list(s.columns) + ["finding_type", "rule", "waste_usd"])
    return pd.concat(results, ignore_index=True).sort_values("waste_usd", ascending=False)


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
