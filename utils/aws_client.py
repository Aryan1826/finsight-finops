"""AWS client factory and credential validation for FinSight.

Strictly READ-ONLY: This module only creates clients for querying metadata,
metrics, and billing information. It contains no destructive operations.
Supports Local AWS Profile, Cross-Account STS AssumeRole, and direct AWS Access Key credentials.
"""
from __future__ import annotations

import os
import re
import secrets
import urllib.parse
from typing import Any, Dict, Optional, Tuple
import boto3
import botocore.exceptions

DEFAULT_CLOUDFORMATION_TEMPLATE_URL = os.environ.get(
    "CLOUDFORMATION_TEMPLATE_URL",
    "https://raw.githubusercontent.com/aryanrangani/finsight_finops/main/templates/finsight-readonly-role.template.yaml",
)

DEFAULT_FINSIGHT_PRODUCTION_TASK_ROLE_ARN = os.environ.get(
    "FINSIGHT_PRODUCTION_TASK_ROLE_ARN",
    "arn:aws:iam::123456789012:role/FinSightProductionTaskRole",
)


class AWSError(Exception):
    """Base exception for FinSight AWS integration errors."""
    pass


class AWSCredentialError(AWSError):
    """Raised when AWS credentials cannot be located, are invalid, or expired."""
    pass


class AWSPermissionError(AWSError):
    """Raised when AWS IAM permissions are insufficient for read-only actions."""
    pass


def generate_external_id(prefix: str = "finsight-ext-") -> str:
    """Generate a cryptographically secure random External ID for cross-account IAM roles."""
    return f"{prefix}{secrets.token_hex(16)}"


def extract_account_id_from_arn(arn: str) -> Optional[str]:
    """Extract the 12-digit AWS Account ID from an IAM Role or Resource ARN."""
    if not arn:
        return None
    match = re.match(r"^arn:aws[a-z\-]*:(?:iam|sts)::(\d{12}):", arn.strip())
    return match.group(1) if match else None


def build_cloudformation_quick_create_url(
    region: str = "us-east-1",
    template_url: Optional[str] = None,
    production_role_arn: Optional[str] = None,
    external_id: Optional[str] = None,
    stack_name: str = "FinSight-Connection",
) -> str:
    """Build an AWS CloudFormation Quick-Create review URL with pre-filled parameters.

    Formats:
    https://console.aws.amazon.com/cloudformation/home?region=<REGION>#/stacks/create/review?templateURL=...&stackName=...&param_FinSightProductionTaskRoleArn=...&param_ExternalId=...
    """
    clean_region = region.strip() if region and region.strip() else "us-east-1"
    effective_template_url = (
        template_url.strip() if template_url and template_url.strip() else DEFAULT_CLOUDFORMATION_TEMPLATE_URL
    )
    effective_role_arn = (
        production_role_arn.strip()
        if production_role_arn and production_role_arn.strip()
        else DEFAULT_FINSIGHT_PRODUCTION_TASK_ROLE_ARN
    )
    clean_stack_name = stack_name.strip() if stack_name and stack_name.strip() else "FinSight-Connection"
    clean_external_id = external_id.strip() if external_id and external_id.strip() else ""

    params = {
        "templateURL": effective_template_url,
        "stackName": clean_stack_name,
        "param_FinSightProductionTaskRoleArn": effective_role_arn,
        "param_ExternalId": clean_external_id,
    }
    encoded_query = urllib.parse.urlencode(params)
    return f"https://console.aws.amazon.com/cloudformation/home?region={clean_region}#/stacks/create/review?{encoded_query}"


def validate_aws_region(region: Optional[str]) -> str:
    """Validate AWS region name string format. Returns clean region name or raises AWSCredentialError."""
    if not region or not str(region).strip():
        return "ap-south-1"
    clean = str(region).strip().lower()
    # AWS region format: e.g. ap-south-1, us-east-1, us-gov-west-1, cn-north-1
    if not re.match(r"^[a-z]{2}(-[a-z]+)+-\d+$", clean):
        raise AWSCredentialError(
            f"Invalid AWS region format: '{clean}'. Expected format like 'ap-south-1' or 'us-east-1'."
        )
    return clean


