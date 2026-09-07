import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch
import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.finops_pipeline import run_live_finops_analysis, FinOpsAnalysisResult
from utils.aws_client import AWSCredentialError


@pytest.fixture
def mock_aws_inventory():
    """Mock live inventory with EC2 (one idle, one rightsizing candidate), EBS, EIP."""
    raw_df = pd.DataFrame([
        {
            "lineItem/ResourceId": "i-idle000000000001",
            "lineItem/UsageAccountId": "987654321098",
            "product/servicecode": "EC2",
            "product/region": "eu-west-1",
            "product/instanceType": "t3.large",
            "resource_state": "running",
            "lineItem/UnblendedCost": np.nan,
            "lineItem/UnblendedRate": np.nan,
            "cpu_utilization_avg": 1.2,
            "cpu_utilization_p95": 2.1,
            "network_usage_gb": 0.5,
            "size_gb": 0,
            "identity/TimeInterval": "2026-08-01T00:00:00Z/2026-08-15T00:00:00Z",
            "resourceTags/user:Owner": "infra-ops",
            "resourceTags/user:Environment": "prod",
            "resourceTags/user:Cost-Center": "CC-100",
        },
        {
            "lineItem/ResourceId": "i-rightsize0000002",
            "lineItem/UsageAccountId": "987654321098",
            "product/servicecode": "EC2",
            "product/region": "eu-west-1",
            "product/instanceType": "t3.2xlarge",
            "resource_state": "running",
            "lineItem/UnblendedCost": np.nan,
            "lineItem/UnblendedRate": np.nan,
            "cpu_utilization_avg": 11.5,
            "cpu_utilization_p95": 14.8,
            "network_usage_gb": 3.2,
            "size_gb": 0,
            "identity/TimeInterval": "2026-08-01T00:00:00Z/2026-08-15T00:00:00Z",
            "resourceTags/user:Owner": "data-ops",
            "resourceTags/user:Environment": "staging",
            "resourceTags/user:Cost-Center": "CC-101",
        },
        {
            "lineItem/ResourceId": "vol-available000003",
            "lineItem/UsageAccountId": "987654321098",
            "product/servicecode": "EBS",
            "product/region": "eu-west-1",
            "product/instanceType": "",
            "resource_state": "available",
            "lineItem/UnblendedCost": np.nan,
            "lineItem/UnblendedRate": np.nan,
            "cpu_utilization_avg": np.nan,
            "cpu_utilization_p95": np.nan,
            "network_usage_gb": np.nan,
            "size_gb": 200,
            "identity/TimeInterval": "2026-08-01T00:00:00Z/2026-08-15T00:00:00Z",
            "resourceTags/user:Owner": "",  # Missing tag
            "resourceTags/user:Environment": "invalid-env",  # Invalid tag
            "resourceTags/user:Cost-Center": "",
        },
        {
            "lineItem/ResourceId": "eipalloc-unused000004",
            "lineItem/UsageAccountId": "987654321098",
            "product/servicecode": "ElasticIP",
            "product/region": "eu-west-1",
            "product/instanceType": "",
            "resource_state": "unassociated",
            "lineItem/UnblendedCost": np.nan,
            "lineItem/UnblendedRate": np.nan,
            "cpu_utilization_avg": np.nan,
            "cpu_utilization_p95": np.nan,
            "network_usage_gb": np.nan,
            "size_gb": 0,
            "identity/TimeInterval": "2026-08-01T00:00:00Z/2026-08-15T00:00:00Z",
            "resourceTags/user:Owner": "devops",
            "resourceTags/user:Environment": "prod",
            "resourceTags/user:Cost-Center": "CC-102",
        },
    ])

    cost_df = pd.DataFrame([
        {
            "date": "2026-08-01",
            "date_end": "2026-08-02",
            "service": "Amazon Elastic Compute Cloud - Compute",
            "usage_type": "BoxUsage:t3.large",
            "unblended_cost_usd": 48.50,
        },
        {
            "date": "2026-08-01",
            "date_end": "2026-08-02",
            "service": "Amazon Elastic Block Store",
            "usage_type": "EBS:VolumeUsage.gp3",
            "unblended_cost_usd": 16.00,
        },
    ])

    collector_meta = {
        "account_id": "987654321098",
        "arn": "arn:aws:iam::987654321098:role/finops-audit",
        "region": "eu-west-1",
        "observation_days": 14,
        "ec2_count": 2,
        "ebs_count": 1,
        "eip_count": 1,
        "total_resources": 4,
        "warnings": [],
        "cost_explorer_summary": cost_df,
    }

    return raw_df, collector_meta


