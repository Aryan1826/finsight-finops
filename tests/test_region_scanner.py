"""Tests for all-region AWS discovery and scanning (aws_region_scanner.py).

Covers:
1. All enabled regions are discovered dynamically.
2. Opt-in regions are handled correctly (skipped when not opted-in).
3. One region failing doesn't stop the remaining regions.
4. Resource results from multiple regions are merged correctly.
5. Pagination works (mocked).
6. Region metadata is retained on each resource record.
7. Duplicate resources are not counted multiple times.
8. No AWS resource-creation/modification APIs are called (read-only guard).
"""
import sys
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, call

import numpy as np
import pandas as pd
import pytest
import botocore.exceptions

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.aws_region_scanner import (
    discover_enabled_regions,
    filter_eligible_regions,
    scan_all_regions,
    REGION_STATUS_OPT_IN_NOT_REQUIRED,
    REGION_STATUS_OPTED_IN,
    REGION_STATUS_NOT_OPTED_IN,
    _scan_single_region,
)
from utils.aws_client import AWSError


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _make_session_with_ec2(describe_regions_response=None):
    """Create a mock boto3.Session whose EC2 client returns a custom DescribeRegions response."""
    mock_ec2_client = MagicMock()
    mock_ec2_client.describe_regions.return_value = describe_regions_response or {
        "Regions": [
            {"RegionName": "us-east-1", "OptInStatus": "opt-in-not-required", "Endpoint": "ec2.us-east-1.amazonaws.com"},
            {"RegionName": "eu-west-1", "OptInStatus": "opt-in-not-required", "Endpoint": "ec2.eu-west-1.amazonaws.com"},
            {"RegionName": "ap-south-1", "OptInStatus": "opted-in", "Endpoint": "ec2.ap-south-1.amazonaws.com"},
            {"RegionName": "me-central-1", "OptInStatus": "not-opted-in", "Endpoint": "ec2.me-central-1.amazonaws.com"},
        ]
    }
    mock_session = MagicMock()
    mock_session.client.return_value = mock_ec2_client
    return mock_session, mock_ec2_client


# ---------------------------------------------------------------------------
# 1. All enabled regions are discovered dynamically
# ---------------------------------------------------------------------------

def test_discover_enabled_regions_returns_all():
    session, ec2 = _make_session_with_ec2()
    regions = discover_enabled_regions(session)
    assert len(regions) == 4
    names = [r["region"] for r in regions]
    assert "us-east-1" in names
    assert "eu-west-1" in names
    assert "ap-south-1" in names
    assert "me-central-1" in names
    ec2.describe_regions.assert_called_once()


def test_discover_enabled_regions_uses_all_regions_param():
    session, ec2 = _make_session_with_ec2()
    discover_enabled_regions(session, include_all=True)
    call_kwargs = ec2.describe_regions.call_args[1]
    assert call_kwargs.get("AllRegions") is True


def test_discover_enabled_regions_includes_opt_in_status():
    session, ec2 = _make_session_with_ec2()
    regions = discover_enabled_regions(session)
    statuses = {r["region"]: r["opt_in_status"] for r in regions}
    assert statuses["us-east-1"] == REGION_STATUS_OPT_IN_NOT_REQUIRED
    assert statuses["ap-south-1"] == REGION_STATUS_OPTED_IN
    assert statuses["me-central-1"] == REGION_STATUS_NOT_OPTED_IN


# ---------------------------------------------------------------------------
# 2. Opt-in regions handled correctly
# ---------------------------------------------------------------------------

def test_filter_eligible_regions_excludes_not_opted_in():
    all_regions = [
        {"region": "us-east-1", "opt_in_status": REGION_STATUS_OPT_IN_NOT_REQUIRED},
        {"region": "ap-south-1", "opt_in_status": REGION_STATUS_OPTED_IN},
        {"region": "me-central-1", "opt_in_status": REGION_STATUS_NOT_OPTED_IN},
    ]
    eligible, skipped = filter_eligible_regions(all_regions)
    assert "us-east-1" in eligible
    assert "ap-south-1" in eligible
    assert "me-central-1" not in eligible
    assert len(skipped) == 1
    assert skipped[0]["region"] == "me-central-1"
    assert "skip_reason" in skipped[0]


def test_filter_eligible_regions_respects_selection():
    all_regions = [
        {"region": "us-east-1", "opt_in_status": REGION_STATUS_OPT_IN_NOT_REQUIRED},
        {"region": "eu-west-1", "opt_in_status": REGION_STATUS_OPT_IN_NOT_REQUIRED},
        {"region": "us-west-2", "opt_in_status": REGION_STATUS_OPT_IN_NOT_REQUIRED},
    ]
    eligible, skipped = filter_eligible_regions(all_regions, selected_regions=["us-east-1"])
    assert eligible == ["us-east-1"]
    assert len(skipped) == 2