def _validate_role_arn_structure(role_arn: str) -> None:
    """Validate that role ARN conforms to standard AWS IAM Role ARN structure."""
    # Pattern matching standard AWS IAM role ARNs (e.g. arn:aws:iam::123456789012:role/RoleName)
    arn_pattern = r"^arn:aws[a-z\-]*:iam::\d{12}:role\/[\w+=,.@\/-]+$"
    if not re.match(arn_pattern, role_arn):
        raise AWSCredentialError(
            f"Invalid AWS Role ARN format: '{role_arn}'. "
            "Expected format: 'arn:aws:iam::<12-digit-account-id>:role/<RoleName>'."
        )


def create_boto3_session(
    profile_name: Optional[str] = None,
    region_name: Optional[str] = None,
    role_arn: Optional[str] = None,
    external_id: Optional[str] = None,
    role_session_name: Optional[str] = None,
    aws_access_key_id: Optional[str] = None,
    aws_secret_access_key: Optional[str] = None,
    aws_session_token: Optional[str] = None,
) -> boto3.Session:
    """Create a boto3 Session with flexible credential resolution.

    Supports:
    1. Direct AWS Access Key Credentials (when aws_access_key_id or aws_secret_access_key is supplied)
    2. Cross-Account STS AssumeRole (if role_arn is specified)
    3. Named AWS profile (if profile_name is specified)
    4. Default AWS credential chain (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / ~/.aws/credentials)
    5. IAM Role / ECS / EC2 instance metadata credentials
    """
    clean_profile = profile_name.strip() if profile_name and profile_name.strip() else None
    clean_region = region_name.strip() if region_name and region_name.strip() else None
    clean_role_arn = role_arn.strip() if role_arn and role_arn.strip() else None
    clean_external_id = external_id.strip() if external_id and external_id.strip() else None
    clean_session_name = role_session_name.strip() if role_session_name and role_session_name.strip() else "FinSightSession"

    has_key_id = aws_access_key_id is not None
    has_secret = aws_secret_access_key is not None
    clean_key_id = aws_access_key_id.strip() if aws_access_key_id and aws_access_key_id.strip() else ""
    clean_secret = aws_secret_access_key.strip() if aws_secret_access_key and aws_secret_access_key.strip() else ""
    clean_token = aws_session_token.strip() if aws_session_token and aws_session_token.strip() else None

    # 1. AWS Access Key Credentials mode (direct keys supplied)
    if has_key_id or has_secret:
        if not clean_key_id and not clean_secret:
            raise AWSCredentialError("Both AWS Access Key ID and AWS Secret Access Key must be provided.")
        if not clean_key_id:
            raise AWSCredentialError("AWS Access Key ID must be provided when connecting with Access Key credentials.")
        if not clean_secret:
            raise AWSCredentialError("AWS Secret Access Key must be provided when connecting with Access Key credentials.")
        if clean_region:
            validate_aws_region(clean_region)

        # If role_arn is ALSO provided, use access keys as the source session for STS AssumeRole
        if clean_role_arn:
            _validate_role_arn_structure(clean_role_arn)
            if not clean_external_id:
                raise AWSCredentialError(
                    "External ID is required for Cross-Account IAM Role assumption. "
                    "Please provide a valid External ID configured in the role's trust policy."
                )
            try:
                source_kwargs = {
                    "aws_access_key_id": clean_key_id,
                    "aws_secret_access_key": clean_secret,
                    "region_name": clean_region,
                }
                if clean_token:
                    source_kwargs["aws_session_token"] = clean_token
                source_session = boto3.Session(**source_kwargs)
                sts_client = source_session.client("sts", region_name=clean_region or "us-east-1")
                response = sts_client.assume_role(
                    RoleArn=clean_role_arn,
                    RoleSessionName=clean_session_name,
                    ExternalId=clean_external_id,
                )
                creds = response["Credentials"]
                return boto3.Session(
                    aws_access_key_id=creds["AccessKeyId"],
                    aws_secret_access_key=creds["SecretAccessKey"],
                    aws_session_token=creds["SessionToken"],
                    region_name=clean_region,
                )
            except botocore.exceptions.ClientError as e:
                error_code = e.response.get("Error", {}).get("Code", "")
                error_msg = e.response.get("Error", {}).get("Message", str(e))
                if error_code in ("AccessDenied", "AccessDeniedException"):
                    raise AWSPermissionError(
                        "Unable to assume the specified FinSight role. "
                        "Please verify the Role ARN, External ID, trust policy, and IAM permissions."
                    ) from e
                elif error_code in ("InvalidParameterValue", "MalformedPolicyDocument"):
                    raise AWSCredentialError(f"Invalid AssumeRole parameter ({error_code}): {error_msg}") from e
                raise AWSCredentialError(f"STS AssumeRole failed ({error_code}): {error_msg}") from e
            except Exception as e:
                raise AWSCredentialError(f"Unexpected error during STS AssumeRole: {e}") from e

        # Direct Access Key session - strictly uses provided credentials with no fallback
        try:
            sess_kwargs = {
                "aws_access_key_id": clean_key_id,
                "aws_secret_access_key": clean_secret,
                "region_name": clean_region,
            }
            if clean_token:
                sess_kwargs["aws_session_token"] = clean_token
            return boto3.Session(**sess_kwargs)
        except Exception as e:
            raise AWSCredentialError(f"Failed to initialize AWS session from access keys: {e}") from e

    # 2. Cross-Account IAM Role mode via STS AssumeRole
    if clean_role_arn:
        _validate_role_arn_structure(clean_role_arn)
        if not clean_external_id:
            raise AWSCredentialError(
                "External ID is required for Cross-Account IAM Role assumption. "
                "Please provide a valid External ID configured in the role's trust policy."
            )

        # Build the source base session
        try:
            if clean_profile:
                source_session = boto3.Session(profile_name=clean_profile, region_name=clean_region)
            else:
                source_session = boto3.Session(region_name=clean_region)
        except botocore.exceptions.ProfileNotFound as e:
            raise AWSCredentialError(
                f"Source AWS profile '{clean_profile}' was not found. "
                "Please check your configured profiles in ~/.aws/credentials."
            ) from e
        except Exception as e:
            raise AWSCredentialError(f"Failed to initialize source AWS session for AssumeRole: {e}") from e

        try:
            sts_client = source_session.client("sts", region_name=clean_region or "us-east-1")
            response = sts_client.assume_role(
                RoleArn=clean_role_arn,
                RoleSessionName=clean_session_name,
                ExternalId=clean_external_id,
            )
            creds = response["Credentials"]
            # Create isolated temporary session without logging credentials
            temp_session = boto3.Session(
                aws_access_key_id=creds["AccessKeyId"],
                aws_secret_access_key=creds["SecretAccessKey"],
                aws_session_token=creds["SessionToken"],
                region_name=clean_region,
            )
            return temp_session
        except botocore.exceptions.ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            error_msg = e.response.get("Error", {}).get("Message", str(e))
            if error_code in ("AccessDenied", "AccessDeniedException"):
                raise AWSPermissionError(
                    "Unable to assume the specified FinSight role. "
                    "Please verify the Role ARN, External ID, trust policy, and IAM permissions."
                ) from e
            elif error_code in ("InvalidParameterValue", "MalformedPolicyDocument"):
                raise AWSCredentialError(f"Invalid AssumeRole parameter ({error_code}): {error_msg}") from e
            raise AWSCredentialError(f"STS AssumeRole failed ({error_code}): {error_msg}") from e
        except Exception as e:
            raise AWSCredentialError(f"Unexpected error during STS AssumeRole: {e}") from e

    # 3. Local Profile / Default Credential Chain mode
    try:
        if clean_profile:
            return boto3.Session(profile_name=clean_profile, region_name=clean_region)
        return boto3.Session(region_name=clean_region)
    except botocore.exceptions.ProfileNotFound as e:
        raise AWSCredentialError(
            f"AWS profile '{clean_profile}' was not found in ~/.aws/credentials or ~/.aws/config. "
            "Please check your configured profiles or leave profile empty for default credentials."
        ) from e
    except Exception as e:
        raise AWSCredentialError(f"Failed to initialize AWS session: {e}") from e


