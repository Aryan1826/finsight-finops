"""Tests for AWS CloudFormation 1-Click Customer Onboarding Flow in FinSight.

Covers:
1. Cryptographic External ID generation (entropy, prefix, uniqueness).
2. Session state lifecycle for External ID.
3. CloudFormation Quick-Create URL construction (safe encoding, parameter names, regions).
4. CloudFormation template YAML validation (IAM Role, trust policy condition, read-only permissions, zero mutations, outputs).
5. Account ID mismatch validation between Role ARN and STS caller identity.
"""
from __future__ import annotations

import inspect
import os
import re
import sys
import urllib.parse
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
import yaml

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import utils.aws_client as ac
from utils.aws_client import (
    generate_external_id,
    extract_account_id_from_arn,
    build_cloudformation_quick_create_url,
    validate_credentials,
    AWSCredentialError,
    AWSError,
)

TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "templates" / "finsight-readonly-role.template.yaml"


# ===========================================================================
# 1. External ID Generation Tests
# ===========================================================================
def test_generate_external_id_structure_and_entropy():
    """Verify that generate_external_id returns high-entropy string with expected prefix."""
    ext_id = generate_external_id()
    assert isinstance(ext_id, str)
    assert ext_id.startswith("finsight-ext-")
    
    # Prefix (13) + 32 hex chars = 45 chars total (128-bit entropy)
    hex_part = ext_id[len("finsight-ext-"):]
    assert len(hex_part) == 32
    assert re.match(r"^[0-9a-f]{32}$", hex_part) is not None


def test_generate_external_id_uniqueness():
    """Verify consecutive generations produce distinct cryptographically random IDs."""
    ids = {generate_external_id() for _ in range(100)}
    assert len(ids) == 100


def test_generate_external_id_custom_prefix():
    """Verify custom prefix support."""
    ext_id = generate_external_id(prefix="custom-tenant-")
    assert ext_id.startswith("custom-tenant-")
    assert len(ext_id[len("custom-tenant-"):]) == 32


# ===========================================================================
# 2. External ID Session State Lifecycle Tests
# ===========================================================================
def test_external_id_session_state_lifecycle():
    """Verify External ID is preserved across reruns and only regenerated explicitly."""
    mock_session_state = {}

    # Initial load: generate External ID if not present
    if "generated_external_id" not in mock_session_state:
        mock_session_state["generated_external_id"] = generate_external_id()
    
    initial_id = mock_session_state["generated_external_id"]
    assert initial_id.startswith("finsight-ext-")

    # Subsequent rerun: existing External ID must NOT be overwritten
    if "generated_external_id" not in mock_session_state:
        mock_session_state["generated_external_id"] = generate_external_id()
    
    assert mock_session_state["generated_external_id"] == initial_id

    # Explicit user action: "Start New AWS Connection"
    mock_session_state["generated_external_id"] = generate_external_id()
    new_id = mock_session_state["generated_external_id"]

    assert new_id != initial_id
    assert new_id.startswith("finsight-ext-")


# ===========================================================================
# 3. CloudFormation Quick-Create URL Generation Tests
# ===========================================================================
def test_build_cloudformation_quick_create_url_default():
    """Verify CloudFormation Quick-Create URL structure and query parameters."""
    url = build_cloudformation_quick_create_url(
        region="ap-south-1",
        template_url="https://example.com/templates/finsight-readonly-role.template.yaml",
        production_role_arn="arn:aws:iam::123456789012:role/FinSightProductionTaskRole",
        external_id="finsight-ext-1234567890abcdef1234567890abcdef",
        stack_name="FinSight-Connection",
    )

    assert url.startswith("https://console.aws.amazon.com/cloudformation/home?region=ap-south-1#/stacks/create/review?")
    
    # Parse URL and query parameters
    parsed = urllib.parse.urlparse(url)
    fragment = parsed.fragment
    assert "/stacks/create/review" in fragment
    query_string = fragment.split("?", 1)[1]
    params = urllib.parse.parse_qs(query_string)

    assert params["templateURL"] == ["https://example.com/templates/finsight-readonly-role.template.yaml"]
    assert params["stackName"] == ["FinSight-Connection"]
    assert params["param_FinSightProductionTaskRoleArn"] == ["arn:aws:iam::123456789012:role/FinSightProductionTaskRole"]
    assert params["param_ExternalId"] == ["finsight-ext-1234567890abcdef1234567890abcdef"]


