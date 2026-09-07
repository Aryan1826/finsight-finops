import sys
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.aws_client import (
    create_boto3_session,
    validate_credentials,
    AWSCredentialError,
    AWSError,
)
from utils.aws_live_collector import (
    collect_live_ec2_instances,
    collect_live_ebs_volumes,
    collect_live_elastic_ips,
    collect_cloudwatch_ec2_metrics,
    collect_cost_explorer_spend,
    collect_live_aws_inventory,
)
from utils.normalizer import normalize_live_dataframe, validate_canonical_schema
from utils.remediation_simulator import generate_remediation_plan, remediation_plan_to_dataframe


def test_validate_credentials_success():
    mock_sts = MagicMock()
    mock_sts.get_caller_identity.return_value = {
        "Account": "123456789012",
        "Arn": "arn:aws:iam::123456789012:user/finops-auditor",
        "UserId": "AIDA1234567890EXAMPLE",
    }
    with patch("utils.aws_client.get_sts_client", return_value=mock_sts):
        caller = validate_credentials()
        assert caller["account_id"] == "123456789012"
        assert "finops-auditor" in caller["arn"]
        assert caller["user_id"] == "AIDA1234567890EXAMPLE"


def test_validate_credentials_missing_raises_error():
    import botocore.exceptions
    mock_sts = MagicMock()
    mock_sts.get_caller_identity.side_effect = botocore.exceptions.NoCredentialsError()
    with patch("utils.aws_client.get_sts_client", return_value=mock_sts):
        with pytest.raises(AWSCredentialError, match="No AWS credentials found"):
            validate_credentials()


def test_collect_live_ec2_instances_mock():
    mock_ec2 = MagicMock()
    mock_paginator = MagicMock()
    mock_ec2.get_paginator.return_value = mock_paginator
    mock_paginator.paginate.return_value = [
        {
            "Reservations": [
                {
                    "Instances": [
                        {
                            "InstanceId": "i-001122334455aabbc",
                            "InstanceType": "t3.medium",
                            "State": {"Name": "running"},
                            "LaunchTime": datetime(2026, 8, 1, tzinfo=timezone.utc),
                            "Tags": [
                                {"Key": "Owner", "Value": "infra-ops"},
                                {"Key": "Environment", "Value": "prod"},
                                {"Key": "Cost-Center", "Value": "CC-100"},
                            ],
                        },
                        {
                            "InstanceId": "i-998877665544ccbba",
                            "InstanceType": "m5.large",
                            "State": {"Name": "stopped"},
                            "LaunchTime": datetime(2026, 8, 10, tzinfo=timezone.utc),
                            "Tags": [],
                        },
                    ]
                }
            ]
        }
    ]

    records = collect_live_ec2_instances(mock_ec2, "123456789012", "us-east-1")
    assert len(records) == 2

    r1 = records[0]
    assert r1["lineItem/ResourceId"] == "i-001122334455aabbc"
    assert r1["product/servicecode"] == "EC2"
    assert r1["product/instanceType"] == "t3.medium"
    assert r1["resource_state"] == "running"
    assert r1["resourceTags/user:Owner"] == "infra-ops"
    assert r1["resourceTags/user:Environment"] == "prod"
    assert r1["resourceTags/user:Cost-Center"] == "CC-100"

    r2 = records[1]
    assert r2["lineItem/ResourceId"] == "i-998877665544ccbba"
    assert r2["resource_state"] == "stopped"
    assert r2["resourceTags/user:Owner"] == ""