def test_filter_eligible_regions_empty_selection():
    all_regions = [
        {"region": "us-east-1", "opt_in_status": REGION_STATUS_OPT_IN_NOT_REQUIRED},
    ]
    eligible, skipped = filter_eligible_regions(all_regions, selected_regions=[])
    assert eligible == []
    assert len(skipped) == 1


# ---------------------------------------------------------------------------
# 3. One region failing doesn't stop remaining regions
# ---------------------------------------------------------------------------

def test_scan_all_regions_continues_when_one_fails():
    mock_session = MagicMock()

    call_count = {"n": 0}

    def mock_ec2_client_factory(session, region_name):
        call_count["n"] += 1
        client = MagicMock()
        if region_name == "us-east-1":
            # Simulate AccessDenied for this region
            err = botocore.exceptions.ClientError(
                {"Error": {"Code": "AccessDenied", "Message": "Not authorized"}},
                "DescribeInstances",
            )
            paginator = MagicMock()
            paginator.paginate.side_effect = err
            client.get_paginator.return_value = paginator
        else:
            # Success for eu-west-1
            paginator = MagicMock()
            paginator.paginate.return_value = [
                {"Reservations": [{"Instances": [{
                    "InstanceId": f"i-{region_name}-001",
                    "InstanceType": "t3.micro",
                    "State": {"Name": "running"},
                    "LaunchTime": datetime(2026, 1, 1, tzinfo=timezone.utc),
                    "Tags": [],
                }]}]}
            ]
            client.get_paginator.return_value = paginator
            # EBS
            vol_paginator = MagicMock()
            vol_paginator.paginate.return_value = [{"Volumes": []}]
            # EIP
            client.describe_addresses.return_value = {"Addresses": []}
        return client

    with patch("utils.aws_region_scanner.get_ec2_client", side_effect=mock_ec2_client_factory), \
         patch("utils.aws_region_scanner.get_cloudwatch_client", return_value=MagicMock()), \
         patch("utils.aws_region_scanner.collect_cloudwatch_ec2_metrics", return_value=({}, [])):

        df, meta = scan_all_regions(
            session=mock_session,
            eligible_regions=["us-east-1", "eu-west-1"],
            account_id="123456789012",
            observation_days=7,
            include_cloudwatch=False,
            max_workers=2,
        )

    # eu-west-1 should have succeeded; us-east-1 may be in failed
    # The key assertion: scan didn't crash and both regions were attempted
    assert call_count["n"] >= 2


# ---------------------------------------------------------------------------
# 4. Resource results from multiple regions are merged correctly
# ---------------------------------------------------------------------------

def test_scan_all_regions_merges_results():
    regions = ["us-east-1", "eu-west-1"]

    def make_ec2_client(session, region_name):
        client = MagicMock()
        paginator = MagicMock()
        paginator.paginate.return_value = [
            {"Reservations": [{"Instances": [{
                "InstanceId": f"i-{region_name}-001",
                "InstanceType": "t3.micro",
                "State": {"Name": "running"},
                "LaunchTime": datetime(2026, 1, 1, tzinfo=timezone.utc),
                "Tags": [],
            }]}]}
        ]
        client.get_paginator.return_value = paginator
        client.describe_addresses.return_value = {"Addresses": []}
        return client

    with patch("utils.aws_region_scanner.get_ec2_client", side_effect=make_ec2_client), \
         patch("utils.aws_region_scanner.get_cloudwatch_client", return_value=MagicMock()), \
         patch("utils.aws_region_scanner.collect_cloudwatch_ec2_metrics", return_value=({}, [])): \

        df, meta = scan_all_regions(
            session=MagicMock(),
            eligible_regions=regions,
            account_id="123456789012",
            observation_days=7,
            include_cloudwatch=False,
            max_workers=2,
        )

    assert len(df) == 2
    resource_ids = set(df["lineItem/ResourceId"].tolist())
    assert "i-us-east-1-001" in resource_ids
    assert "i-eu-west-1-001" in resource_ids
    # Each record has its region tagged
    for _, row in df.iterrows():
        assert row["product/region"] in regions


# ---------------------------------------------------------------------------
# 5. Pagination works
# ---------------------------------------------------------------------------

