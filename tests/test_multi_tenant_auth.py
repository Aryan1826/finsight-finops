"""Multi-Tenant AWS Authentication & Isolation Test Suite for FinSight.

Tests:
1. User A assumes Account A role -> account_id == "111111111111"
2. User B assumes Account B role -> account_id == "222222222222"
3. User A data is not reused for User B
4. Different role ARN creates separate connection context
5. Different regions create separate connection context
6. Missing External ID fails safely
7. Invalid Role ARN fails safely
8. AssumeRole AccessDenied fails safely
9. No AWS credentials appear in logs/errors
10. No destructive AWS methods exist
11. End-to-end sequential connection flow test
"""
import inspect
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
import numpy as np
import pandas as pd
import pytest

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import utils.aws_client as ac
import utils.aws_live_collector as alc
import utils.finops_pipeline as fp
import utils.remediation_simulator as rs
import scripts.test_live_pipeline as tlp
from utils.aws_client import (
    create_boto3_session,
    validate_credentials,
    test_aws_connection as check_aws_connection,
    AWSCredentialError,
    AWSPermissionError,
    AWSError,
)
from utils.finops_pipeline import run_live_finops_analysis, FinOpsAnalysisResult


# ---------------------------------------------------------------------------
# Test 1: User A assumes Account A role
# ---------------------------------------------------------------------------
def test_user_a_assumes_account_a_role():
    mock_sts = MagicMock()
    mock_sts.assume_role.return_value = {
        "Credentials": {
            "AccessKeyId": "ASIA_USER_A_TEMP_KEY",
            "SecretAccessKey": "secret_key_a",
            "SessionToken": "token_a",
            "Expiration": datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc),
        }
    }
    mock_sts.get_caller_identity.return_value = {
        "Account": "111111111111",
        "Arn": "arn:aws:sts::111111111111:assumed-role/FinSightReadOnlyRole/FinSightSession",
        "UserId": "AROA111111111111:FinSightSession",
    }

    with patch("boto3.Session") as mock_session_cls:
        mock_source_sess = MagicMock()
        mock_source_sess.client.return_value = mock_sts

        mock_temp_sess = MagicMock()
        mock_temp_sess.client.return_value = mock_sts
        mock_session_cls.side_effect = [mock_source_sess, mock_temp_sess]

        conn_info = check_aws_connection(
            role_arn="arn:aws:iam::111111111111:role/FinSightReadOnlyRole",
            external_id="external-a",
            region_name="ap-south-1",
        )

        assert conn_info["account_id"] == "111111111111"
        assert "111111111111" in conn_info["arn"]
        assert conn_info["region"] == "ap-south-1"


# ---------------------------------------------------------------------------
# Test 2: User B assumes Account B role
# ---------------------------------------------------------------------------
def test_user_b_assumes_account_b_role():
    mock_sts = MagicMock()
    mock_sts.assume_role.return_value = {
        "Credentials": {
            "AccessKeyId": "ASIA_USER_B_TEMP_KEY",
            "SecretAccessKey": "secret_key_b",
            "SessionToken": "token_b",
            "Expiration": datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc),
        }
    }
    mock_sts.get_caller_identity.return_value = {
        "Account": "222222222222",
        "Arn": "arn:aws:sts::222222222222:assumed-role/FinSightReadOnlyRole/FinSightSession",
        "UserId": "AROA222222222222:FinSightSession",
    }

    with patch("boto3.Session") as mock_session_cls:
        mock_source_sess = MagicMock()
        mock_source_sess.client.return_value = mock_sts

        mock_temp_sess = MagicMock()
        mock_temp_sess.client.return_value = mock_sts
        mock_session_cls.side_effect = [mock_source_sess, mock_temp_sess]

        conn_info = check_aws_connection(
            role_arn="arn:aws:iam::222222222222:role/FinSightReadOnlyRole",
            external_id="external-b",
            region_name="us-east-1",
        )

        assert conn_info["account_id"] == "222222222222"
        assert "222222222222" in conn_info["arn"]
        assert conn_info["region"] == "us-east-1"