def get_sts_client(
    session: Optional[boto3.Session] = None,
    region_name: Optional[str] = None,
) -> Any:
    """Get an STS client for credential validation and caller identity."""
    sess = session or create_boto3_session(region_name=region_name)
    return sess.client("sts", region_name=region_name)


def get_ec2_client(
    session: Optional[boto3.Session] = None,
    region_name: Optional[str] = None,
) -> Any:
    """Get an EC2 client for read-only resource discovery."""
    sess = session or create_boto3_session(region_name=region_name)
    return sess.client("ec2", region_name=region_name)


def get_cloudwatch_client(
    session: Optional[boto3.Session] = None,
    region_name: Optional[str] = None,
) -> Any:
    """Get a CloudWatch client for metric data collection."""
    sess = session or create_boto3_session(region_name=region_name)
    return sess.client("cloudwatch", region_name=region_name)


def get_ce_client(
    session: Optional[boto3.Session] = None,
    region_name: Optional[str] = None,
) -> Any:
    """Get an AWS Cost Explorer client.

    Note: Cost Explorer endpoint is global (us-east-1).
    """
    sess = session or create_boto3_session(region_name=region_name)
    # Cost Explorer API is routed through us-east-1 by convention
    effective_region = region_name or sess.region_name or "us-east-1"
    return sess.client("ce", region_name=effective_region)


