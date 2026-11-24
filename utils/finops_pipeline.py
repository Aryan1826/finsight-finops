"""Unified FinOps analysis pipeline connecting Live AWS Ingestion to core analysis engines.

Strictly READ-ONLY:
Executes: Live AWS Collector -> Normalizer -> Existing Analysis Engines -> Unified Results.
Under no circumstances does this module modify, create, stop, or delete AWS resources.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional
import math
import numpy as np
import pandas as pd

from utils.aws_client import (
    create_boto3_session,
    validate_credentials,
    AWSCredentialError,
    AWSError,
)
from utils.aws_live_collector import collect_live_aws_inventory, collect_cost_explorer_spend
from utils.aws_region_scanner import (
    discover_enabled_regions,
    filter_eligible_regions,
    scan_all_regions as execute_all_region_scan,
    REGION_STATUS_OPT_IN_NOT_REQUIRED,
    REGION_STATUS_OPTED_IN,
    REGION_STATUS_NOT_OPTED_IN,
)
from utils.idle_detector import detect_idle_resources
from utils.normalizer import normalize_live_dataframe, validate_canonical_schema
from utils.rightsizing import recommend_rightsizing
from utils.tagging_policy import evaluate_tagging_compliance, load_policy


@dataclass
class FinOpsAnalysisResult:
    """Structured representation of a FinOps analysis execution."""
    account_id: str
    arn: str
    region: str
    observation_days: int
    data: pd.DataFrame
    idle_findings: pd.DataFrame
    rightsizing_findings: pd.DataFrame
    tagging_report: pd.DataFrame
    tagging_compliance_pct: float
    compliant_count: int
    noncompliant_count: int
    cost_summary: pd.DataFrame
    total_spend_usd: float
    identified_waste_usd: float
    rightsizing_savings_usd: float
    metadata: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    # Multi-region scan metadata
    regions_scanned: List[str] = field(default_factory=list)
    regions_failed: List[str] = field(default_factory=list)
    region_errors: Dict[str, List[str]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert result object to dictionary."""
        return {
            "account_id": self.account_id,
            "arn": self.arn,
            "region": self.region,
            "observation_days": self.observation_days,
            "data": self.data,
            "idle_findings": self.idle_findings,
            "rightsizing_findings": self.rightsizing_findings,
            "tagging_report": self.tagging_report,
            "tagging_compliance_pct": self.tagging_compliance_pct,
            "compliant_count": self.compliant_count,
            "noncompliant_count": self.noncompliant_count,
            "cost_summary": self.cost_summary,
            "total_spend_usd": self.total_spend_usd,
            "identified_waste_usd": self.identified_waste_usd,
            "rightsizing_savings_usd": self.rightsizing_savings_usd,
            "metadata": self.metadata,
            "warnings": self.warnings,
            "regions_scanned": self.regions_scanned,
            "regions_failed": self.regions_failed,
            "region_errors": self.region_errors,
        }