def test_collect_live_ebs_volumes_mock():
    mock_ec2 = MagicMock()
    mock_paginator = MagicMock()
    mock_ec2.get_paginator.return_value = mock_paginator
    mock_paginator.paginate.return_value = [
        {
            "Volumes": [
                {
                    "VolumeId": "vol-1234567890abcdef0",
                    "Size": 150,
                    "VolumeType": "gp3",
                    "State": "available",
                    "Tags": [
                        {"Key": "Owner", "Value": "storage-team"},
                        {"Key": "Environment", "Value": "staging"},
                    ],
                },
                {
                    "VolumeId": "vol-abcdef12345678901",
                    "Size": 50,
                    "VolumeType": "io2",
                    "State": "in-use",
                    "Tags": [],
                },
            ]
        }
    ]

    records = collect_live_ebs_volumes(mock_ec2, "123456789012", "us-east-1")
    assert len(records) == 2

    v1 = records[0]
    assert v1["lineItem/ResourceId"] == "vol-1234567890abcdef0"
    assert v1["product/servicecode"] == "EBS"
    assert v1["resource_state"] == "available"
    assert v1["size_gb"] == 150
    assert v1["resourceTags/user:Owner"] == "storage-team"

    v2 = records[1]
    assert v2["lineItem/ResourceId"] == "vol-abcdef12345678901"
    assert v2["resource_state"] == "in-use"
    assert v2["size_gb"] == 50


def test_collect_live_elastic_ips_mock():
    mock_ec2 = MagicMock()
    mock_ec2.describe_addresses.return_value = {
        "Addresses": [
            {
                "AllocationId": "eipalloc-11223344",
                "PublicIp": "54.200.10.1",
                "AssociationId": None,
                "Tags": [{"Key": "Owner", "Value": "network-ops"}],
            },
            {
                "AllocationId": "eipalloc-55667788",
                "PublicIp": "54.200.10.2",
                "AssociationId": "eipassoc-998877",
                "Tags": [],
            },
        ]
    }

    records = collect_live_elastic_ips(mock_ec2, "123456789012", "us-east-1")
    assert len(records) == 2

    e1 = records[0]
    assert e1["lineItem/ResourceId"] == "eipalloc-11223344"
    assert e1["product/servicecode"] == "ElasticIP"
    assert e1["resource_state"] == "unassociated"
    assert e1["resourceTags/user:Owner"] == "network-ops"

    e2 = records[1]
    assert e2["lineItem/ResourceId"] == "eipalloc-55667788"
    assert e2["resource_state"] == "associated"


def test_collect_cloudwatch_ec2_metrics_mock():
    mock_cw = MagicMock()
    mock_cw._service_model = MagicMock()
    mock_cw._service_model.operation_names = ["get_metric_data"]
    # Provide synthetic metric values: [2.0, 3.0, 4.0, 1.0, 2.5]
    mock_cw.get_metric_data.return_value = {
        "MetricDataResults": [
            {
                "Id": "m_0_i_001122334455aabbc",
                "Values": [2.0, 3.0, 4.0, 1.0, 2.5],
                "Timestamps": [
                    datetime(2026, 8, 1, tzinfo=timezone.utc),
                    datetime(2026, 8, 2, tzinfo=timezone.utc),
                    datetime(2026, 8, 3, tzinfo=timezone.utc),
                    datetime(2026, 8, 4, tzinfo=timezone.utc),
                    datetime(2026, 8, 5, tzinfo=timezone.utc),
                ],
            }
        ]
    }

    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    end = datetime(2026, 8, 15, tzinfo=timezone.utc)
    metrics_map, warnings = collect_cloudwatch_ec2_metrics(
        mock_cw, ["i-001122334455aabbc"], start_time=start, end_time=end
    )

    assert len(warnings) == 0
    assert "i-001122334455aabbc" in metrics_map
    m = metrics_map["i-001122334455aabbc"]
    assert m["cpu_utilization_avg"] == 2.5
    assert m["cpu_utilization_p95"] > 2.5
    assert m["datapoints_count"] == 5
    assert m["observed_days"] == 5