def validate_credentials(
    session: Optional[boto3.Session] = None,
    region_name: Optional[str] = None,
    expected_role_arn: Optional[str] = None,
) -> Dict[str, str]:
    """Validate active AWS credentials using sts:GetCallerIdentity.

    Returns:
        Dict with keys: 'account_id', 'arn', 'user_id'

    Raises:
        AWSCredentialError: If credentials are missing, expired, or invalid.
        AWSPermissionError: If sts:GetCallerIdentity is blocked.
        AWSError: On network failure or AWS STS service failure.
    """
    effective_region = validate_aws_region(region_name) if region_name else None
    try:
        sts = get_sts_client(session=session, region_name=effective_region)
        caller = sts.get_caller_identity()
        caller_account = str(caller.get("Account", ""))
        caller_arn = str(caller.get("Arn", ""))
        caller_user_id = str(caller.get("UserId", ""))

        # If an expected role ARN was provided, verify the account matches
        if expected_role_arn:
            expected_account = extract_account_id_from_arn(expected_role_arn)
            if expected_account and caller_account != expected_account:
                raise AWSCredentialError(
                    f"AWS Account ID mismatch: The authenticated session belongs to account '{caller_account}', "
                    f"but the provided Role ARN specified account '{expected_account}'."
                )

        return {
            "account_id": caller_account,
            "arn": caller_arn,
            "user_id": caller_user_id,
        }
    except botocore.exceptions.NoCredentialsError as e:
        raise AWSCredentialError(
            "No AWS credentials found. Please configure ~/.aws/credentials, "
            "set AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY environment variables, "
            "or attach an IAM role."
        ) from e
    except botocore.exceptions.ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "")
        error_msg = e.response.get("Error", {}).get("Message", str(e))
        if error_code in (
            "AuthFailure",
            "InvalidClientTokenId",
            "SignatureDoesNotMatch",
            "UnrecognizedClientException",
            "IncompleteSignature",
            "InvalidSignatureException",
            "MissingAuthenticationToken",
        ):
            raise AWSCredentialError(f"AWS authentication failed ({error_code}): {error_msg}") from e
        elif error_code in ("ExpiredToken", "RequestExpired"):
            raise AWSCredentialError(f"AWS authentication failed ({error_code}): Credentials or token have expired.") from e
        elif error_code in ("AccessDenied", "AccessDeniedException"):
            raise AWSPermissionError(f"Access denied calling sts:GetCallerIdentity ({error_code}): {error_msg}") from e
        raise AWSError(f"AWS STS error ({error_code}): {error_msg}") from e
    except botocore.exceptions.EndpointConnectionError as e:
        raise AWSError(
            f"Network error: Unable to connect to AWS STS endpoint"
            + (f" in region '{effective_region}'" if effective_region else "")
            + ". Please verify network connectivity and region name."
        ) from e
    except (botocore.exceptions.ConnectTimeoutError, botocore.exceptions.ReadTimeoutError) as e:
        raise AWSError(
            "Network timeout: Connection to AWS STS endpoint timed out. Please check your network connection."
        ) from e
    except (AWSCredentialError, AWSPermissionError, AWSError):
        raise
    except Exception as e:
        raise AWSCredentialError(f"Failed to validate AWS credentials: {e}") from e