def test_pipeline_successful_execution(mock_aws_inventory):
    raw_df, collector_meta = mock_aws_inventory

    with patch("utils.finops_pipeline.create_boto3_session") as mock_sess, \
         patch("utils.finops_pipeline.validate_credentials") as mock_val, \
         patch("utils.finops_pipeline.collect_live_aws_inventory") as mock_col:

        mock_val.return_value = {"account_id": "987654321098", "arn": "arn:aws:iam::987654321098:role/finops-audit"}
        mock_col.return_value = (raw_df, collector_meta)

        result = run_live_finops_analysis(
            profile_name="finsight-test",
            region="eu-west-1",
            observation_days=14,
        )

        assert isinstance(result, FinOpsAnalysisResult)
        # Account & Region propagation
        assert result.account_id == "987654321098"
        assert result.region == "eu-west-1"
        assert result.arn == "arn:aws:iam::987654321098:role/finops-audit"

        # Resource Data verification
        assert len(result.data) == 4

        # Idle findings verification
        assert not result.idle_findings.empty
        idle_types = set(result.idle_findings["finding_type"])
        assert "Idle EC2" in idle_types
        assert "Unattached EBS" in idle_types
        assert "Unused Elastic IP" in idle_types

        # Rightsizing findings verification
        assert not result.rightsizing_findings.empty
        assert "i-rightsize0000002" in result.rightsizing_findings["resource_id"].values
        rs_item = result.rightsizing_findings.iloc[0]
        assert rs_item["current_instance_type"] == "t3.2xlarge"
        assert rs_item["recommended_instance_type"] == "t3.xlarge"
        assert result.rightsizing_savings_usd > 0.0

        # Tagging findings verification
        assert not result.tagging_report.empty
        assert result.compliant_count == 3
        assert result.noncompliant_count == 1
        assert result.tagging_compliance_pct == 75.0

        # Cost summary remains separate
        assert len(result.cost_summary) == 2
        assert result.total_spend_usd == pytest.approx(64.50)

        # Dictionary export
        d = result.to_dict()
        assert d["account_id"] == "987654321098"
        assert d["region"] == "eu-west-1"


def test_pipeline_missing_cloudwatch_warning():
    raw_df = pd.DataFrame([
        {
            "lineItem/ResourceId": "i-nocw0000001",
            "lineItem/UsageAccountId": "111111111111",
            "product/servicecode": "EC2",
            "product/region": "us-west-2",
            "product/instanceType": "t3.micro",
            "resource_state": "running",
            "lineItem/UnblendedCost": np.nan,
            "lineItem/UnblendedRate": np.nan,
            "cpu_utilization_avg": np.nan,
            "cpu_utilization_p95": np.nan,
            "network_usage_gb": np.nan,
            "size_gb": 0,
            "resourceTags/user:Owner": "dev",
            "resourceTags/user:Environment": "dev",
            "resourceTags/user:Cost-Center": "CC-101",
        }
    ])
    collector_meta = {
        "account_id": "111111111111",
        "arn": "arn:aws:iam::111111111111:user/dev",
        "region": "us-west-2",
        "observation_days": 14,
        "ec2_count": 1,
        "ebs_count": 0,
        "eip_count": 0,
        "total_resources": 1,
        "warnings": ["CloudWatch metric data unavailable: AccessDenied"],
        "cost_explorer_summary": pd.DataFrame(),
    }

    with patch("utils.finops_pipeline.create_boto3_session"), \
         patch("utils.finops_pipeline.validate_credentials", return_value={"account_id": "111111111111", "arn": "arn:aws:iam::111111111111:user/dev"}), \
         patch("utils.finops_pipeline.collect_live_aws_inventory", return_value=(raw_df, collector_meta)):

        result = run_live_finops_analysis(region="us-west-2")
        assert len(result.warnings) > 0
        assert any("CloudWatch" in w for w in result.warnings)
        # Should not crash, and should return empty rightsizing findings because of missing CPU data
        assert result.rightsizing_findings.empty