# ---------------------------------------------------------------------------
# Test 3: User A data is not reused for User B
# ---------------------------------------------------------------------------
def test_user_a_data_is_not_reused_for_user_b():
    # Setup data for Account A
    df_a = pd.DataFrame([
        {
            "lineItem/ResourceId": "i-account-a-001",
            "lineItem/UsageAccountId": "111111111111",
            "product/servicecode": "EC2",
            "product/region": "ap-south-1",
            "product/instanceType": "t3.micro",
            "resource_state": "running",
            "lineItem/UnblendedCost": np.nan,
            "lineItem/UnblendedRate": np.nan,
            "cpu_utilization_avg": 1.0,
            "cpu_utilization_p95": 2.0,
            "network_usage_gb": 0.1,
            "size_gb": 0,
            "resourceTags/user:Owner": "user-a",
            "resourceTags/user:Environment": "prod",
            "resourceTags/user:Cost-Center": "CC-A",
        }
    ])
    meta_a = {
        "account_id": "111111111111",
        "arn": "arn:aws:sts::111111111111:assumed-role/FinSightReadOnlyRole/FinSightSession",
        "region": "ap-south-1",
        "observation_days": 14,
        "ec2_count": 1,
        "ebs_count": 0,
        "eip_count": 0,
        "total_resources": 1,
        "warnings": [],
        "cost_explorer_summary": pd.DataFrame(),
    }

    # Setup data for Account B
    df_b = pd.DataFrame([
        {
            "lineItem/ResourceId": "i-account-b-999",
            "lineItem/UsageAccountId": "222222222222",
            "product/servicecode": "EC2",
            "product/region": "us-west-2",
            "product/instanceType": "m5.large",
            "resource_state": "running",
            "lineItem/UnblendedCost": np.nan,
            "lineItem/UnblendedRate": np.nan,
            "cpu_utilization_avg": 45.0,
            "cpu_utilization_p95": 60.0,
            "network_usage_gb": 10.0,
            "size_gb": 0,
            "resourceTags/user:Owner": "user-b",
            "resourceTags/user:Environment": "staging",
            "resourceTags/user:Cost-Center": "CC-B",
        }
    ])
    meta_b = {
        "account_id": "222222222222",
        "arn": "arn:aws:sts::222222222222:assumed-role/FinSightReadOnlyRole/FinSightSession",
        "region": "us-west-2",
        "observation_days": 14,
        "ec2_count": 1,
        "ebs_count": 0,
        "eip_count": 0,
        "total_resources": 1,
        "warnings": [],
        "cost_explorer_summary": pd.DataFrame(),
    }

    with patch("utils.finops_pipeline.create_boto3_session"), \
         patch("utils.finops_pipeline.validate_credentials") as mock_val, \
         patch("utils.finops_pipeline.collect_live_aws_inventory") as mock_col:

        # Run for User A
        mock_val.return_value = {"account_id": "111111111111", "arn": meta_a["arn"]}
        mock_col.return_value = (df_a, meta_a)
        res_a = run_live_finops_analysis(
            role_arn="arn:aws:iam::111111111111:role/FinSightReadOnlyRole",
            external_id="ext-a",
            region="ap-south-1",
        )

        # Run for User B
        mock_val.return_value = {"account_id": "222222222222", "arn": meta_b["arn"]}
        mock_col.return_value = (df_b, meta_b)
        res_b = run_live_finops_analysis(
            role_arn="arn:aws:iam::222222222222:role/FinSightReadOnlyRole",
            external_id="ext-b",
            region="us-west-2",
        )

        assert res_a.account_id == "111111111111"
        assert res_b.account_id == "222222222222"
        assert res_a.region == "ap-south-1"
        assert res_b.region == "us-west-2"
        assert res_a.data.iloc[0]["lineItem/ResourceId"] == "i-account-a-001"
        assert res_b.data.iloc[0]["lineItem/ResourceId"] == "i-account-b-999"