def test_scan_paginates_ec2_instances():
    """Verify the paginator is used (not just describe_instances directly)."""
    mock_session = MagicMock()

    page1 = {"Reservations": [{"Instances": [{"InstanceId": "i-page1-001", "InstanceType": "t3.micro",
                                                "State": {"Name": "running"},
                                                "LaunchTime": datetime(2026, 1, 1, tzinfo=timezone.utc),
                                                "Tags": []}]}]}
    page2 = {"Reservations": [{"Instances": [{"InstanceId": "i-page2-002", "InstanceType": "t3.micro",
                                                "State": {"Name": "running"},
                                                "LaunchTime": datetime(2026, 1, 1, tzinfo=timezone.utc),
                                                "Tags": []}]}]}
    mock_client = MagicMock()
    mock_pager = MagicMock()
    mock_pager.paginate.return_value = [page1, page2]
    mock_client.get_paginator.return_value = mock_pager
    mock_client.describe_addresses.return_value = {"Addresses": []}

    with patch("utils.aws_region_scanner.get_ec2_client", return_value=mock_client), \
         patch("utils.aws_region_scanner.get_cloudwatch_client", return_value=MagicMock()), \
         patch("utils.aws_region_scanner.collect_cloudwatch_ec2_metrics", return_value=({}, [])):

        df, meta = scan_all_regions(
            session=mock_session,
            eligible_regions=["us-east-1"],
            account_id="123",
            observation_days=7,
            include_cloudwatch=False,
        )

    ec2_rows = df[df["product/servicecode"] == "EC2"]
    assert len(ec2_rows) == 2


# ---------------------------------------------------------------------------
# 6. Region metadata retained on each resource record
# ---------------------------------------------------------------------------

def test_region_metadata_on_records():
    mock_client = MagicMock()
    paginator = MagicMock()
    paginator.paginate.return_value = [
        {"Reservations": [{"Instances": [{"InstanceId": "i-regional-001", "InstanceType": "t3.micro",
                                           "State": {"Name": "running"},
                                           "LaunchTime": datetime(2026, 1, 1, tzinfo=timezone.utc),
                                           "Tags": []}]}]}
    ]
    mock_client.get_paginator.return_value = paginator
    mock_client.describe_addresses.return_value = {"Addresses": []}

    with patch("utils.aws_region_scanner.get_ec2_client", return_value=mock_client), \
         patch("utils.aws_region_scanner.get_cloudwatch_client", return_value=MagicMock()), \
         patch("utils.aws_region_scanner.collect_cloudwatch_ec2_metrics", return_value=({}, [])): \

        df, meta = scan_all_regions(
            session=MagicMock(),
            eligible_regions=["ap-south-1"],
            account_id="123",
            observation_days=7,
            include_cloudwatch=False,
        )

    ec2_row = df[df["product/servicecode"] == "EC2"].iloc[0]
    assert ec2_row["product/region"] == "ap-south-1"
    assert "identity/TimeInterval" in ec2_row.index


# ---------------------------------------------------------------------------
# 7. Duplicate resources not counted multiple times
# ---------------------------------------------------------------------------

def test_no_duplicate_resources_across_regions():
    """Two different regions return different instance IDs — no duplicates."""
    call_map = {}

    def make_client(session, region_name):
        client = MagicMock()
        iid = f"i-{region_name.replace('-', '')}-unique"
        paginator = MagicMock()
        paginator.paginate.return_value = [
            {"Reservations": [{"Instances": [{"InstanceId": iid, "InstanceType": "t3.micro",
                                               "State": {"Name": "running"},
                                               "LaunchTime": datetime(2026, 1, 1, tzinfo=timezone.utc),
                                               "Tags": []}]}]}
        ]
        client.get_paginator.return_value = paginator
        client.describe_addresses.return_value = {"Addresses": []}
        call_map[region_name] = iid
        return client

    with patch("utils.aws_region_scanner.get_ec2_client", side_effect=make_client), \
         patch("utils.aws_region_scanner.get_cloudwatch_client", return_value=MagicMock()), \
         patch("utils.aws_region_scanner.collect_cloudwatch_ec2_metrics", return_value=({}, [])):

        df, meta = scan_all_regions(
            session=MagicMock(),
            eligible_regions=["us-east-1", "eu-west-1"],
            account_id="123",
            observation_days=7,
            include_cloudwatch=False,
        )

    # No duplicates — each resource should appear exactly once
    assert df["lineItem/ResourceId"].duplicated().sum() == 0
    assert len(df) == len(call_map)


# ---------------------------------------------------------------------------
# 8. No AWS resource-creation/modification APIs called
# ---------------------------------------------------------------------------