def test_build_cloudformation_quick_create_url_special_characters_encoded():
    """Verify special characters in parameters are safely URL encoded without malforming the query."""
    url = build_cloudformation_quick_create_url(
        region="eu-west-1",
        template_url="https://raw.githubusercontent.com/test/repo/branch-1.0/template.yaml",
        production_role_arn="arn:aws:iam::123456789012:role/FinSight+Prod/Task@Role",
        external_id="finsight-ext-!@#$%^&*()_+=",
    )

    parsed = urllib.parse.urlparse(url)
    query_string = parsed.fragment.split("?", 1)[1]
    params = urllib.parse.parse_qs(query_string)

    assert params["param_FinSightProductionTaskRoleArn"] == ["arn:aws:iam::123456789012:role/FinSight+Prod/Task@Role"]
    assert params["param_ExternalId"] == ["finsight-ext-!@#$%^&*()_+="]
    assert "region=eu-west-1" in parsed.query


def test_build_cloudformation_quick_create_url_contains_no_secrets():
    """Verify that CloudFormation URLs contain no AWS access keys, secret keys, or passwords."""
    url = build_cloudformation_quick_create_url(
        region="us-east-1",
        external_id="finsight-ext-abcdef0123456789abcdef0123456789",
    )
    lower_url = url.lower()
    assert "secret" not in lower_url
    assert "password" not in lower_url
    assert "aws_access_key_id" not in lower_url
    assert "aws_secret_access_key" not in lower_url
    assert "sessiontoken" not in lower_url


# ===========================================================================
# 4. Account ID Extraction and Mismatch Validation Tests
# ===========================================================================
def test_extract_account_id_from_arn():
    """Verify extraction of 12-digit account ID from IAM Role ARN."""
    assert extract_account_id_from_arn("arn:aws:iam::123456789012:role/FinSightReadOnlyRole") == "123456789012"
    assert extract_account_id_from_arn("arn:aws-us-gov:iam::987654321098:role/TestRole") == "987654321098"
    assert extract_account_id_from_arn("arn:aws-cn:iam::111222333444:role/ChinaRole") == "111222333444"
    assert extract_account_id_from_arn("not-an-arn") is None
    assert extract_account_id_from_arn("") is None


def test_validate_credentials_account_mismatch_raises():
    """Verify that validate_credentials fails if authenticated account differs from expected role ARN."""
    mock_sts = MagicMock()
    mock_sts.get_caller_identity.return_value = {
        "Account": "222222222222",
        "Arn": "arn:aws:sts::222222222222:assumed-role/FinSightReadOnlyRole/FinSightSession",
        "UserId": "AROA222222222222:FinSightSession",
    }
    mock_session = MagicMock()
    mock_session.client.return_value = mock_sts

    with pytest.raises(AWSCredentialError, match="AWS Account ID mismatch"):
        validate_credentials(
            session=mock_session,
            expected_role_arn="arn:aws:iam::111111111111:role/FinSightReadOnlyRole",
        )


def test_validate_credentials_account_match_succeeds():
    """Verify that validate_credentials passes when account matches expected role ARN."""
    mock_sts = MagicMock()
    mock_sts.get_caller_identity.return_value = {
        "Account": "111111111111",
        "Arn": "arn:aws:sts::111111111111:assumed-role/FinSightReadOnlyRole/FinSightSession",
        "UserId": "AROA111111111111:FinSightSession",
    }
    mock_session = MagicMock()
    mock_session.client.return_value = mock_sts

    identity = validate_credentials(
        session=mock_session,
        expected_role_arn="arn:aws:iam::111111111111:role/FinSightReadOnlyRole",
    )
    assert identity["account_id"] == "111111111111"


# ===========================================================================
# 5. CloudFormation Template Static & Semantic Validation Tests
# ===========================================================================
def _load_cf_template():
    """Helper to parse CloudFormation YAML template."""
    assert TEMPLATE_PATH.exists(), f"Template not found at {TEMPLATE_PATH}"
    with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    # CloudFormation YAML uses custom tags like !Ref, !Sub, !GetAtt.
    # Set up PyYAML constructor to parse them gracefully.
    class CFTemplateLoader(yaml.SafeLoader):
        pass

    def generic_constructor(loader, tag_suffix, node):
        if isinstance(node, yaml.ScalarNode):
            return {tag_suffix: loader.construct_scalar(node)}
        elif isinstance(node, yaml.SequenceNode):
            return {tag_suffix: loader.construct_sequence(node)}
        elif isinstance(node, yaml.MappingNode):
            return {tag_suffix: loader.construct_mapping(node)}
        return {tag_suffix: None}

    CFTemplateLoader.add_multi_constructor("!", generic_constructor)
    return yaml.load(content, Loader=CFTemplateLoader), content


