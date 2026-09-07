"""Dry-run remediation simulator for FinSight.

SECURITY ASSURANCE:
This module is strictly SIMULATION ONLY. It formats recommendations,
calculates potential savings, and generates AWS CLI preview commands
with the --dry-run flag for audit review.

Under NO circumstances does this module execute commands or mutate AWS resources.
It does not import subprocess or call mutation APIs.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
import pandas as pd


def generate_remediation_plan(
    idle_findings: Optional[pd.DataFrame] = None,
    rightsizing_recommendations: Optional[pd.DataFrame] = None,
) -> List[Dict[str, Any]]:
    """Generate dry-run remediation simulation records without executing any changes.

    Returns:
        List of dictionaries with simulation details, CLI preview strings,
        estimated savings, and safety flags.
    """
    plan: List[Dict[str, Any]] = []

    # 1. Process Idle Findings
    if idle_findings is not None and not idle_findings.empty:
        for _, row in idle_findings.iterrows():
            rid = str(row.get("lineItem/ResourceId") or row.get("resource_id") or "")
            account = str(row.get("lineItem/UsageAccountId") or row.get("account") or "")
            finding_type = str(row.get("finding_type", "Idle Resource"))
            waste = float(row.get("waste_usd", 0.0))
            rule = str(row.get("rule", "Identified by idle detection engine"))
            region = str(row.get("product/region") or row.get("region") or "us-east-1")

            if finding_type == "Idle EC2":
                recommendation = "Stop idle EC2 instance"
                action_type = "STOP_INSTANCE"
                cli_preview = f"aws ec2 stop-instances --instance-ids {rid} --region {region} --dry-run"
            elif finding_type == "Unattached EBS":
                recommendation = "Delete unattached EBS volume"
                action_type = "DELETE_VOLUME"
                cli_preview = f"aws ec2 delete-volume --volume-id {rid} --region {region} --dry-run"
            elif finding_type == "Unused Elastic IP":
                recommendation = "Release unused Elastic IP"
                action_type = "RELEASE_ADDRESS"
                cli_preview = f"aws ec2 release-address --allocation-id {rid} --region {region} --dry-run"
            else:
                recommendation = f"Remediate {finding_type}"
                action_type = "UNKNOWN"
                cli_preview = f"# Review resource {rid} ({finding_type}) in AWS Console"

            plan.append({
                "resource_id": rid,
                "account_id": account,
                "region": region,
                "finding_type": finding_type,
                "recommendation": recommendation,
                "action_type": action_type,
                "reason": rule,
                "estimated_monthly_savings_usd": round(waste, 2),
                "cli_command_preview": cli_preview,
                "approval_status": "Pending Approval (Dry-Run Only)",
                "safety_mode": "SIMULATION_ONLY",
            })

    # 2. Process Rightsizing Recommendations
    if rightsizing_recommendations is not None and not rightsizing_recommendations.empty:
        for _, row in rightsizing_recommendations.iterrows():
            rid = str(row.get("resource_id") or row.get("lineItem/ResourceId") or "")
            account = str(row.get("account") or row.get("lineItem/UsageAccountId") or "")
            current = str(row.get("current_instance_type", ""))
            recommended = str(row.get("recommended_instance_type", ""))
            savings = float(row.get("estimated_monthly_savings_usd", 0.0))
            reason = str(row.get("reason", "Instance underutilized"))
            region = str(row.get("region") or "us-east-1")

            cli_preview = (
                f"# Step 1: Dry-run stop instance:\n"
                f"aws ec2 stop-instances --instance-ids {rid} --region {region} --dry-run\n"
                f"# Step 2: Modify instance attribute (requires instance to be stopped):\n"
                f"aws ec2 modify-instance-attribute --instance-id {rid} --instance-type \"{{\\\"Value\\\": \\\"{recommended}\\\"}}\" --region {region}\n"
                f"# Step 3: Restart instance:\n"
                f"aws ec2 start-instances --instance-ids {rid} --region {region}"
            )

            plan.append({
                "resource_id": rid,
                "account_id": account,
                "region": region,
                "finding_type": "Overprovisioned EC2",
                "recommendation": f"Downsize from {current} to {recommended}",
                "action_type": "RIGHTSIZE_INSTANCE",
                "reason": reason,
                "estimated_monthly_savings_usd": round(savings, 2),
                "cli_command_preview": cli_preview,
                "approval_status": "Pending Approval (Dry-Run Only)",
                "safety_mode": "SIMULATION_ONLY",
            })

    return plan


def remediation_plan_to_dataframe(plan: List[Dict[str, Any]]) -> pd.DataFrame:
    """Convert remediation plan to structured DataFrame."""
    if not plan:
        return pd.DataFrame(columns=[
            "resource_id",
            "account_id",
            "region",
            "finding_type",
            "recommendation",
            "action_type",
            "reason",
            "estimated_monthly_savings_usd",
            "cli_command_preview",
            "approval_status",
            "safety_mode",
        ])
    return pd.DataFrame(plan)