def test_collect_cost_explorer_spend_mock():
    mock_ce = MagicMock()
    mock_ce.get_cost_and_usage.return_value = {
        "ResultsByTime": [
            {
                "TimePeriod": {"Start": "2026-08-01", "End": "2026-08-02"},
                "Groups": [
                    {
                        "Keys": ["Amazon Elastic Compute Cloud - Compute", "BoxUsage:t3.medium"],
                        "Metrics": {"UnblendedCost": {"Amount": "12.45"}},
                    },
                    {
                        "Keys": ["Amazon Elastic Block Store", "EBS:VolumeUsage.gp3"],
                        "Metrics": {"UnblendedCost": {"Amount": "3.20"}},
                    },
                ],
            }
        ]
    }

    df, warnings = collect_cost_explorer_spend(
        mock_ce, start_date=date(2026, 8, 1), end_date=date(2026, 8, 2)
    )
    assert len(warnings) == 0
    assert len(df) == 2
    assert "service" in df.columns
    assert "unblended_cost_usd" in df.columns
    assert df["unblended_cost_usd"].sum() == pytest.approx(15.65)


def test_master_collector_integration_mock():
    with patch("utils.aws_live_collector.create_boto3_session") as mock_sess, \
         patch("utils.aws_live_collector.validate_credentials") as mock_val, \
         patch("utils.aws_live_collector.collect_live_ec2_instances") as mock_ec2, \
         patch("utils.aws_live_collector.collect_live_ebs_volumes") as mock_ebs, \
         patch("utils.aws_live_collector.collect_live_elastic_ips") as mock_eip, \
         patch("utils.aws_live_collector.collect_cloudwatch_ec2_metrics") as mock_cw:

        mock_val.return_value = {"account_id": "123456789012", "arn": "arn:aws:iam::123456789012:root"}
        mock_ec2.return_value = [{
            "lineItem/ResourceId": "i-12345",
            "lineItem/UsageAccountId": "123456789012",
            "product/servicecode": "EC2",
            "product/region": "us-east-1",
            "product/instanceType": "t3.medium",
            "resource_state": "running",
            "lineItem/UnblendedCost": np.nan,
            "lineItem/UnblendedRate": np.nan,
            "cpu_utilization_avg": np.nan,
            "cpu_utilization_p95": np.nan,
            "network_usage_gb": np.nan,
            "size_gb": 0,
            "resourceTags/user:Owner": "devops",
            "resourceTags/user:Environment": "prod",
            "resourceTags/user:Cost-Center": "CC-100",
        }]
        mock_ebs.return_value = [{
            "lineItem/ResourceId": "vol-12345",
            "lineItem/UsageAccountId": "123456789012",
            "product/servicecode": "EBS",
            "product/region": "us-east-1",
            "product/instanceType": "",
            "resource_state": "available",
            "lineItem/UnblendedCost": np.nan,
            "lineItem/UnblendedRate": np.nan,
            "cpu_utilization_avg": np.nan,
            "cpu_utilization_p95": np.nan,
            "network_usage_gb": np.nan,
            "size_gb": 100,
            "resourceTags/user:Owner": "devops",
            "resourceTags/user:Environment": "prod",
            "resourceTags/user:Cost-Center": "CC-100",
        }]
        mock_eip.return_value = [{
            "lineItem/ResourceId": "eipalloc-12345",
            "lineItem/UsageAccountId": "123456789012",
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
            "resourceTags/user:Owner": "",
            "resourceTags/user:Environment": "",
            "resourceTags/user:Cost-Center": "",
        }]
        mock_cw.return_value = (
            {"i-12345": {"cpu_utilization_avg": 2.1, "cpu_utilization_p95": 3.4, "observed_days": 14}},
            []
        )

        df, meta = collect_live_aws_inventory(region="us-east-1", include_cloudwatch=True)

        assert meta["total_resources"] == 3
        assert len(df) == 3
        assert meta["ec2_count"] == 1
        assert meta["ebs_count"] == 1
        assert meta["eip_count"] == 1

        # Check normalization
        normalized = normalize_live_dataframe(df)
        validation = validate_canonical_schema(normalized)
        assert validation["is_valid"] is True


