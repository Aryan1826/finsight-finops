#!/usr/bin/env python3
"""Safe Live AWS FinOps Pipeline CLI & Test Script.

Strictly READ-ONLY:
Connects to an AWS account using specified credentials/profile,
runs discovery across EC2, EBS, Elastic IPs, CloudWatch, and Cost Explorer,
evaluates core analysis engines, and outputs a diagnostic summary.

Usage:
    python scripts/test_live_pipeline.py [--profile finsight] [--region us-east-1] [--days 14]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Add project root to path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from utils.aws_client import AWSCredentialError, AWSError
from utils.finops_pipeline import run_live_finops_analysis


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="FinSight — Live AWS Read-Only Inspection & FinOps Analysis"
    )
    parser.add_argument(
        "--profile",
        default="finsight",
        help="AWS named profile to use (default: finsight). Leave empty or pass '' for default credentials.",
    )
    parser.add_argument(
        "--role-arn",
        default=None,
        help="AWS IAM Role ARN for cross-account STS AssumeRole (e.g. arn:aws:iam::123456789012:role/FinSightRole)",
    )
    parser.add_argument(
        "--external-id",
        default=None,
        help="External ID for cross-account STS AssumeRole authentication",
    )
    parser.add_argument(
        "--role-session-name",
        default="FinSightSession",
        help="Session identifier for STS AssumeRole (default: FinSightSession)",
    )
    parser.add_argument(
        "--region",
        default="us-east-1",
        help="AWS region to inspect (default: us-east-1)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=14,
        help="Historical observation window in days for CloudWatch & Cost Explorer (default: 14)",
    )
    parser.add_argument(
        "--policy",
        default="config/tagging_policy.yaml",
        help="Path to tagging governance policy YAML (default: config/tagging_policy.yaml)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    print("=" * 70)
    print(" ☁️  FinSight — Live AWS FinOps Pipeline (Read-Only Inspection)")
    print("=" * 70)
    if args.role_arn:
        print(f" Auth Mode : Cross-Account IAM Role (AssumeRole)")
        print(f" Role ARN  : {args.role_arn}")
        print(f" Ext ID    : {args.external_id}")
    else:
        print(f" Auth Mode : Local Profile / Credential Chain")
        print(f" Profile   : {args.profile or '[Default AWS Credential Chain]'}")
    print(f" Region    : {args.region}")
    print(f" Window    : {args.days} days")
    print(f" Policy    : {args.policy}")
    print("-" * 70)

    try:
        print("[1/5] Authenticating and validating AWS caller identity...")
        result = run_live_finops_analysis(
            profile_name=args.profile if not args.role_arn else None,
            region=args.region,
            role_arn=args.role_arn,
            external_id=args.external_id,
            role_session_name=args.role_session_name,
            observation_days=args.days,
            policy_path=args.policy,
            include_cloudwatch=True,
            include_cost_explorer=True,
        )
    except AWSCredentialError as e:

        print(f"\n❌ AWS Credential Error: {e}")
        print("\nTroubleshooting tips:")
        print("  1. Ensure your profile exists: `aws configure --profile finsight`")
        print("  2. Or export credentials: `export AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=...`")
        sys.exit(1)
    except AWSError as e:
        print(f"\n❌ AWS Integration Error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        sys.exit(1)

    # 1 & 2 & 3. Account, ARN, Region
    print(f"✅ Authenticated successfully!")
    print(f"   Account ID : {result.account_id}")
    print(f"   Caller ARN : {result.arn}")
    print(f"   AWS Region : {result.region}")
    print("-" * 70)

    # 4 & 5. Live Inventory Counts
    ec2_count = result.metadata.get("ec2_count", 0)
    ebs_count = result.metadata.get("ebs_count", 0)
    eip_count = result.metadata.get("eip_count", 0)
    total_resources = result.metadata.get("total_resources", 0)

    print("[2/5] Live Resource Inventory:")
    print(f"   • EC2 Instances : {ec2_count}")
    print(f"   • EBS Volumes   : {ebs_count}")
    print(f"   • Elastic IPs   : {eip_count}")
    print(f"   • Total Tracked : {total_resources}")
    print("-" * 70)

    # 6. CloudWatch Availability
    df = result.data
    ec2_with_cpu = 0
    ec2_without_cpu = 0
    if not df.empty:
        ec2_df = df[df["product/servicecode"] == "EC2"]
        if not ec2_df.empty:
            ec2_with_cpu = int(ec2_df["cpu_utilization_avg"].notna().sum())
            ec2_without_cpu = len(ec2_df) - ec2_with_cpu

    print("[3/5] CloudWatch Metric Telemetry:")
    print(f"   • Instances with CPU data    : {ec2_with_cpu}")
    print(f"   • Instances without CPU data : {ec2_without_cpu}")
    print("-" * 70)

    # 7. Cost Explorer Summary
    ce_records_count = len(result.cost_summary)
    disp_total = 0.0 if abs(result.total_spend_usd) < 0.001 else result.total_spend_usd
    print("[4/5] AWS Cost Explorer Spend Summary:")
    print(f"   • Total Spend (Observation Window) : ${disp_total:,.2f}")
    print(f"   • Daily Cost Records Returned      : {ce_records_count}")
    if not result.cost_summary.empty:
        spend_by_service = result.cost_summary.groupby("service", as_index=False)["unblended_cost_usd"].sum()
        for _, row in spend_by_service.iterrows():
            val = float(row['unblended_cost_usd'])
            disp_val = 0.0 if abs(val) < 0.001 else val
            print(f"     - {row['service']:<40}: ${disp_val:>8.2f}")
    print("-" * 70)


    # 8 & 9. Core Engine Findings
    print("[5/5] FinOps Analysis Engine Results:")
    print(f"\n  🔍 Idle Resource Findings ({len(result.idle_findings)} found):")
    if result.idle_findings.empty:
        print("     No idle resources detected.")
    else:
        for _, row in result.idle_findings.iterrows():
            rid = row.get("lineItem/ResourceId", "")
            ftype = row.get("finding_type", "")
            rule = row.get("rule", "")
            print(f"     [{ftype}] {rid} — Rule: {rule}")

    print(f"\n  📉 Rightsizing Candidates ({len(result.rightsizing_findings)} found):")
    if result.rightsizing_findings.empty:
        print("     No rightsizing opportunities detected.")
    else:
        for _, row in result.rightsizing_findings.iterrows():
            rid = row.get("resource_id", "")
            curr = row.get("current_instance_type", "")
            rec = row.get("recommended_instance_type", "")
            savings = row.get("estimated_monthly_savings_usd", 0.0)
            print(f"     {rid}: {curr} -> {rec} (Est. Monthly Savings: ${savings:,.2f})")

    print(f"\n  🏷️  Tagging Governance Compliance:")
    print(f"     • Overall Compliance : {result.tagging_compliance_pct:.1f}%")
    print(f"     • Compliant Resources: {result.compliant_count}/{result.compliant_count + result.noncompliant_count}")
    print(f"     • Non-Compliant      : {result.noncompliant_count}")
    if result.noncompliant_count > 0:
        noncomp = result.tagging_report[~result.tagging_report["compliant"]]
        for _, row in noncomp.head(5).iterrows():
            rid = row.get("lineItem/ResourceId", "")
            missing = row.get("missing_or_invalid_tags", "")
            print(f"       - {rid}: Missing/Invalid [{missing}]")
        if len(noncomp) > 5:
            print(f"       ... and {len(noncomp) - 5} more non-compliant resources")

    # 10. Warnings
    if result.warnings:
        print("-" * 70)
        print("⚠️  Warnings / Diagnostic Notices:")
        for w in result.warnings:
            print(f"   • {w}")

    print("=" * 70)
    print(" ✅ Live Analysis Pipeline run complete. (Zero changes made to AWS).")
    print("=" * 70)


if __name__ == "__main__":
    main()