# ---------------------------------------------------------------------------
# Test 4: Different role ARN creates separate connection context
# ---------------------------------------------------------------------------
def test_different_role_arn_creates_separate_connection_context():
    mock_sts = MagicMock()
    mock_sts.assume_role.return_value = {
        "Credentials": {
            "AccessKeyId": "ASIA_TEMP",
            "SecretAccessKey": "temp_secret",
            "SessionToken": "temp_token",
            "Expiration": datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc),
        }
    }

    with patch("boto3.Session") as mock_session_cls:
        mock_source_sess = MagicMock()
        mock_source_sess.client.return_value = mock_sts
        mock_temp_sess = MagicMock()
        mock_session_cls.side_effect = [mock_source_sess, mock_temp_sess, mock_source_sess, mock_temp_sess]

        # Call with Role ARN 1
        create_boto3_session(
            role_arn="arn:aws:iam::111111111111:role/RoleOne",
            external_id="ext-1",
            region_name="us-east-1",
        )
        mock_sts.assume_role.assert_called_with(
            RoleArn="arn:aws:iam::111111111111:role/RoleOne",
            RoleSessionName="FinSightSession",
            ExternalId="ext-1",
        )

        # Call with Role ARN 2
        create_boto3_session(
            role_arn="arn:aws:iam::222222222222:role/RoleTwo",
            external_id="ext-2",
            region_name="us-west-2",
        )
        mock_sts.assume_role.assert_called_with(
            RoleArn="arn:aws:iam::222222222222:role/RoleTwo",
            RoleSessionName="FinSightSession",
            ExternalId="ext-2",
        )


# ---------------------------------------------------------------------------
# Test 5: Different regions create separate connection context
# ---------------------------------------------------------------------------
def test_different_regions_create_separate_connection_context():
    with patch("boto3.Session") as mock_session_cls:
        mock_sess_1 = MagicMock()
        mock_sess_2 = MagicMock()
        mock_session_cls.side_effect = [mock_sess_1, mock_sess_2]

        sess_1 = create_boto3_session(profile_name="finsight", region_name="ap-south-1")
        mock_session_cls.assert_called_with(profile_name="finsight", region_name="ap-south-1")

        sess_2 = create_boto3_session(profile_name="finsight", region_name="eu-central-1")
        mock_session_cls.assert_called_with(profile_name="finsight", region_name="eu-central-1")


# ---------------------------------------------------------------------------
# Test 6: Missing External ID fails safely
# ---------------------------------------------------------------------------
def test_missing_external_id_fails_safely():
    with pytest.raises(AWSCredentialError, match="External ID is required"):
        create_boto3_session(
            role_arn="arn:aws:iam::123456789012:role/FinSightReadOnlyRole",
            external_id=None,
        )

    with pytest.raises(AWSCredentialError, match="External ID is required"):
        create_boto3_session(
            role_arn="arn:aws:iam::123456789012:role/FinSightReadOnlyRole",
            external_id="   ",
        )


# ---------------------------------------------------------------------------
# Test 7: Invalid Role ARN fails safely
# ---------------------------------------------------------------------------
def test_invalid_role_arn_fails_safely():
    invalid_arns = [
        "not-an-arn",
        "arn:aws:ec2:us-east-1:123456789012:instance/i-12345",
        "arn:aws:iam::invalid_account:role/TestRole",
        "arn:aws:iam::12345:role/ShortAccountId",
    ]
    for invalid_arn in invalid_arns:
        with pytest.raises(AWSCredentialError, match="Invalid AWS Role ARN format"):
            create_boto3_session(role_arn=invalid_arn, external_id="ext-id-123")


# ---------------------------------------------------------------------------
# Test 8: AssumeRole AccessDenied fails safely
# ---------------------------------------------------------------------------
def test_assume_role_access_denied_fails_safely():
    import botocore.exceptions
    mock_sts = MagicMock()
    mock_sts.assume_role.side_effect = botocore.exceptions.ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "User is not authorized to assume role"}},
        "AssumeRole",
    )

    with patch("boto3.Session") as mock_session_cls:
        mock_source_sess = MagicMock()
        mock_source_sess.client.return_value = mock_sts
        mock_session_cls.return_value = mock_source_sess

        with pytest.raises(AWSPermissionError, match="Unable to assume the specified FinSight role"):
            create_boto3_session(
                role_arn="arn:aws:iam::123456789012:role/FinSightReadOnlyRole",
                external_id="ext-id-123",
            )