def test_remediation_simulation_only():
    idle_findings = pd.DataFrame([
        {
            "lineItem/ResourceId": "i-idleec2",
            "lineItem/UsageAccountId": "123456789012",
            "product/region": "us-east-1",
            "finding_type": "Idle EC2",
            "waste_usd": 60.50,
            "rule": "p95 CPU < 5.0%",
        },
        {
            "lineItem/ResourceId": "vol-idleebs",
            "lineItem/UsageAccountId": "123456789012",
            "product/region": "us-east-1",
            "finding_type": "Unattached EBS",
            "waste_usd": 24.00,
            "rule": "EBS state = available",
        },
        {
            "lineItem/ResourceId": "eipalloc-unused",
            "lineItem/UsageAccountId": "123456789012",
            "product/region": "us-east-1",
            "finding_type": "Unused Elastic IP",
            "waste_usd": 3.65,
            "rule": "Elastic IP is unassociated",
        },
    ])

    rightsizing = pd.DataFrame([
        {
            "resource_id": "i-overprovisioned",
            "account": "123456789012",
            "region": "us-east-1",
            "current_instance_type": "t3.2xlarge",
            "recommended_instance_type": "t3.xlarge",
            "estimated_monthly_savings_usd": 121.47,
            "reason": "p95 CPU 12.5% is below 20% threshold",
        }
    ])

    plan = generate_remediation_plan(idle_findings, rightsizing)
    assert len(plan) == 4

    plan_df = remediation_plan_to_dataframe(plan)
    assert len(plan_df) == 4
    assert (plan_df["safety_mode"] == "SIMULATION_ONLY").all()
    assert (plan_df["approval_status"] == "Pending Approval (Dry-Run Only)").all()

    # Verify CLI preview strings include --dry-run
    ec2_item = next(p for p in plan if p["finding_type"] == "Idle EC2")
    assert "--dry-run" in ec2_item["cli_command_preview"]
    assert "stop-instances" in ec2_item["cli_command_preview"]

    ebs_item = next(p for p in plan if p["finding_type"] == "Unattached EBS")
    assert "--dry-run" in ebs_item["cli_command_preview"]
    assert "delete-volume" in ebs_item["cli_command_preview"]

    eip_item = next(p for p in plan if p["finding_type"] == "Unused Elastic IP")
    assert "--dry-run" in eip_item["cli_command_preview"]
    assert "release-address" in eip_item["cli_command_preview"]


def test_no_destructive_aws_methods_exist():
    import inspect
    import utils.aws_client as ac
    import utils.aws_live_collector as alc
    import utils.remediation_simulator as rs

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

    for module in [ac, alc, rs]:
        source = inspect.getsource(module)
        for method in forbidden:
            # Check function definitions
            assert f"def {method}" not in source, f"Forbidden method definition found in {module.__name__}: {method}"


def test_assume_role_session_success():
    import botocore.exceptions
    mock_sts = MagicMock()
    mock_sts.assume_role.return_value = {
        "Credentials": {
            "AccessKeyId": "ASIA_TEMP_ACCESS_KEY",
            "SecretAccessKey": "temp_secret_key",
            "SessionToken": "temp_session_token",
            "Expiration": datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc),
        }
    }

    with patch("boto3.Session") as mock_session_cls:
        mock_source_sess = MagicMock()
        mock_source_sess.client.return_value = mock_sts

        mock_temp_sess = MagicMock()
        mock_session_cls.side_effect = [mock_source_sess, mock_temp_sess]

        session = create_boto3_session(
            role_arn="arn:aws:iam::987654321098:role/FinSightAuditRole",
            external_id="ext-id-12345",
            region_name="ap-south-1",
        )

        # Verify STS assume_role call
        mock_sts.assume_role.assert_called_once_with(
            RoleArn="arn:aws:iam::987654321098:role/FinSightAuditRole",
            RoleSessionName="FinSightSession",
            ExternalId="ext-id-12345",
        )
        assert session == mock_temp_sess