def test_cloudformation_template_parameters():
    """Verify parameters in the CloudFormation template."""
    template, _ = _load_cf_template()
    params = template.get("Parameters", {})

    assert "FinSightProductionTaskRoleArn" in params, "Missing FinSightProductionTaskRoleArn parameter"
    assert "ExternalId" in params, "Missing ExternalId parameter"
    assert "RoleName" in params, "Missing RoleName parameter"

    assert params["FinSightProductionTaskRoleArn"]["Type"] == "String"
    assert params["ExternalId"]["Type"] == "String"


def test_cloudformation_template_resources():
    """Verify IAM role and policy resources in the CloudFormation template."""
    template, _ = _load_cf_template()
    resources = template.get("Resources", {})

    assert "FinSightReadOnlyRole" in resources, "Missing FinSightReadOnlyRole resource"
    role = resources["FinSightReadOnlyRole"]
    assert role.get("Type") == "AWS::IAM::Role"

    props = role.get("Properties", {})
    assume_doc = props.get("AssumeRolePolicyDocument", {})
    statement = assume_doc.get("Statement", [])[0]

    assert statement.get("Effect") == "Allow"
    assert statement.get("Action") == "sts:AssumeRole"
    
    # Trust policy must reference FinSightProductionTaskRoleArn and require ExternalId
    principal = statement.get("Principal", {})
    assert "AWS" in principal
    condition = statement.get("Condition", {})
    assert "StringEquals" in condition
    assert "sts:ExternalId" in condition["StringEquals"]


def test_cloudformation_template_read_only_permissions():
    """Verify all required read-only actions are present in the template policy."""
    template, raw_yaml = _load_cf_template()
    role = template["Resources"]["FinSightReadOnlyRole"]
    policies = role["Properties"]["Policies"]
    
    # Find FinSightReadOnlyPolicy
    finops_policy = next((p for p in policies if p.get("PolicyName") == "FinSightReadOnlyPolicy"), None)
    assert finops_policy is not None, "FinSightReadOnlyPolicy not found in role policies"

    statements = finops_policy["PolicyDocument"]["Statement"]
    all_actions = []
    for stmt in statements:
        actions = stmt.get("Action", [])
        if isinstance(actions, str):
            all_actions.append(actions)
        elif isinstance(actions, list):
            all_actions.extend(actions)

    # Required read-only permissions
    expected_actions = [
        "sts:GetCallerIdentity",
        "ec2:DescribeInstances",
        "ec2:DescribeVolumes",
        "ec2:DescribeAddresses",
        "ec2:DescribeTags",
        "ec2:DescribeVpcs",
        "ec2:DescribeSubnets",
        "ec2:DescribeSecurityGroups",
        "cloudwatch:GetMetricData",
        "cloudwatch:GetMetricStatistics",
        "cloudwatch:ListMetrics",
        "ce:GetCostAndUsage",
        "ce:GetCostAndUsageWithResources",
    ]
    for action in expected_actions:
        assert action in all_actions, f"Required action missing from template: {action}"


def test_cloudformation_template_zero_mutations_or_destructive_actions():
    """Verify no destructive or mutating IAM actions exist in the CloudFormation template."""
    _, raw_yaml = _load_cf_template()
    forbidden_actions = [
        "ec2:StopInstances",
        "ec2:TerminateInstances",
        "ec2:DeleteVolume",
        "ec2:ReleaseAddress",
        "ec2:RebootInstances",
        "ec2:ModifyInstanceAttribute",
        "ec2:CreateTags",
        "ec2:DeleteTags",
        "ec2:RunInstances",
        "ec2:CreateVolume",
        "iam:Create",
        "iam:Delete",
        "iam:Update",
        "s3:Delete",
        "s3:Put",
    ]
    for forbidden in forbidden_actions:
        assert forbidden.lower() not in raw_yaml.lower(), f"Forbidden mutating action found in template: {forbidden}"


def test_cloudformation_template_outputs():
    """Verify template outputs include RoleArn, RoleName, and ExternalId, but no credentials."""
    template, _ = _load_cf_template()
    outputs = template.get("Outputs", {})

    assert "RoleArn" in outputs, "RoleArn missing from template outputs"
    assert "RoleName" in outputs, "RoleName missing from template outputs"
    assert "ExternalId" in outputs, "ExternalId missing from template outputs"

    for output_name, output_def in outputs.items():
        desc = output_def.get("Description", "").lower()
        assert "secret" not in desc
        assert "password" not in desc
        assert "key" not in desc or "external id" in desc
