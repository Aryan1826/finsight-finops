"""Generate realistic AWS CUR-like data with injected, known-answer anomalies."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd

INSTANCE_TYPES = [
    ("t3.nano", 0.0052, 2),
    ("t3.micro", 0.0104, 2),
    ("t3.small", 0.0208, 2),
    ("t3.medium", 0.0416, 2),
    ("t3.large", 0.0832, 2),
    ("t3.xlarge", 0.1664, 4),
    ("t3.2xlarge", 0.3328, 8),
    ("m5.large", 0.096, 8),
    ("m5.xlarge", 0.192, 16),
    ("m5.2xlarge", 0.384, 32),
    ("m5.4xlarge", 0.768, 64),
]

DOWNSIZE_MAP = {
    "t3.2xlarge": "t3.xlarge",
    "t3.xlarge": "t3.large",
    "t3.large": "t3.medium",
    "t3.medium": "t3.small",
    "t3.small": "t3.micro",
    "t3.micro": "t3.nano",
    "m5.4xlarge": "m5.2xlarge",
    "m5.2xlarge": "m5.xlarge",
    "m5.xlarge": "m5.large",
}

SERVICE_META = {
    "EC2": {"product_code": "AmazonEC2", "usage_type": "BoxUsage"},
    "EBS": {"product_code": "AmazonEC2", "usage_type": "EBS:VolumeUsage.gp3"},
    "ElasticIP": {"product_code": "AmazonEC2", "usage_type": "ElasticIP:IdleAddress"},
}


def _tags(i: int, resource_type: str, violations: bool) -> Dict[str, str]:
    owner = ["platform", "data", "engineering", "security"][i % 4]
    env = ["prod", "staging", "dev"][i % 3]
    cc = f"CC-{100+i%6:03d}"
    if violations:
        mode = i % 3
        if mode == 0:
            owner = ""
        elif mode == 1:
            env = "qa-unknown"
        else:
            cc = ""
    return {"Owner": owner, "Environment": env, "Cost-Center": cc}


def _make_resources(rng: np.random.Generator, accounts: List[str]) -> pd.DataFrame:
    rows: List[dict] = []
    resource_no = 0

    for account in accounts:
        # EC2 resources
        for i in range(28):
            resource_no += 1
            anomaly = i < 5
            rightsizing_candidate = (not anomaly) and i in {7, 8, 9, 10, 11, 12, 13}
            instance_type, rate, vcpu = INSTANCE_TYPES[rng.integers(3, len(INSTANCE_TYPES))]
            if anomaly:
                instance_type, rate, vcpu = INSTANCE_TYPES[rng.integers(3, 7)]
            cpu_base = float(rng.uniform(1.0, 2.8)) if anomaly else (
                float(rng.uniform(8.0, 18.0)) if rightsizing_candidate else float(rng.uniform(28.0, 72.0))
            )
            rows.append({
                "account": account,
                "resource_type": "EC2",
                "resource_id": f"i-{resource_no:010d}",
                "region": ["ap-south-1", "us-east-1", "us-west-2"][i % 3],
                "instance_type": instance_type,
                "hourly_rate": rate,
                "vcpu": vcpu,
                "resource_state": "running",
                "cpu_profile": cpu_base,
                "network_profile": float(rng.uniform(0.01, 1.5)) if anomaly else float(rng.uniform(0.4, 8.0)),
                "tags": _tags(i + resource_no, "EC2", violations=(i in {2, 5, 15})),
                "ground_truth_idle_ec2": bool(anomaly),
                "ground_truth_rightsizing": bool(rightsizing_candidate),
                "ground_truth_idle_ebs": False,
                "ground_truth_unused_eip": False,
            })

        # EBS volumes, with available = unattached anomalies
        for i in range(12):
            resource_no += 1
            anomaly = i < 3
            size_gb = int(rng.integers(20, 501))
            rows.append({
                "account": account,
                "resource_type": "EBS",
                "resource_id": f"vol-{resource_no:010d}",
                "region": ["ap-south-1", "us-east-1", "us-west-2"][i % 3],
                "instance_type": "",
                "hourly_rate": 0.08 / 730.0,
                "vcpu": 0,
                "resource_state": "available" if anomaly else "in-use",
                "cpu_profile": np.nan,
                "network_profile": np.nan,
                "tags": _tags(i + resource_no, "EBS", violations=(i == 6)),
                "size_gb": size_gb,
                "ground_truth_idle_ebs": bool(anomaly),
                "ground_truth_idle_ec2": False,
                "ground_truth_unused_eip": False,
                "ground_truth_rightsizing": False,
            })

        # Elastic IPs, with association flag
        for i in range(8):
            resource_no += 1
            anomaly = i < 2
            rows.append({
                "account": account,
                "resource_type": "ElasticIP",
                "resource_id": f"eip-{resource_no:010d}",
                "region": ["ap-south-1", "us-east-1", "us-west-2"][i % 3],
                "instance_type": "",
                "hourly_rate": 0.005 if anomaly else 0.0,
                "vcpu": 0,
                "resource_state": "unassociated" if anomaly else "associated",
                "cpu_profile": np.nan,
                "network_profile": np.nan,
                "tags": _tags(i + resource_no, "ElasticIP", violations=(i == 5)),
                "ground_truth_unused_eip": bool(anomaly),
                "ground_truth_idle_ec2": False,
                "ground_truth_idle_ebs": False,
                "ground_truth_rightsizing": False,
            })

    return pd.DataFrame(rows)


def _daily_rows(resources: pd.DataFrame, days: int, start_date: datetime, rng: np.random.Generator) -> pd.DataFrame:
    output: List[dict] = []
    for _, r in resources.iterrows():
        for d in range(days):
            start = start_date + timedelta(days=d)
            end = start + timedelta(days=1)
            hours = 24.0
            if r.resource_type == "EC2":
                cpu = max(0.1, min(99.0, rng.normal(r.cpu_profile, 0.35) if r.resource_type == "EC2" and r.get("ground_truth_idle_ec2", False) else rng.normal(r.cpu_profile, 1.2)))
                net = max(0.0, rng.normal(r.network_profile, max(0.05, r.network_profile * 0.08)))
                cost = r.hourly_rate * hours
                usage_amount = hours
                usage_type = f"{SERVICE_META['EC2']['usage_type']}:{r.instance_type}"
            elif r.resource_type == "EBS":
                cpu = np.nan
                net = np.nan
                cost = float(r.size_gb) * 0.08 / 30.0
                usage_amount = float(r.size_gb)
                usage_type = SERVICE_META["EBS"]["usage_type"]
            else:
                cpu = np.nan
                net = np.nan
                cost = r.hourly_rate * hours
                usage_amount = hours
                usage_type = SERVICE_META["ElasticIP"]["usage_type"]

            output.append({
                "bill/PayerAccountId": "111122223333",
                "lineItem/UsageAccountId": r.account,
                "identity/TimeInterval": f"{start.date()}T00:00:00Z/{end.date()}T00:00:00Z",
                "lineItem/UsageStartDate": start.replace(tzinfo=timezone.utc).isoformat(),
                "lineItem/UsageEndDate": end.replace(tzinfo=timezone.utc).isoformat(),
                "lineItem/LineItemType": "Usage",
                "lineItem/ProductCode": SERVICE_META[r.resource_type]["product_code"],
                "product/servicecode": r.resource_type,
                "product/region": r.region,
                "product/instanceType": r.instance_type,
                "product/usagetype": usage_type,
                "lineItem/ResourceId": r.resource_id,
                "lineItem/UsageAmount": round(usage_amount, 4),
                "lineItem/UnblendedRate": round(float(r.hourly_rate), 6),
                "lineItem/UnblendedCost": round(float(cost), 4),
                "resourceTags/user:Owner": r.tags.get("Owner", ""),
                "resourceTags/user:Environment": r.tags.get("Environment", ""),
                "resourceTags/user:Cost-Center": r.tags.get("Cost-Center", ""),
                "resource_state": r.resource_state,
                "cpu_utilization_avg": round(float(cpu), 2) if pd.notna(cpu) else np.nan,
                "cpu_utilization_p95": round(float(cpu), 2) if pd.notna(cpu) else np.nan,
                "network_usage_gb": round(float(net), 4) if pd.notna(net) else np.nan,
                "size_gb": int(r["size_gb"]) if pd.notna(r.get("size_gb")) else 0,
                "ground_truth_idle_ec2": bool(r.get("ground_truth_idle_ec2", False)),
                "ground_truth_idle_ebs": bool(r.get("ground_truth_idle_ebs", False)),
                "ground_truth_unused_eip": bool(r.get("ground_truth_unused_eip", False)),
                "ground_truth_rightsizing": bool(r.get("ground_truth_rightsizing", False)),
            })
    return pd.DataFrame(output)


def save_synthetic_dataset(
    df: pd.DataFrame,
    csv_path: str | Path | None = None,
    parquet_path: str | Path | None = None,
) -> None:
    """Save synthetic dataset to CSV and/or Parquet formats."""
    if csv_path:
        p_csv = Path(csv_path)
        p_csv.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(p_csv, index=False)
    if parquet_path:
        p_parquet = Path(parquet_path)
        p_parquet.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(p_parquet, index=False)


def generate_synthetic_cur(
    output_path: str | Path = "data/synthetic_cur.csv",
    days: int = 30,
    seed: int = 42,
    accounts: List[str] | None = None,
    parquet_path: str | Path | None = None,
) -> pd.DataFrame:
    """Generate deterministic synthetic CUR-style data and save it to CSV / Parquet."""
    accounts = accounts or ["100000000001", "100000000002"]
    rng = np.random.default_rng(seed)
    resources = _make_resources(rng, accounts)
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    df = _daily_rows(resources, days=days, start_date=start, rng=rng)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".parquet":
        df.to_parquet(path, index=False)
    else:
        df.to_csv(path, index=False)

    if parquet_path:
        p_pq = Path(parquet_path)
        p_pq.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(p_pq, index=False)

    return df


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic AWS CUR-like FinOps benchmark data")
    parser.add_argument("--output", default="data/synthetic_cur.csv")
    parser.add_argument("--parquet-output", default=None, help="Optional Parquet output path")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    df = generate_synthetic_cur(args.output, args.days, args.seed, parquet_path=args.parquet_output)
    print(f"Generated {len(df):,} CUR rows -> {args.output}")
    if args.parquet_output:
        print(f"Also saved Parquet dataset -> {args.parquet_output}")
    print("Ground truth idle EC2:", int(df.groupby("lineItem/ResourceId")["ground_truth_idle_ec2"].max().astype(bool).sum()))
    print("Ground truth idle EBS:", int(df.groupby("lineItem/ResourceId")["ground_truth_idle_ebs"].max().astype(bool).sum()))
    print("Ground truth unused EIP:", int(df.groupby("lineItem/ResourceId")["ground_truth_unused_eip"].max().astype(bool).sum()))
    print("Ground truth rightsizing:", int(df.groupby("lineItem/ResourceId")["ground_truth_rightsizing"].max().astype(bool).sum()))


if __name__ == "__main__":
    main()

