"""Regression test suite for the EC2 rightsizing engine.

Validates that recommend_rightsizing():
1. Works with standard CUR / synthetic datasets.
2. Works when 'product/servicecode' is missing (using aliases or instance type deduction).
3. Safely handles empty DataFrames without raising KeyError or crashing.
4. Safely handles missing CPU metrics without raising KeyError or fabricating data.
5. Works with live AWS EC2 inventory datasets with pre-aggregated CloudWatch metrics.
6. Safely handles completely unsupported/malformed schemas.
7. Produces valid rightsizing recommendations for valid EC2 downsize candidates.
"""
from __future__ import annotations

import sys
from pathlib import Path
import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.rightsizing import recommend_rightsizing, DOWNSIZE_MAP, INSTANCE_RATES
from utils.synthetic_generator import generate_synthetic_cur


def test_empty_dataframe_returns_empty():
    res = recommend_rightsizing(pd.DataFrame())
    assert isinstance(res, pd.DataFrame)
    assert res.empty


def test_missing_servicecode_uses_alias_or_type():
    # DataFrame without 'product/servicecode', but has 'service' alias
    df = pd.DataFrame([
        {
            "lineItem/ResourceId": "i-test001",
            "lineItem/UsageAccountId": "123456789012",
            "service": "EC2",
            "product/instanceType": "t3.large",
            "product/region": "us-east-1",
            "cpu_utilization_avg": 5.0,
        }
    ])
    res = recommend_rightsizing(df, p95_threshold=20.0)
    assert not res.empty
    assert res.iloc[0]["resource_id"] == "i-test001"
    assert res.iloc[0]["recommended_instance_type"] == "t3.medium"
    assert res.iloc[0]["estimated_monthly_savings_usd"] > 0


def test_missing_servicecode_entirely_detects_from_instance_type():
    # DataFrame with no service column at all, only instance type and CPU
    df = pd.DataFrame([
        {
            "resource_id": "i-test002",
            "account_id": "123456789012",
            "instance_type": "m5.2xlarge",
            "region": "eu-west-1",
            "cpu_avg": 8.0,
        }
    ])
    res = recommend_rightsizing(df, p95_threshold=20.0)
    assert not res.empty
    assert res.iloc[0]["resource_id"] == "i-test002"
    assert res.iloc[0]["recommended_instance_type"] == "m5.xlarge"


def test_missing_cpu_metrics_returns_empty():
    # EC2 instances present, but CPU metrics missing
    df = pd.DataFrame([
        {
            "lineItem/ResourceId": "i-test003",
            "lineItem/UsageAccountId": "123456789012",
            "product/servicecode": "EC2",
            "product/instanceType": "t3.xlarge",
            "product/region": "us-east-1",
        }
    ])
    res = recommend_rightsizing(df, p95_threshold=20.0)
    assert isinstance(res, pd.DataFrame)
    assert res.empty


def test_all_nan_cpu_metrics_returns_empty():
    # CPU columns present, but all values are NaN
    df = pd.DataFrame([
        {
            "lineItem/ResourceId": "i-test004",
            "lineItem/UsageAccountId": "123456789012",
            "product/servicecode": "EC2",
            "product/instanceType": "t3.xlarge",
            "product/region": "us-east-1",
            "cpu_utilization_avg": np.nan,
            "cpu_utilization_p95": np.nan,
        }
    ])
    res = recommend_rightsizing(df, p95_threshold=20.0)
    assert isinstance(res, pd.DataFrame)
    assert res.empty


def test_unsupported_schema_returns_empty():
    # Unrelated arbitrary schema with no resource identifiers
    df = pd.DataFrame([
        {"some_random_key": "val1", "another_key": 123},
        {"some_random_key": "val2", "another_key": 456},
    ])
    res = recommend_rightsizing(df)
    assert isinstance(res, pd.DataFrame)
    assert res.empty


def test_cur_synthetic_mode_compatibility(tmp_path):
    # Full synthetic dataset
    cur_file = tmp_path / "synthetic.csv"
    df = generate_synthetic_cur(cur_file, seed=42)
    res = recommend_rightsizing(df, p95_threshold=20.0)
    assert isinstance(res, pd.DataFrame)
    assert not res.empty
    expected_cols = {
        "account", "resource_id", "region", "current_instance_type",
        "recommended_instance_type", "p95_cpu_pct", "current_monthly_cost_usd",
        "recommended_monthly_cost_usd", "estimated_monthly_savings_usd", "reason"
    }
    assert expected_cols.issubset(set(res.columns))
    assert (res["estimated_monthly_savings_usd"] > 0).all()


def test_live_inventory_mode_with_p95_col():
    # Live AWS inventory format where CloudWatch p95 metric is already present
    df = pd.DataFrame([
        {
            "lineItem/ResourceId": "i-live999",
            "lineItem/UsageAccountId": "999888777666",
            "product/servicecode": "EC2",
            "product/region": "ap-south-1",
            "product/instanceType": "t3.2xlarge",
            "resource_state": "running",
            "lineItem/UnblendedCost": np.nan,
            "lineItem/UnblendedRate": np.nan,
            "cpu_utilization_avg": 4.5,
            "cpu_utilization_p95": 7.2,
            "network_usage_gb": 0.5,
            "size_gb": 0,
            "identity/TimeInterval": "2026-08-01T00:00:00Z/2026-08-15T00:00:00Z",
            "resourceTags/user:Owner": "devops",
            "resourceTags/user:Environment": "prod",
            "resourceTags/user:Cost-Center": "CC-99",
        }
    ])
    res = recommend_rightsizing(df, p95_threshold=20.0)
    assert not res.empty
    rec = res.iloc[0]
    assert rec["resource_id"] == "i-live999"
    assert rec["current_instance_type"] == "t3.2xlarge"
    assert rec["recommended_instance_type"] == "t3.xlarge"
    assert rec["p95_cpu_pct"] == 7.2
    assert rec["estimated_monthly_savings_usd"] > 0


def test_high_utilization_not_downsized():
    # Instance with CPU p95 above threshold should NOT be recommended for downsizing
    df = pd.DataFrame([
        {
            "lineItem/ResourceId": "i-busy001",
            "lineItem/UsageAccountId": "123456789012",
            "product/servicecode": "EC2",
            "product/instanceType": "t3.large",
            "product/region": "us-east-1",
            "cpu_utilization_avg": 35.0,
            "cpu_utilization_p95": 45.0,
        }
    ])
    res = recommend_rightsizing(df, p95_threshold=20.0)
    assert res.empty


def test_non_downsizeable_instance_type_skipped():
    # Instance type that cannot be downsized further (e.g. t3.nano) or unknown family
    df = pd.DataFrame([
        {
            "lineItem/ResourceId": "i-nano001",
            "lineItem/UsageAccountId": "123456789012",
            "product/servicecode": "EC2",
            "product/instanceType": "t3.nano",
            "product/region": "us-east-1",
            "cpu_utilization_avg": 1.0,
            "cpu_utilization_p95": 2.0,
        }
    ])
    res = recommend_rightsizing(df, p95_threshold=20.0)
    assert res.empty