def test_assume_role_missing_external_id_raises():
    with pytest.raises(AWSCredentialError, match="External ID is required"):
        create_boto3_session(
            role_arn="arn:aws:iam::987654321098:role/FinSightAuditRole",
            external_id=None,
        )


def test_assume_role_invalid_arn_format_raises():
    with pytest.raises(AWSCredentialError, match="Invalid AWS Role ARN format"):
        create_boto3_session(
            role_arn="invalid-role-arn-string",
            external_id="ext-id-12345",
        )


def test_assume_role_access_denied_raises_permission_error():
    import botocore.exceptions
    mock_sts = MagicMock()
    mock_sts.assume_role.side_effect = botocore.exceptions.ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "User is not authorized to perform sts:AssumeRole"}},
        "AssumeRole",
    )

    with patch("boto3.Session") as mock_session_cls:
        mock_source_sess = MagicMock()
        mock_source_sess.client.return_value = mock_sts
        mock_session_cls.return_value = mock_source_sess

        with pytest.raises(AWSError, match="Unable to assume the specified FinSight role"):
            create_boto3_session(
                role_arn="arn:aws:iam::987654321098:role/FinSightAuditRole",
                external_id="ext-id-12345",
            )


def test_local_profile_session_success():
    with patch("boto3.Session") as mock_session_cls:
        mock_sess = MagicMock()
        mock_session_cls.return_value = mock_sess

        session = create_boto3_session(profile_name="finsight", region_name="ap-south-1")
        mock_session_cls.assert_called_once_with(profile_name="finsight", region_name="ap-south-1")
        assert session == mock_sess


def test_dynamic_account_id_resolution():
    mock_sts_tenant_a = MagicMock()
    mock_sts_tenant_a.get_caller_identity.return_value = {
        "Account": "111122223333",
        "Arn": "arn:aws:sts::111122223333:assumed-role/FinSightRole/FinSightSession",
        "UserId": "AROA111122223333:FinSightSession",
    }
    with patch("utils.aws_client.get_sts_client", return_value=mock_sts_tenant_a):
        caller_a = validate_credentials()
        assert caller_a["account_id"] == "111122223333"

    mock_sts_tenant_b = MagicMock()
    mock_sts_tenant_b.get_caller_identity.return_value = {
        "Account": "999988887777",
        "Arn": "arn:aws:sts::999988887777:assumed-role/FinSightRole/FinSightSession",
        "UserId": "AROA999988887777:FinSightSession",
    }
    with patch("utils.aws_client.get_sts_client", return_value=mock_sts_tenant_b):
        caller_b = validate_credentials()
        assert caller_b["account_id"] == "999988887777"


def test_no_credentials_leaked_in_exceptions():
    import botocore.exceptions
    mock_sts = MagicMock()
    secret_token = "SUPER_SECRET_AWS_KEY_DO_NOT_LEAK"
    mock_sts.assume_role.side_effect = botocore.exceptions.ClientError(
        {"Error": {"Code": "AccessDenied", "Message": f"Failed with {secret_token}"}},
        "AssumeRole",
    )

    with patch("boto3.Session") as mock_session_cls:
        mock_source_sess = MagicMock()
        mock_source_sess.client.return_value = mock_sts
        mock_session_cls.return_value = mock_source_sess

        try:
            create_boto3_session(
                role_arn="arn:aws:iam::987654321098:role/FinSightAuditRole",
                external_id="ext-id-12345",
            )
        except AWSError as e:
            # Confirm user-friendly message without leaking internal secrets
            assert secret_token not in str(e)
            assert "Unable to assume the specified FinSight role" in str(e)