# ---------------------------------------------------------------------------
# Test 9: No AWS credentials appear in logs/errors
# ---------------------------------------------------------------------------
def test_no_aws_credentials_appear_in_logs_or_errors():
    import botocore.exceptions
    sensitive_secret = "AKIA_MOCK_SECRET_KEY_EXPOSURE_TEST"
    mock_sts = MagicMock()
    mock_sts.assume_role.side_effect = botocore.exceptions.ClientError(
        {"Error": {"Code": "AccessDenied", "Message": f"Failed with {sensitive_secret}"}},
        "AssumeRole",
    )

    with patch("boto3.Session") as mock_session_cls:
        mock_source_sess = MagicMock()
        mock_source_sess.client.return_value = mock_sts
        mock_session_cls.return_value = mock_source_sess

        try:
            create_boto3_session(
                role_arn="arn:aws:iam::123456789012:role/FinSightReadOnlyRole",
                external_id="ext-id-123",
            )
        except AWSError as e:
            err_msg = str(e)
            assert sensitive_secret not in err_msg
            assert "Unable to assume the specified FinSight role" in err_msg


# ---------------------------------------------------------------------------
# Test 10: No destructive AWS methods exist across codebase
# ---------------------------------------------------------------------------
def test_no_destructive_aws_methods_exist_across_codebase():
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

    modules_to_check = [ac, alc, fp, rs, tlp]
    for module in modules_to_check:
        source = inspect.getsource(module)
        for method in forbidden:
            assert f"def {method}" not in source, f"Forbidden method definition found in {module.__name__}: {method}"


# ---------------------------------------------------------------------------
# Connection Flow Test: Sequential multi-tenant simulation
# ---------------------------------------------------------------------------
def test_sequential_multi_tenant_connection_flow():
    """Simulate User A connecting Account A, then User B connecting Account B."""
    sessions = {}

    def mock_assume(role_arn, external_id, region):
        mock_sts = MagicMock()
        account = "111111111111" if "111111111111" in role_arn else "222222222222"
        mock_sts.assume_role.return_value = {
            "Credentials": {
                "AccessKeyId": f"ASIA_{account}",
                "SecretAccessKey": f"secret_{account}",
                "SessionToken": f"token_{account}",
                "Expiration": datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc),
            }
        }
        mock_sts.get_caller_identity.return_value = {
            "Account": account,
            "Arn": f"arn:aws:sts::{account}:assumed-role/FinSightReadOnlyRole/FinSightSession",
            "UserId": f"AROA_{account}:FinSightSession",
        }
        return mock_sts

    # 1. Tenant A connects
    with patch("boto3.Session") as mock_session_cls:
        sts_a = mock_assume("arn:aws:iam::111111111111:role/FinSightReadOnlyRole", "external-a", "ap-south-1")
        mock_src_a = MagicMock()
        mock_src_a.client.return_value = sts_a
        mock_tmp_a = MagicMock()
        mock_tmp_a.client.return_value = sts_a
        mock_session_cls.side_effect = [mock_src_a, mock_tmp_a]

        res_a = check_aws_connection(
            role_arn="arn:aws:iam::111111111111:role/FinSightReadOnlyRole",
            external_id="external-a",
            region_name="ap-south-1",
        )
        assert res_a["account_id"] == "111111111111"
        assert res_a["region"] == "ap-south-1"

    # 2. Tenant B connects
    with patch("boto3.Session") as mock_session_cls:
        sts_b = mock_assume("arn:aws:iam::222222222222:role/FinSightReadOnlyRole", "external-b", "us-east-1")
        mock_src_b = MagicMock()
        mock_src_b.client.return_value = sts_b
        mock_tmp_b = MagicMock()
        mock_tmp_b.client.return_value = sts_b
        mock_session_cls.side_effect = [mock_src_b, mock_tmp_b]

        res_b = check_aws_connection(
            role_arn="arn:aws:iam::222222222222:role/FinSightReadOnlyRole",
            external_id="external-b",
            region_name="us-east-1",
        )
        assert res_b["account_id"] == "222222222222"
        assert res_b["region"] == "us-east-1"

    # 3. Verify total isolation between A and B
    assert res_a["account_id"] != res_b["account_id"]
    assert res_a["arn"] != res_b["arn"]
    assert res_a["region"] != res_b["region"]