def authenticate_with_access_keys(
    aws_access_key_id: Optional[str],
    aws_secret_access_key: Optional[str],
    aws_session_token: Optional[str] = None,
    region_name: Optional[str] = "ap-south-1",
) -> Tuple[boto3.Session, Dict[str, str]]:
    """Establish and validate a boto3 session using manually entered AWS Access Keys.

    Strictly non-persistent: Credentials are never written to disk, files, databases,
    or logged. Authenticates immediately via sts:GetCallerIdentity.

    When credentials are explicitly supplied, does not fall back to local profile,
    environment credentials, or another AWS identity if authentication fails.

    Returns:
        Tuple of (boto3.Session, Dict[str, str])
        Dict contains: 'account_id', 'arn', 'user_id', 'region', 'auth_method'

    Raises:
        AWSCredentialError: If keys are missing, invalid, or expired.
        AWSPermissionError: If sts:GetCallerIdentity is blocked by IAM policy.
        AWSError: On network failure or AWS STS service failure.
    """
    clean_key_id = str(aws_access_key_id).strip() if aws_access_key_id is not None and str(aws_access_key_id).strip() else ""
    clean_secret = str(aws_secret_access_key).strip() if aws_secret_access_key is not None and str(aws_secret_access_key).strip() else ""
    clean_token = str(aws_session_token).strip() if aws_session_token is not None and str(aws_session_token).strip() else None

    if not clean_key_id and not clean_secret:
        raise AWSCredentialError("Both AWS Access Key ID and Secret Access Key must be provided.")
    if not clean_key_id:
        raise AWSCredentialError("AWS Access Key ID must be provided.")
    if not clean_secret:
        raise AWSCredentialError("AWS Secret Access Key must be provided.")

    effective_region = validate_aws_region(region_name)

    session = create_boto3_session(
        aws_access_key_id=clean_key_id,
        aws_secret_access_key=clean_secret,
        aws_session_token=clean_token,
        region_name=effective_region,
    )

    caller = validate_credentials(session=session, region_name=effective_region)
    identity_data = {
        "account_id": caller["account_id"],
        "arn": caller["arn"],
        "user_id": caller["user_id"],
        "region": effective_region,
        "auth_method": "AWS Access Key Credentials",
    }
    return session, identity_data


def test_aws_connection(
    profile_name: Optional[str] = None,
    region_name: Optional[str] = None,
    role_arn: Optional[str] = None,
    external_id: Optional[str] = None,
    role_session_name: Optional[str] = None,
    aws_access_key_id: Optional[str] = None,
    aws_secret_access_key: Optional[str] = None,
    aws_session_token: Optional[str] = None,
) -> Dict[str, str]:
    """Test and validate AWS connection parameters and return caller identity.

    Performs preflight validation across all authentication modes:
    1. AWS Access Key Credentials (if aws_access_key_id or aws_secret_access_key is provided)
    2. Cross-Account IAM Role (if role_arn is specified)
    3. Local AWS Profile / Default Credential chain

    Returns:
        Dict with keys: 'account_id', 'arn', 'user_id', 'region', 'auth_method'

    Raises:
        AWSCredentialError: If role ARN, external ID, or credentials are invalid.
        AWSPermissionError: If STS AssumeRole or GetCallerIdentity permissions are denied.
    """
    if aws_access_key_id is not None or aws_secret_access_key is not None:
        _, identity = authenticate_with_access_keys(
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
            aws_session_token=aws_session_token,
            region_name=region_name or "ap-south-1",
        )
        return identity

    session = create_boto3_session(
        profile_name=profile_name,
        region_name=region_name,
        role_arn=role_arn,
        external_id=external_id,
        role_session_name=role_session_name,
    )
    caller = validate_credentials(session=session, region_name=region_name, expected_role_arn=role_arn)
    caller["region"] = region_name or session.region_name or "us-east-1"
    caller["auth_method"] = "Cross-Account IAM Role" if role_arn else ("Local AWS Profile" if profile_name else "Default Credentials")
    return caller

