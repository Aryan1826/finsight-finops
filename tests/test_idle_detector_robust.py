"""Tests for the idle detector — covering all scenarios from the requirements:

1. CUR input with ``lineItem/UnblendedCost``
2. Input with ``UnblendedCost`` alias
3. Input with normalised ``cost`` column
4. Input with no cost column (live AWS inventory)
5. Empty DataFrame
6. Missing resource-identifier columns
7. Missing CPU / utilisation data
8. Live AWS inventory data without CUR billing columns
9. Idle resources detected when costs are missing
10. ``waste_usd`` is NaN (not 0) when cost is unavailable
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.idle_detector import detect_idle_resources, _resolve_cost_column


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ec2_row(
    resource_id="i-abc",
    state="running",
    cpu_p95=1.0,
    cpu_avg=1.0,
    cost=50.0,
    cost_col="lineItem/UnblendedCost",
    time_interval="2026-01-01T00:00:00Z/2026-01-02T00:00:00Z",
) -> dict:
    row = {
        "lineItem/ResourceId": resource_id,
        "lineItem/UsageAccountId": "123456789012",
        "product/servicecode": "EC2",
        "product/region": "us-east-1",
        "product/instanceType": "t3.medium",
        "resource_state": state,
        "identity/TimeInterval": time_interval,
        "cpu_utilization_avg": cpu_avg,
        "cpu_utilization_p95": cpu_p95,
        "network_usage_gb": 0.1,
        "size_gb": 0,
        "resourceTags/user:Owner": "test",
        "resourceTags/user:Environment": "dev",
        "resourceTags/user:Cost-Center": "CC-1",
    }
    if cost_col is not None:
        row[cost_col] = cost
    return row


def _ebs_row(resource_id="vol-abc", state="available", cost_col="lineItem/UnblendedCost"):
    row = {
        "lineItem/ResourceId": resource_id,
        "lineItem/UsageAccountId": "123456789012",
        "product/servicecode": "EBS",
        "product/region": "us-east-1",
        "product/instanceType": "",
        "resource_state": state,
        "identity/TimeInterval": "2026-01-01T00:00:00Z/2026-01-02T00:00:00Z",
        "cpu_utilization_avg": np.nan,
        "cpu_utilization_p95": np.nan,
        "network_usage_gb": np.nan,
        "size_gb": 100,
        "resourceTags/user:Owner": "test",
        "resourceTags/user:Environment": "dev",
        "resourceTags/user:Cost-Center": "CC-1",
    }
    if cost_col is not None:
        row[cost_col] = 10.0
    return row


def _eip_row(resource_id="eipalloc-abc", state="unassociated", cost_col="lineItem/UnblendedCost"):
    row = {
        "lineItem/ResourceId": resource_id,
        "lineItem/UsageAccountId": "123456789012",
        "product/servicecode": "ElasticIP",
        "product/region": "us-east-1",
        "product/instanceType": "",
        "resource_state": state,
        "identity/TimeInterval": "2026-01-01T00:00:00Z/2026-01-02T00:00:00Z",
        "cpu_utilization_avg": np.nan,
        "cpu_utilization_p95": np.nan,
        "network_usage_gb": np.nan,
        "size_gb": 0,
        "resourceTags/user:Owner": "test",
        "resourceTags/user:Environment": "dev",
        "resourceTags/user:Cost-Center": "CC-1",
    }
    if cost_col is not None:
        row[cost_col] = 3.65
    return row


# ---------------------------------------------------------------------------
# 1. CUR input with lineItem/UnblendedCost
# ---------------------------------------------------------------------------

def test_cur_with_unblended_cost_detects_idle_ec2():
    rows = [
        _ec2_row("i-idle", state="running", cpu_p95=2.0, cpu_avg=2.0, cost=55.0),
        _ec2_row("i-busy", state="running", cpu_p95=80.0, cpu_avg=70.0, cost=120.0),
    ]
    df = pd.DataFrame(rows)
    findings = detect_idle_resources(df, ec2_cpu_p95_threshold=5.0, min_days_observed=1)

    idle_ec2 = findings[findings["finding_type"] == "Idle EC2"]
    assert len(idle_ec2) == 1
    assert idle_ec2.iloc[0]["lineItem/ResourceId"] == "i-idle"
    assert idle_ec2.iloc[0]["waste_usd"] == pytest.approx(55.0)
    assert bool(findings.iloc[0].get("cost_data_available", True)) is True


def test_cur_with_unblended_cost_detects_unattached_ebs():
    df = pd.DataFrame([_ebs_row("vol-free", state="available")])
    findings = detect_idle_resources(df)
    assert not findings.empty
    assert findings.iloc[0]["finding_type"] == "Unattached EBS"
    assert findings.iloc[0]["waste_usd"] == pytest.approx(10.0)


def test_cur_with_unblended_cost_detects_unused_eip():
    df = pd.DataFrame([_eip_row("eipalloc-unused", state="unassociated")])
    findings = detect_idle_resources(df)
    assert not findings.empty
    assert findings.iloc[0]["finding_type"] == "Unused Elastic IP"
    assert findings.iloc[0]["waste_usd"] == pytest.approx(3.65)


# ---------------------------------------------------------------------------
# 2. Input with UnblendedCost alias (not lineItem/...)
# ---------------------------------------------------------------------------

def test_alias_unblended_cost_column():
    rows = [_ec2_row("i-alias", state="running", cpu_p95=1.0, cost=40.0, cost_col="UnblendedCost")]
    df = pd.DataFrame(rows)
    findings = detect_idle_resources(df, ec2_cpu_p95_threshold=5.0, min_days_observed=1)
    idle = findings[findings["finding_type"] == "Idle EC2"]
    assert len(idle) == 1
    assert idle.iloc[0]["waste_usd"] == pytest.approx(40.0)


# ---------------------------------------------------------------------------
# 3. Input with normalised cost column
# ---------------------------------------------------------------------------

def test_normalised_cost_column():
    rows = [_ec2_row("i-cost", state="running", cpu_p95=1.0, cost=25.0, cost_col="cost")]
    df = pd.DataFrame(rows)
    findings = detect_idle_resources(df, ec2_cpu_p95_threshold=5.0, min_days_observed=1)
    idle = findings[findings["finding_type"] == "Idle EC2"]
    assert len(idle) == 1
    assert idle.iloc[0]["waste_usd"] == pytest.approx(25.0)


# ---------------------------------------------------------------------------
# 4. Input with no cost column (live AWS inventory)
# ---------------------------------------------------------------------------

def test_no_cost_column_does_not_crash():
    rows = [
        _ec2_row("i-nocost", state="running", cpu_p95=1.0, cost_col=None),
        _ebs_row("vol-nocost", state="available", cost_col=None),
        _eip_row("eip-nocost", state="unassociated", cost_col=None),
    ]
    df = pd.DataFrame(rows)
    findings = detect_idle_resources(df, ec2_cpu_p95_threshold=5.0, min_days_observed=1)

    assert not findings.empty
    assert set(findings["finding_type"]) == {"Idle EC2", "Unattached EBS", "Unused Elastic IP"}

    # waste_usd must be NaN, NOT zero (cost data truly unavailable)
    assert findings["waste_usd"].isna().all(), (
        "waste_usd should be NaN when no cost column is present, "
        "not zero — NaN signals 'unknown' not 'free'."
    )


def test_no_cost_column_cost_data_available_is_false():
    rows = [_ebs_row("vol-nocost", state="available", cost_col=None)]
    df = pd.DataFrame(rows)
    findings = detect_idle_resources(df)
    assert not findings.empty
    assert bool(findings.iloc[0]["cost_data_available"]) is False


def test_all_nan_cost_column_treated_as_unavailable():
    """Live AWS inventory has lineItem/UnblendedCost but all values are NaN."""
    rows = [
        _ec2_row("i-nancost", state="running", cpu_p95=1.0, cost=np.nan),
        _ebs_row("vol-nancost", state="available"),
    ]
    # Set the cost column values to NaN
    df = pd.DataFrame(rows)
    df["lineItem/UnblendedCost"] = np.nan
    findings = detect_idle_resources(df, ec2_cpu_p95_threshold=5.0, min_days_observed=1)

    assert not findings.empty
    # waste_usd should be NaN (not zero) because all costs were NaN
    assert findings["waste_usd"].isna().all()
    assert findings["cost_data_available"].eq(False).all()


# ---------------------------------------------------------------------------
# 5. Empty DataFrame
# ---------------------------------------------------------------------------

def test_empty_dataframe_returns_empty_findings():
    findings = detect_idle_resources(pd.DataFrame())
    assert isinstance(findings, pd.DataFrame)
    assert findings.empty


def test_empty_dataframe_with_columns_returns_empty_findings():
    df = pd.DataFrame(columns=["lineItem/ResourceId", "lineItem/UnblendedCost"])
    findings = detect_idle_resources(df)
    assert isinstance(findings, pd.DataFrame)
    assert findings.empty


# ---------------------------------------------------------------------------
# 6. Missing resource identifier columns
# ---------------------------------------------------------------------------

def test_missing_resource_id_column_does_not_crash():
    df = pd.DataFrame([{
        "product/servicecode": "EBS",
        "resource_state": "available",
        "lineItem/UsageAccountId": "111",
    }])
    findings = detect_idle_resources(df)
    # Should not raise; may or may not detect findings depending on defaults
    assert isinstance(findings, pd.DataFrame)


def test_missing_account_id_column_does_not_crash():
    df = pd.DataFrame([{
        "lineItem/ResourceId": "vol-xyz",
        "product/servicecode": "EBS",
        "resource_state": "available",
    }])
    findings = detect_idle_resources(df)
    assert isinstance(findings, pd.DataFrame)


# ---------------------------------------------------------------------------
# 7. Missing CPU / utilisation data
# ---------------------------------------------------------------------------

def test_missing_cpu_column_ec2_not_flagged_as_idle():
    """Without CPU data, EC2 instances should NOT be incorrectly flagged as idle."""
    rows = [_ec2_row("i-nocpu", state="running", cpu_p95=np.nan, cpu_avg=np.nan)]
    df = pd.DataFrame(rows)
    df["cpu_utilization_avg"] = np.nan
    df["cpu_utilization_p95"] = np.nan
    findings = detect_idle_resources(df, ec2_cpu_p95_threshold=5.0, min_days_observed=1)
    idle = findings[findings["finding_type"] == "Idle EC2"]
    # fillna(100) for cpu_p95 means NaN CPU -> treated as 100% -> NOT idle
    assert idle.empty, "EC2 with no CPU data should not be flagged as idle (NaN -> 100% assumption)"


def test_missing_cpu_column_does_not_crash():
    df = pd.DataFrame([{
        "lineItem/ResourceId": "i-nocpu",
        "lineItem/UsageAccountId": "123",
        "product/servicecode": "EC2",
        "resource_state": "running",
    }])
    findings = detect_idle_resources(df)
    assert isinstance(findings, pd.DataFrame)


# ---------------------------------------------------------------------------
# 8. Live AWS inventory without CUR billing columns
# ---------------------------------------------------------------------------

def test_live_inventory_schema_no_billing_columns():
    """Simulate the exact record format returned by aws_live_collector.py."""
    import numpy as np
    live_records = [
        {
            "lineItem/ResourceId": "i-live001",
            "lineItem/UsageAccountId": "987654321098",
            "product/servicecode": "EC2",
            "product/region": "us-east-1",
            "product/instanceType": "t3.large",
            "resource_state": "running",
            "lineItem/UnblendedCost": np.nan,   # always NaN in live collector
            "lineItem/UnblendedRate": np.nan,
            "cpu_utilization_avg": 1.5,
            "cpu_utilization_p95": 2.0,
            "network_usage_gb": 0.1,
            "size_gb": 0,
            "identity/TimeInterval": "2026-01-01T00:00:00Z/2026-01-15T00:00:00Z",
            "resourceTags/user:Owner": "ops",
            "resourceTags/user:Environment": "prod",
            "resourceTags/user:Cost-Center": "CC-1",
        },
        {
            "lineItem/ResourceId": "vol-live002",
            "lineItem/UsageAccountId": "987654321098",
            "product/servicecode": "EBS",
            "product/region": "us-east-1",
            "product/instanceType": "",
            "resource_state": "available",
            "lineItem/UnblendedCost": np.nan,
            "lineItem/UnblendedRate": np.nan,
            "cpu_utilization_avg": np.nan,
            "cpu_utilization_p95": np.nan,
            "network_usage_gb": np.nan,
            "size_gb": 200,
            "identity/TimeInterval": "2026-01-01T00:00:00Z/2026-01-15T00:00:00Z",
            "resourceTags/user:Owner": "",
            "resourceTags/user:Environment": "",
            "resourceTags/user:Cost-Center": "",
        },
        {
            "lineItem/ResourceId": "eipalloc-live003",
            "lineItem/UsageAccountId": "987654321098",
            "product/servicecode": "ElasticIP",
            "product/region": "us-east-1",
            "product/instanceType": "",
            "resource_state": "unassociated",
            "lineItem/UnblendedCost": np.nan,
            "lineItem/UnblendedRate": np.nan,
            "cpu_utilization_avg": np.nan,
            "cpu_utilization_p95": np.nan,
            "network_usage_gb": np.nan,
            "size_gb": 0,
            "identity/TimeInterval": "2026-01-01T00:00:00Z/2026-01-15T00:00:00Z",
            "resourceTags/user:Owner": "net",
            "resourceTags/user:Environment": "prod",
            "resourceTags/user:Cost-Center": "CC-2",
        },
    ]
    df = pd.DataFrame(live_records)
    findings = detect_idle_resources(df, ec2_cpu_p95_threshold=5.0, min_days_observed=1)

    assert not findings.empty
    types = set(findings["finding_type"])
    assert "Idle EC2" in types
    assert "Unattached EBS" in types
    assert "Unused Elastic IP" in types

    # Waste USD is NaN because cost data is unavailable
    assert findings["waste_usd"].isna().all()
    assert findings["cost_data_available"].eq(False).all()


# ---------------------------------------------------------------------------
# 9. Idle resources detected even when cost is missing
# ---------------------------------------------------------------------------

def test_idle_ec2_detected_without_cost():
    rows = [_ec2_row("i-idle-nocost", state="running", cpu_p95=0.5, cost_col=None)]
    df = pd.DataFrame(rows)
    findings = detect_idle_resources(df, ec2_cpu_p95_threshold=5.0, min_days_observed=1)
    assert not findings.empty
    assert findings.iloc[0]["finding_type"] == "Idle EC2"


def test_unattached_ebs_detected_without_cost():
    rows = [_ebs_row("vol-free-nocost", state="available", cost_col=None)]
    df = pd.DataFrame(rows)
    findings = detect_idle_resources(df)
    assert not findings.empty
    assert findings.iloc[0]["finding_type"] == "Unattached EBS"


def test_unused_eip_detected_without_cost():
    rows = [_eip_row("eip-nocost", state="unassociated", cost_col=None)]
    df = pd.DataFrame(rows)
    findings = detect_idle_resources(df)
    assert not findings.empty
    assert findings.iloc[0]["finding_type"] == "Unused Elastic IP"


# ---------------------------------------------------------------------------
# 10. waste_usd is NaN, not 0, when cost unavailable
# ---------------------------------------------------------------------------

def test_waste_usd_is_nan_not_zero_when_cost_missing():
    rows = [
        _ec2_row("i-waste", state="running", cpu_p95=0.5, cost_col=None),
        _ebs_row("vol-waste", state="available", cost_col=None),
        _eip_row("eip-waste", state="unassociated", cost_col=None),
    ]
    df = pd.DataFrame(rows)
    findings = detect_idle_resources(df, ec2_cpu_p95_threshold=5.0, min_days_observed=1)

    assert not findings.empty
    for _, row in findings.iterrows():
        assert pd.isna(row["waste_usd"]), (
            f"Expected NaN waste_usd for {row['finding_type']} (resource: {row['lineItem/ResourceId']}), "
            f"got {row['waste_usd']}. Zero would falsely suggest free resources."
        )


# ---------------------------------------------------------------------------
# _resolve_cost_column helper
# ---------------------------------------------------------------------------

def test_resolve_cost_column_finds_canonical():
    df = pd.DataFrame([{"lineItem/UnblendedCost": 10.0}])
    col, has_real = _resolve_cost_column(df)
    assert col == "lineItem/UnblendedCost"
    assert has_real is True


def test_resolve_cost_column_finds_alias():
    df = pd.DataFrame([{"UnblendedCost": 5.5}])
    col, has_real = _resolve_cost_column(df)
    assert col == "UnblendedCost"
    assert has_real is True


def test_resolve_cost_column_all_nan():
    df = pd.DataFrame([{"lineItem/UnblendedCost": np.nan}])
    col, has_real = _resolve_cost_column(df)
    assert col == "lineItem/UnblendedCost"
    assert has_real is False


def test_resolve_cost_column_missing():
    df = pd.DataFrame([{"some_other_column": 1}])
    col, has_real = _resolve_cost_column(df)
    assert col is None
    assert has_real is False


# ---------------------------------------------------------------------------
# Regression: synthetic CUR still works after refactor
# ---------------------------------------------------------------------------

def test_synthetic_cur_still_detected(tmp_path):
    from utils.synthetic_generator import generate_synthetic_cur
    csv_path = tmp_path / "syn.csv"
    generate_synthetic_cur(csv_path, seed=42)
    df = pd.read_csv(csv_path)
    findings = detect_idle_resources(df)
    assert not findings.empty
    assert "finding_type" in findings.columns
    assert "waste_usd" in findings.columns
    # Synthetic data has real costs, so cost_data_available should be True
    if "cost_data_available" in findings.columns:
        assert findings["cost_data_available"].any()


# ---------------------------------------------------------------------------
# Regression: uploaded CUR data with real costs still works
# ---------------------------------------------------------------------------

def test_uploaded_cur_format_works():
    rows = [
        _ec2_row("i-uploaded", state="running", cpu_p95=2.0, cost=78.0, cost_col="lineItem/UnblendedCost"),
        _ebs_row("vol-uploaded", state="available", cost_col="lineItem/UnblendedCost"),
    ]
    df = pd.DataFrame(rows)
    findings = detect_idle_resources(df, ec2_cpu_p95_threshold=5.0, min_days_observed=1)
    assert not findings.empty
    # Real cost data present — waste_usd should NOT be NaN
    assert not findings["waste_usd"].isna().all()