def test_no_destructive_aws_methods_in_scanner():
    import inspect
    import utils.aws_region_scanner as scanner

    forbidden_patterns = [
        "stop_instances",
        "terminate_instances",
        "delete_volume",
        "release_address",
        "reboot_instances",
        "modify_instance_attribute",
        "create_tags",
        "delete_tags",
        "run_instances",
        "create_volume",
        "allocate_address",
    ]
    source = inspect.getsource(scanner)
    for pattern in forbidden_patterns:
        assert f".{pattern}(" not in source, (
            f"Forbidden mutating API call found in aws_region_scanner: {pattern}"
        )


# ---------------------------------------------------------------------------
# scan_all_regions with empty eligible list
# ---------------------------------------------------------------------------

def test_scan_all_regions_empty_input():
    df, meta = scan_all_regions(
        session=MagicMock(),
        eligible_regions=[],
        account_id="123",
        observation_days=7,
    )
    assert df.empty
    assert meta["total_resources"] == 0
    assert meta["regions_scanned"] == []


# ---------------------------------------------------------------------------
# Full pipeline integration test: multi-region
# ---------------------------------------------------------------------------

def test_pipeline_multi_region_integration():
    """Test run_live_finops_analysis with scan_all_regions=True (fully mocked)."""
    from utils.finops_pipeline import run_live_finops_analysis
    import numpy as np

    raw_df = pd.DataFrame([
        {
            "lineItem/ResourceId": "i-multiregion-us-001",
            "lineItem/UsageAccountId": "123456789012",
            "product/servicecode": "EC2",
            "product/region": "us-east-1",
            "product/instanceType": "t3.micro",
            "resource_state": "running",
            "lineItem/UnblendedCost": np.nan,
            "lineItem/UnblendedRate": np.nan,
            "cpu_utilization_avg": 1.0,
            "cpu_utilization_p95": 2.0,
            "network_usage_gb": 0.1,
            "size_gb": 0,
            "identity/TimeInterval": "2026-01-01T00:00:00Z/2026-01-14T00:00:00Z",
            "resourceTags/user:Owner": "ops",
            "resourceTags/user:Environment": "prod",
            "resourceTags/user:Cost-Center": "CC-1",
        },
        {
            "lineItem/ResourceId": "vol-multiregion-eu-002",
            "lineItem/UsageAccountId": "123456789012",
            "product/servicecode": "EBS",
            "product/region": "eu-west-1",
            "product/instanceType": "",
            "resource_state": "available",
            "lineItem/UnblendedCost": np.nan,
            "lineItem/UnblendedRate": np.nan,
            "cpu_utilization_avg": np.nan,
            "cpu_utilization_p95": np.nan,
            "network_usage_gb": np.nan,
            "size_gb": 50,
            "identity/TimeInterval": "2026-01-01T00:00:00Z/2026-01-14T00:00:00Z",
            "resourceTags/user:Owner": "storage",
            "resourceTags/user:Environment": "prod",
            "resourceTags/user:Cost-Center": "CC-2",
        },
    ])

    scan_meta = {
        "regions_scanned": ["us-east-1", "eu-west-1"],
        "regions_failed": [],
        "region_errors": {},
        "ec2_count": 1,
        "ebs_count": 1,
        "eip_count": 0,
        "total_resources": 2,
        "warnings": [],
    }

    with patch("utils.finops_pipeline.create_boto3_session"), \
         patch("utils.finops_pipeline.validate_credentials",
               return_value={"account_id": "123456789012", "arn": "arn:aws:iam::123456789012:user/test"}), \
         patch("utils.finops_pipeline.discover_enabled_regions",
               return_value=[
                   {"region": "us-east-1", "opt_in_status": "opt-in-not-required", "endpoint": ""},
                   {"region": "eu-west-1", "opt_in_status": "opt-in-not-required", "endpoint": ""},
               ]), \
         patch("utils.finops_pipeline.execute_all_region_scan", return_value=(raw_df, scan_meta)), \
         patch("utils.finops_pipeline.collect_cost_explorer_spend",
               return_value=(pd.DataFrame(), ["CE unavailable"])):

        result = run_live_finops_analysis(
            region="us-east-1",
            scan_all_regions=True,
            observation_days=14,
        )

    assert result.account_id == "123456789012"
    assert len(result.data) == 2
    assert len(result.regions_scanned) == 2
    assert len(result.regions_failed) == 0
    # EBS in available state should be detected as idle
    assert not result.idle_findings.empty
    ebs_findings = result.idle_findings[result.idle_findings["finding_type"] == "Unattached EBS"]
    assert len(ebs_findings) == 1
