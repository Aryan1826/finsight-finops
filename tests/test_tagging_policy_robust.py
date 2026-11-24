"""Regression test suite for FinSight Tagging Governance engine.

Tests evaluate_tagging_compliance() for:
1. Standard CUR dataset with canonical columns.
2. Synthetic dataset with required tags.
3. Live AWS inventory schema with canonical tags.
4. Dataset with alternative tag aliases ('Owner', 'tag:Owner', 'user:Owner', 'env', 'CostCenter').
5. Missing individual metadata columns ('product/servicecode', 'product/region', 'lineItem/UsageAccountId').
6. Missing 'lineItem/ResourceId' with alternative resource ID alias ('resource_id', 'id').
7. Dataset with no resource ID at all (unsupported schema).
8. Dataset with no tag columns at all (unavailable tagging data).
9. Empty DataFrame.
10. Tag compliance values and allowed_values validation.
"""
from __future__ import annotations

import sys
from pathlib import Path
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.tagging_policy import evaluate_tagging_compliance, load_policy
from utils.synthetic_generator import generate_synthetic_cur


@pytest.fixture
def default_policy():
    return {
        "policy_name": "TestPolicy",
        "required_tags": ["Owner", "Environment", "Cost-Center"],
        "allowed_values": {"Environment": ["prod", "staging", "dev"]},
    }


def test_empty_dataframe(default_policy):
    report, compliance = evaluate_tagging_compliance(pd.DataFrame(), default_policy)
    assert isinstance(report, pd.DataFrame)
    assert report.empty
    assert compliance == 100.0


def test_standard_cur_schema(default_policy):
    df = pd.DataFrame([
        {
            "lineItem/ResourceId": "i-001",
            "lineItem/UsageAccountId": "111222333444",
            "product/servicecode": "EC2",
            "product/region": "us-east-1",
            "resourceTags/user:Owner": "alpha-team",
            "resourceTags/user:Environment": "prod",
            "resourceTags/user:Cost-Center": "CC-100",
        },
        {
            "lineItem/ResourceId": "i-002",
            "lineItem/UsageAccountId": "111222333444",
            "product/servicecode": "EC2",
            "product/region": "us-east-1",
            "resourceTags/user:Owner": "beta-team",
            "resourceTags/user:Environment": "invalid-env",
            "resourceTags/user:Cost-Center": "CC-200",
        },
    ])
    report, compliance = evaluate_tagging_compliance(df, default_policy)
    assert len(report) == 2
    assert report.loc[report["lineItem/ResourceId"] == "i-001", "compliant"].iloc[0] == True
    assert report.loc[report["lineItem/ResourceId"] == "i-002", "compliant"].iloc[0] == False
    assert compliance == 50.0


def test_synthetic_cur(default_policy, tmp_path):
    path = tmp_path / "synthetic.csv"
    generate_synthetic_cur(path, seed=42)
    df = pd.read_csv(path)
    report, compliance = evaluate_tagging_compliance(df, default_policy)
    assert len(report) > 0
    assert "compliant" in report.columns
    assert "missing_or_invalid_tags" in report.columns
    assert 0.0 < compliance < 100.0


def test_live_inventory_schema(default_policy):
    df = pd.DataFrame([
        {
            "lineItem/ResourceId": "vol-123",
            "lineItem/UsageAccountId": "999888777666",
            "product/servicecode": "EBS",
            "product/region": "ap-south-1",
            "resourceTags/user:Owner": "storage-ops",
            "resourceTags/user:Environment": "staging",
            "resourceTags/user:Cost-Center": "CC-99",
        }
    ])
    report, compliance = evaluate_tagging_compliance(df, default_policy)
    assert len(report) == 1
    assert report.iloc[0]["compliant"] == True
    assert compliance == 100.0


def test_tag_and_column_aliases(default_policy):
    # Uses 'resource_id', 'account', 'service', 'region' and tag aliases 'Owner', 'env', 'CostCenter'
    df = pd.DataFrame([
        {
            "resource_id": "i-alias-01",
            "account": "123456789012",
            "service": "ec2",
            "region": "eu-central-1",
            "Owner": "devops",
            "env": "dev",
            "CostCenter": "CC-55",
        }
    ])
    report, compliance = evaluate_tagging_compliance(df, default_policy)
    assert len(report) == 1
    row = report.iloc[0]
    assert row["lineItem/ResourceId"] == "i-alias-01"
    assert row["lineItem/UsageAccountId"] == "123456789012"
    assert row["product/servicecode"] == "ec2"
    assert row["product/region"] == "eu-central-1"
    assert row["resourceTags/user:Owner"] == "devops"
    assert row["resourceTags/user:Environment"] == "dev"
    assert row["resourceTags/user:Cost-Center"] == "CC-55"
    assert row["compliant"] == True
    assert compliance == 100.0


def test_missing_metadata_columns_does_not_crash(default_policy):
    # Only ResourceId and tags present, missing service, region, account
    df = pd.DataFrame([
        {
            "lineItem/ResourceId": "i-minimal-1",
            "resourceTags/user:Owner": "devops",
            "resourceTags/user:Environment": "prod",
            "resourceTags/user:Cost-Center": "CC-1",
        }
    ])
    report, compliance = evaluate_tagging_compliance(df, default_policy)
    assert len(report) == 1
    assert report.iloc[0]["product/servicecode"] == "unknown"
    assert report.iloc[0]["product/region"] == "unknown"
    assert report.iloc[0]["lineItem/UsageAccountId"] == "unknown"
    assert report.iloc[0]["compliant"] == True
    assert compliance == 100.0


def test_unsupported_schema_no_resource_id(default_policy):
    df = pd.DataFrame([
        {"col_a": "val1", "col_b": "val2"},
        {"col_a": "val3", "col_b": "val4"},
    ])
    report, compliance = evaluate_tagging_compliance(df, default_policy)
    assert isinstance(report, pd.DataFrame)
    assert report.empty
    assert compliance == 100.0


def test_no_tag_columns_evaluable_distinction(default_policy):
    # Resource present but zero tagging columns exist
    df = pd.DataFrame([
        {
            "lineItem/ResourceId": "i-notags-1",
            "product/servicecode": "EC2",
            "product/region": "us-east-1",
        }
    ])
    report, compliance = evaluate_tagging_compliance(df, default_policy)
    assert len(report) == 1
    assert report.iloc[0]["tagging_evaluable"] == False
    assert report.iloc[0]["compliant"] == False
    assert "Owner" in report.iloc[0]["missing_or_invalid_tags"]
    # Compliance percentage defaults to 100.0 when no resources are evaluable
    assert compliance == 100.0
