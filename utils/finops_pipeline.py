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
from utils.aws_live_collector import collect_live_aws_inventory
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
) -> FinOpsAnalysisResult:
    """Execute the end-to-end Live AWS FinOps analysis pipeline.

    Supports both Local AWS Profile/Default Credentials and Cross-Account IAM Role (AssumeRole).

    Workflow:
    1. Authenticate & validate AWS identity via STS.
    2. Collect read-only inventory (EC2, EBS, EIP), CloudWatch CPU metrics, and Cost Explorer spend.
    3. Normalize records into canonical FinSight schema.
    4. Run reused analysis engines (idle_detector, rightsizing, tagging_policy).
    5. Aggregate findings without fabricating missing per-resource costs.

    Returns:
        FinOpsAnalysisResult dataclass containing normalized data, engine findings,
        Cost Explorer summary, metrics, and warnings.
    """
    warnings: List[str] = []

    # 1. Establish AWS session (profile or STS AssumeRole) and validate caller identity
    session = create_boto3_session(
        profile_name=profile_name,
        region_name=region,
        role_arn=role_arn,
        external_id=external_id,
        role_session_name=role_session_name,
    )
    caller = validate_credentials(session=session, region_name=region, expected_role_arn=role_arn)
    account_id = caller.get("account_id", "")
    arn = caller.get("arn", "")


    # 2. Collect Live AWS data
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

    # 3. Normalize into canonical FinSight schema
    if raw_df.empty:
        normalized_df = pd.DataFrame()
        warnings.append("No EC2, EBS, or Elastic IP resources were discovered in the target region.")
    else:
        normalized_df = normalize_live_dataframe(raw_df)
        validation = validate_canonical_schema(normalized_df)
        if not validation["is_valid"]:
            warnings.append(f"Schema validation warning: Missing columns {validation['missing_required']}")

    # 4. Execute reused Idle Detector Engine
    idle_findings = pd.DataFrame()
    identified_waste_usd = 0.0
    if not normalized_df.empty:
        # In live inventory, CloudWatch CPU metrics are pre-aggregated over the observation_days window.
        # If the dataframe has a single interval snapshot row, min_days is set to 1; for multi-day datasets, min(7, observation_days) is used.
        num_intervals = normalized_df["identity/TimeInterval"].nunique() if "identity/TimeInterval" in normalized_df.columns else 1
        effective_min_days = 1 if num_intervals <= 1 else min(7, observation_days)

        idle_findings = detect_idle_resources(
            normalized_df,
            ec2_cpu_p95_threshold=5.0,
            min_days_observed=effective_min_days,
        )
        if not idle_findings.empty:
            # Sum waste where available without converting NaN to 0 when cost is unavailable
            numeric_waste = pd.to_numeric(idle_findings["waste_usd"], errors="coerce")
            identified_waste_usd = float(numeric_waste.dropna().sum()) if numeric_waste.notna().any() else 0.0


    # 5. Execute reused Rightsizing Engine
    rightsizing_findings = pd.DataFrame()
    rightsizing_savings_usd = 0.0
    if not normalized_df.empty:
        rightsizing_findings = recommend_rightsizing(normalized_df, p95_threshold=20.0)
        if not rightsizing_findings.empty:
            savings_col = pd.to_numeric(rightsizing_findings["estimated_monthly_savings_usd"], errors="coerce")
            rightsizing_savings_usd = float(savings_col.dropna().sum())

    # 6. Execute reused Tagging Governance Policy Engine
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

    # 7. Extract Cost Explorer spend
    cost_summary_df = collector_meta.get("cost_explorer_summary", pd.DataFrame())
    total_spend_usd = 0.0
    if not cost_summary_df.empty and "unblended_cost_usd" in cost_summary_df.columns:
        raw_sum = float(cost_summary_df["unblended_cost_usd"].sum())
        total_spend_usd = 0.0 if abs(raw_sum) < 0.001 else round(raw_sum, 2)


    # 8. Assemble metadata
    metadata = {
        "account_id": account_id,
        "arn": arn,
        "region": region,
        "observation_days": observation_days,
        "total_resources": len(normalized_df),
        "ec2_count": collector_meta.get("ec2_count", 0),
        "ebs_count": collector_meta.get("ebs_count", 0),
        "eip_count": collector_meta.get("eip_count", 0),
        "has_cloudwatch_metrics": include_cloudwatch and not normalized_df.empty and normalized_df["cpu_utilization_avg"].notna().any(),
        "has_cost_explorer_spend": not cost_summary_df.empty,
        "collector_meta": collector_meta,
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
    )