def test_pipeline_missing_cost_explorer_warning():
    raw_df = pd.DataFrame([
        {
            "lineItem/ResourceId": "vol-noce0000001",
            "lineItem/UsageAccountId": "111111111111",
            "product/servicecode": "EBS",
            "product/region": "us-west-2",
            "product/instanceType": "",
            "resource_state": "available",
            "lineItem/UnblendedCost": np.nan,
            "lineItem/UnblendedRate": np.nan,
            "cpu_utilization_avg": np.nan,
            "cpu_utilization_p95": np.nan,
            "network_usage_gb": np.nan,
            "size_gb": 50,
            "resourceTags/user:Owner": "dev",
            "resourceTags/user:Environment": "dev",
            "resourceTags/user:Cost-Center": "CC-101",
        }
    ])
    collector_meta = {
        "account_id": "111111111111",
        "arn": "arn:aws:iam::111111111111:user/dev",
        "region": "us-west-2",
        "observation_days": 14,
        "ec2_count": 0,
        "ebs_count": 1,
        "eip_count": 0,
        "total_resources": 1,
        "warnings": ["AWS Cost Explorer query failed (AccessDenied): spend records unavailable."],
        "cost_explorer_summary": pd.DataFrame(),
    }

    with patch("utils.finops_pipeline.create_boto3_session"), \
         patch("utils.finops_pipeline.validate_credentials", return_value={"account_id": "111111111111", "arn": "arn:aws:iam::111111111111:user/dev"}), \
         patch("utils.finops_pipeline.collect_live_aws_inventory", return_value=(raw_df, collector_meta)):

        result = run_live_finops_analysis(region="us-west-2")
        assert result.total_spend_usd == 0.0
        assert len(result.warnings) > 0
        assert any("Cost Explorer" in w for w in result.warnings)
        # EBS available state still detected despite missing Cost Explorer
        assert len(result.idle_findings) == 1
        assert result.idle_findings.iloc[0]["finding_type"] == "Unattached EBS"


def test_pipeline_no_destructive_methods_exist():
    import inspect
    import utils.finops_pipeline as fp
    import scripts.test_live_pipeline as tlp

    forbidden = [
        "stop_instances",
        "terminate_instances",
        "delete_volume",
        "release_address",
        "reboot_instances",
        "modify_instance_attribute",
        "create_tags",
        "delete_tags",
    ]

    for module in [fp, tlp]:
        source = inspect.getsource(module)
        for method in forbidden:
            assert f"def {method}" not in source, f"Forbidden method definition found in {module.__name__}: {method}"


def test_pipeline_cross_account_assume_role_dynamic_account():
    raw_df = pd.DataFrame([
        {
            "lineItem/ResourceId": "i-crossaccount001",
            "lineItem/UsageAccountId": "444455556666",
            "product/servicecode": "EC2",
            "product/region": "ap-southeast-1",
            "product/instanceType": "t3.small",
            "resource_state": "running",
            "lineItem/UnblendedCost": np.nan,
            "lineItem/UnblendedRate": np.nan,
            "cpu_utilization_avg": 25.0,
            "cpu_utilization_p95": 30.0,
            "network_usage_gb": 1.0,
            "size_gb": 0,
            "resourceTags/user:Owner": "finance",
            "resourceTags/user:Environment": "prod",
            "resourceTags/user:Cost-Center": "CC-999",
        }
    ])
    collector_meta = {
        "account_id": "444455556666",
        "arn": "arn:aws:sts::444455556666:assumed-role/FinSightReadOnlyRole/FinSightSession",
        "region": "ap-southeast-1",
        "observation_days": 14,
        "ec2_count": 1,
        "ebs_count": 0,
        "eip_count": 0,
        "total_resources": 1,
        "warnings": [],
        "cost_explorer_summary": pd.DataFrame(),
    }

    with patch("utils.finops_pipeline.create_boto3_session") as mock_sess, \
         patch("utils.finops_pipeline.validate_credentials", return_value={"account_id": "444455556666", "arn": "arn:aws:sts::444455556666:assumed-role/FinSightReadOnlyRole/FinSightSession"}), \
         patch("utils.finops_pipeline.collect_live_aws_inventory", return_value=(raw_df, collector_meta)):

        result = run_live_finops_analysis(
            role_arn="arn:aws:iam::444455556666:role/FinSightReadOnlyRole",
            external_id="finops-tenant-external-id",
            region="ap-southeast-1",
        )

        mock_sess.assert_called_once_with(
            profile_name=None,
            region_name="ap-southeast-1",
            role_arn="arn:aws:iam::444455556666:role/FinSightReadOnlyRole",
            external_id="finops-tenant-external-id",
            role_session_name=None,
        )

        # Confirm dynamic account ID and ARN propagation
        assert result.account_id == "444455556666"
        assert result.region == "ap-southeast-1"
        assert "FinSightReadOnlyRole" in result.arn

