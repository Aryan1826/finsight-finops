import sys
from pathlib import Path
import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.normalizer import (
    CANONICAL_COLUMNS,
    normalize_cur_dataframe,
    normalize_live_dataframe,
    validate_canonical_schema,
)
from utils.synthetic_generator import generate_synthetic_cur, save_synthetic_dataset


def test_canonical_schema_on_synthetic_cur(tmp_path):
    csv_path = tmp_path / "synthetic.csv"
    generate_synthetic_cur(output_path=csv_path, days=7, seed=42)
    df = pd.read_csv(csv_path)

    normalized = normalize_cur_dataframe(df)
    validation = validate_canonical_schema(normalized)

    assert validation["is_valid"] is True
    assert len(validation["missing_required"]) == 0
    for col in CANONICAL_COLUMNS:
        assert col in normalized.columns


def test_aliases_and_missing_columns():
    raw_data = pd.DataFrame([
        {
            "resource_id": "i-1234567890abcdef0",
            "account": "111122223333",
            "service": "Amazon Elastic Compute Cloud - Compute",
            "region": "us-east-1",
            "instance_type": "t3.medium",
            "status": "running",
            "cost": "45.50",
            "cpu_avg": 4.2,
            "Owner": "platform-team",
            "Environment": "prod",
            "CostCenter": "CC-101",
        },
        {
            "id": "vol-0987654321fedcba0",
            "AccountId": "111122223333",
            "resource_type": "ebs",
            "Region": "us-east-1",
            "state": "available",
            "size": 100,
            "tag:Owner": "data-team",
            "tag:Environment": "staging",
            "tag:Cost-Center": "CC-102",
        },
        {
            "allocation_id": "eipalloc-0123456789",
            "account_id": "111122223333",
            "servicecode": "Elastic IP",
            "product_region": "us-west-2",
            "state": "unassociated",
            "user:Owner": "devops",
            "user:Environment": "dev",
            "user:Cost-Center": "CC-103",
        },
    ])

    normalized = normalize_cur_dataframe(raw_data)
    validation = validate_canonical_schema(normalized)

    assert validation["is_valid"] is True
    assert len(normalized) == 3

    # Check EC2 row
    ec2_row = normalized[normalized["product/servicecode"] == "EC2"].iloc[0]
    assert ec2_row["lineItem/ResourceId"] == "i-1234567890abcdef0"
    assert ec2_row["lineItem/UsageAccountId"] == "111122223333"
    assert ec2_row["product/instanceType"] == "t3.medium"
    assert ec2_row["resource_state"] == "running"
    assert ec2_row["lineItem/UnblendedCost"] == 45.50
    assert ec2_row["cpu_utilization_avg"] == 4.2
    assert ec2_row["resourceTags/user:Owner"] == "platform-team"
    assert ec2_row["resourceTags/user:Environment"] == "prod"
    assert ec2_row["resourceTags/user:Cost-Center"] == "CC-101"

    # Check EBS row
    ebs_row = normalized[normalized["product/servicecode"] == "EBS"].iloc[0]
    assert ebs_row["lineItem/ResourceId"] == "vol-0987654321fedcba0"
    assert ebs_row["resource_state"] == "available"
    assert ebs_row["size_gb"] == 100
    assert ebs_row["resourceTags/user:Owner"] == "data-team"

    # Check EIP row
    eip_row = normalized[normalized["product/servicecode"] == "ElasticIP"].iloc[0]
    assert eip_row["lineItem/ResourceId"] == "eipalloc-0123456789"
    assert eip_row["resource_state"] == "unassociated"
    assert eip_row["product/region"] == "us-west-2"
    assert eip_row["resourceTags/user:Owner"] == "devops"


def test_empty_dataframe_normalization():
    empty_df = pd.DataFrame()
    normalized = normalize_cur_dataframe(empty_df)
    validation = validate_canonical_schema(normalized)

    assert validation["is_valid"] is True
    assert len(normalized) == 0
    for col in CANONICAL_COLUMNS:
        assert col in normalized.columns


def test_parquet_csv_roundtrip(tmp_path):
    parquet_path = tmp_path / "synthetic.parquet"
    csv_path = tmp_path / "synthetic.csv"

    df = generate_synthetic_cur(output_path=parquet_path, days=3, seed=123)
    assert parquet_path.exists()

    save_synthetic_dataset(df, csv_path=csv_path, parquet_path=parquet_path)
    assert csv_path.exists()

    df_parquet = pd.read_parquet(parquet_path)
    df_csv = pd.read_csv(csv_path)

    assert len(df_parquet) == len(df_csv)
    norm_parquet = normalize_cur_dataframe(df_parquet)
    norm_csv = normalize_cur_dataframe(df_csv)

    assert validate_canonical_schema(norm_parquet)["is_valid"] is True
    assert validate_canonical_schema(norm_csv)["is_valid"] is True



def test_engines_compatibility_with_normalized_data(tmp_path):
    from utils.idle_detector import detect_idle_resources
    from utils.rightsizing import recommend_rightsizing
    from utils.tagging_policy import evaluate_tagging_compliance, load_policy

    csv_path = tmp_path / "synthetic.csv"
    raw_df = generate_synthetic_cur(output_path=csv_path, days=14, seed=42)
    normalized = normalize_cur_dataframe(raw_df)
    idle = detect_idle_resources(normalized)

    assert not idle.empty
    assert "finding_type" in idle.columns
    assert "waste_usd" in idle.columns

    rightsizing = recommend_rightsizing(normalized)
    assert not rightsizing.empty
    assert "estimated_monthly_savings_usd" in rightsizing.columns

    policy = load_policy("config/tagging_policy.yaml")
    tag_report, compliance = evaluate_tagging_compliance(normalized, policy)
    assert not tag_report.empty
    assert 0 < compliance < 100


def test_upload_edge_cases_missing_cpu_and_tags():

    # User uploads a minimal CSV without CPU telemetry or tags
    minimal_data = pd.DataFrame([
        {
            "ResourceId": "i-minimal001",
            "service": "EC2",
            "cost": "15.00",
        },
        {
            "ResourceId": "vol-minimal002",
            "service": "EBS",
            "state": "available",
            "cost": "5.00",
        },
    ])
    normalized = normalize_cur_dataframe(minimal_data)
    validation = validate_canonical_schema(normalized)

    assert validation["is_valid"] is True
    # CPU should be NaN without crashing
    assert pd.isna(normalized.loc[0, "cpu_utilization_avg"])
    # Tags should be empty string defaults
    assert normalized.loc[0, "resourceTags/user:Owner"] == ""
    assert normalized.loc[0, "resourceTags/user:Environment"] == ""
    assert normalized.loc[0, "resourceTags/user:Cost-Center"] == ""


def test_upload_numeric_strings_and_unrecognized_columns():
    messy_data = pd.DataFrame([
        {
            "resource_id": "i-messy001",
            "cost": "$25.50",  # String with formatting or numeric string
            "size": "500",
            "cpu_avg": "3.5",
            "CustomUnrecognizedColumn": "custom_value",
        }
    ])
    normalized = normalize_cur_dataframe(messy_data)
    validation = validate_canonical_schema(normalized)

    assert validation["is_valid"] is True
    assert normalized.loc[0, "size_gb"] == 500
    assert normalized.loc[0, "cpu_utilization_avg"] == 3.5
    # Extra columns should be preserved
    assert "CustomUnrecognizedColumn" in normalized.columns
    assert normalized.loc[0, "CustomUnrecognizedColumn"] == "custom_value"