def run_live_finops_analysis(
    profile_name: Optional[str] = None,
    region: str = "us-east-1",
    role_arn: Optional[str] = None,
    external_id: Optional[str] = None,
    role_session_name: Optional[str] = None,
    observation_days: int = 14,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    policy_path: str | Path = "config/tagging_policy.yaml",
    include_cloudwatch: bool = True,
    include_cost_explorer: bool = True,
    aws_access_key_id: Optional[str] = None,
    aws_secret_access_key: Optional[str] = None,
    aws_session_token: Optional[str] = None,
    session: Optional[Any] = None,
    # Multi-region options
    scan_all_regions: bool = False,
    selected_regions: Optional[List[str]] = None,
) -> FinOpsAnalysisResult:
    """Execute the end-to-end Live AWS FinOps analysis pipeline.

    Supports:
    - AWS Access Key Credentials (manual input)
    - Cross-Account IAM Role (AssumeRole)
    - Local AWS Profile / Default Credentials
    - Single-region scan (legacy, default)
    - All-enabled-region scan (``scan_all_regions=True``)
    - Selected-region scan (``selected_regions=[...]``)

    Workflow:
    1. Authenticate & validate AWS identity via STS.
    2. Discover enabled AWS regions (if multi-region mode).
    3. Collect read-only inventory (EC2, EBS, EIP), CloudWatch CPU metrics.
    4. Collect Cost Explorer spend (global, called once).
    5. Normalize records into canonical FinSight schema.
    6. Run reused analysis engines (idle_detector, rightsizing, tagging_policy).
    7. Aggregate findings without fabricating missing per-resource costs.

    Returns:
        FinOpsAnalysisResult dataclass containing normalized data, engine findings,
        Cost Explorer summary, metrics, region scan status, and warnings.
    """
    warnings: List[str] = []

    # 1. Establish AWS session and validate caller identity
    if session is None:
        sess_kwargs: Dict[str, Any] = {
            "profile_name": profile_name,
            "region_name": region,
            "role_arn": role_arn,
            "external_id": external_id,
            "role_session_name": role_session_name,
        }
        if aws_access_key_id is not None or aws_secret_access_key is not None or aws_session_token is not None:
            sess_kwargs["aws_access_key_id"] = aws_access_key_id
            sess_kwargs["aws_secret_access_key"] = aws_secret_access_key
            sess_kwargs["aws_session_token"] = aws_session_token
        session = create_boto3_session(**sess_kwargs)
    caller = validate_credentials(session=session, region_name=region, expected_role_arn=role_arn)
    account_id = caller.get("account_id", "")
    arn = caller.get("arn", "")

    # Track multi-region metadata
    regions_scanned: List[str] = []
    regions_failed: List[str] = []
    region_errors: Dict[str, List[str]] = {}
    all_region_records: List[Dict[str, str]] = []  # from discover_enabled_regions

    # 2. Collect Live AWS data (single or multi-region)
    raw_df = pd.DataFrame()
    collector_meta: Dict[str, Any] = {}

    is_multi_region = scan_all_regions or (selected_regions is not None and len(selected_regions) > 1)

    if is_multi_region:
        # Discover all enabled regions for this account
        try:
            all_region_records = discover_enabled_regions(session, base_region=region)
        except AWSError as e:
            warnings.append(f"Region discovery failed: {e}. Falling back to single-region scan of '{region}'.")
            is_multi_region = False

    if is_multi_region and all_region_records:
        eligible_regions, skipped_regions = filter_eligible_regions(
            all_region_records,
            selected_regions=selected_regions,
        )

        for sr in skipped_regions:
            warnings.append(
                f"Region '{sr['region']}' skipped "
                f"(opt-in status: {sr['opt_in_status']}, reason: {sr.get('skip_reason', 'N/A')})"
            )

        if not eligible_regions:
            warnings.append("No eligible regions found after filtering. Falling back to single-region scan.")
            is_multi_region = False
    elif is_multi_region:
        is_multi_region = False

    if is_multi_region:
        raw_df, scan_meta = execute_all_region_scan(
            session=session,
            eligible_regions=eligible_regions,
            account_id=account_id,
            observation_days=observation_days,
            include_cloudwatch=include_cloudwatch,
        )
        warnings.extend(scan_meta.get("warnings", []))
        regions_scanned = scan_meta.get("regions_scanned", [])
        regions_failed = scan_meta.get("regions_failed", [])
        region_errors = scan_meta.get("region_errors", {})
        collector_meta = {
            "account_id": account_id,
            "arn": arn,
            "region": f"multi-region ({len(regions_scanned)} regions)",
            "observation_days": observation_days,
            "ec2_count": scan_meta.get("ec2_count", 0),
            "ebs_count": scan_meta.get("ebs_count", 0),
            "eip_count": scan_meta.get("eip_count", 0),
            "total_resources": scan_meta.get("total_resources", 0),
            "warnings": scan_meta.get("warnings", []),
            "cost_explorer_summary": pd.DataFrame(),
        }
    else:
        # Single-region legacy path
        raw_df, collector_meta = collect_live_aws_inventory(
            session=session,
            region=region,
            observation_days=observation_days,
            include_cloudwatch=include_cloudwatch,
            include_cost_explorer=include_cost_explorer,
            start_date=start_date,
            end_date=end_date,
        )
        warnings.extend(collector_meta.get("warnings", []))
        regions_scanned = [region] if not raw_df.empty else []

    # 3. Cost Explorer (global billing service — called once, never per-region)
    ce_df = collector_meta.get("cost_explorer_summary", pd.DataFrame())
    if include_cost_explorer and is_multi_region:
        from datetime import datetime, timezone, timedelta as _td
        try:
            ce_client = get_ce_client(session=session, region_name="us-east-1")
            now_utc = datetime.now(timezone.utc)
            ce_start = start_date or (now_utc.date() - timedelta(days=observation_days))
            ce_end = end_date or now_utc.date()
            ce_df, ce_warns = collect_cost_explorer_spend(ce_client, start_date=ce_start, end_date=ce_end)
            warnings.extend(ce_warns)
        except Exception as e:
            warnings.append(f"Cost Explorer query failed unexpectedly: {e}. Spend records unavailable.")
        collector_meta["cost_explorer_summary"] = ce_df

    # 4. Normalize into canonical FinSight schema
    if raw_df.empty:
        normalized_df = pd.DataFrame()
        warnings.append("No EC2, EBS, or Elastic IP resources were discovered in the target region(s).")
    else:
        normalized_df = normalize_live_dataframe(raw_df)
        validation = validate_canonical_schema(normalized_df)
        if not validation["is_valid"]:
            warnings.append(f"Schema validation warning: Missing columns {validation['missing_required']}")

    # 5. Execute Idle Detector Engine
    idle_findings = pd.DataFrame()
    identified_waste_usd = 0.0
    if not normalized_df.empty:
        num_intervals = normalized_df["identity/TimeInterval"].nunique() if "identity/TimeInterval" in normalized_df.columns else 1
        effective_min_days = 1 if num_intervals <= 1 else min(7, observation_days)

        idle_findings = detect_idle_resources(
            normalized_df,
            ec2_cpu_p95_threshold=5.0,
            min_days_observed=effective_min_days,
        )
        if not idle_findings.empty:
            numeric_waste = pd.to_numeric(idle_findings["waste_usd"], errors="coerce")
            identified_waste_usd = float(numeric_waste.dropna().sum()) if numeric_waste.notna().any() else 0.0

        # Surface cost-unavailability warning to users
        if not idle_findings.empty and "cost_data_available" in idle_findings.columns:
            if not idle_findings["cost_data_available"].any():
                warnings.append(
                    "Cost data (lineItem/UnblendedCost) is not available for live AWS inventory data — "
                    "per-resource cost attribution requires a Cost and Usage Report (CUR) export. "
                    "Idle/waste resource detection is still active based on resource state and "
                    "CloudWatch utilisation metrics. Waste USD values are shown as N/A."
                )

    # 6. Execute Rightsizing Engine
    rightsizing_findings = pd.DataFrame()
    rightsizing_savings_usd = 0.0
    if not normalized_df.empty:
        rightsizing_findings = recommend_rightsizing(normalized_df, p95_threshold=20.0)
        if not rightsizing_findings.empty:
            savings_col = pd.to_numeric(rightsizing_findings["estimated_monthly_savings_usd"], errors="coerce")
            rightsizing_savings_usd = float(savings_col.dropna().sum())

    # 7. Execute Tagging Governance Policy Engine
    policy_file = Path(policy_path)
    if policy_file.exists():
        policy = load_policy(policy_file)
    else:
        policy = {"required_tags": ["Owner", "Environment", "Cost-Center"], "allowed_values": {"Environment": ["prod", "staging"]}}
        warnings.append(f"Tagging policy file {policy_path} not found. Used fallback default policy.")

    tagging_report = pd.DataFrame()
    tagging_compliance_pct = 100.0
    compliant_count = 0
    noncompliant_count = 0

    if not normalized_df.empty:
        tagging_report, tagging_compliance_pct = evaluate_tagging_compliance(normalized_df, policy)
        compliant_count = int(tagging_report["compliant"].sum()) if not tagging_report.empty and "compliant" in tagging_report.columns else 0
        noncompliant_count = len(tagging_report) - compliant_count

    # 8. Extract Cost Explorer spend
    cost_summary_df = collector_meta.get("cost_explorer_summary", pd.DataFrame())
    total_spend_usd = 0.0
    if not cost_summary_df.empty and "unblended_cost_usd" in cost_summary_df.columns:
        raw_sum = float(cost_summary_df["unblended_cost_usd"].sum())
        total_spend_usd = 0.0 if abs(raw_sum) < 0.001 else round(raw_sum, 2)

    # 9. Assemble metadata
    metadata = {
        "account_id": account_id,
        "arn": arn,
        "region": region,
        "observation_days": observation_days,
        "total_resources": len(normalized_df),
        "ec2_count": collector_meta.get("ec2_count", 0),
        "ebs_count": collector_meta.get("ebs_count", 0),
        "eip_count": collector_meta.get("eip_count", 0),
        "has_cloudwatch_metrics": include_cloudwatch and not normalized_df.empty and normalized_df["cpu_utilization_avg"].notna().any() if not normalized_df.empty and "cpu_utilization_avg" in normalized_df.columns else False,
        "has_cost_explorer_spend": not cost_summary_df.empty,
        "collector_meta": collector_meta,
        # Multi-region details
        "regions_scanned": regions_scanned,
        "regions_failed": regions_failed,
        "region_errors": region_errors,
        "all_discovered_regions": [r["region"] for r in all_region_records] if all_region_records else [],
    }

    return FinOpsAnalysisResult(
        account_id=account_id,
        arn=arn,
        region=region,
        observation_days=observation_days,
        data=normalized_df,
        idle_findings=idle_findings,
        rightsizing_findings=rightsizing_findings,
        tagging_report=tagging_report,
        tagging_compliance_pct=round(tagging_compliance_pct, 1),
        compliant_count=compliant_count,
        noncompliant_count=noncompliant_count,
        cost_summary=cost_summary_df,
        total_spend_usd=total_spend_usd,
        identified_waste_usd=round(identified_waste_usd, 2),
        rightsizing_savings_usd=round(rightsizing_savings_usd, 2),
        metadata=metadata,
        warnings=warnings,
        regions_scanned=regions_scanned,
        regions_failed=regions_failed,
        region_errors=region_errors,
    )
