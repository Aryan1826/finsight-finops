"""Optional live AWS ingestion helpers. Synthetic mode remains the default."""
from __future__ import annotations

from datetime import date, timedelta
import pandas as pd


def fetch_cost_explorer_daily_costs(start: date, end: date, region: str = "us-east-1") -> pd.DataFrame:
    """Fetch daily unblended EC2 cost from AWS Cost Explorer.

    Requires AWS credentials configured for boto3 and Cost Explorer permissions.
    This helper intentionally returns cost-level data; resource-level utilization
    still requires CloudWatch and resource metadata APIs.
    """
    import boto3

    client = boto3.client("ce", region_name=region)
    response = client.get_cost_and_usage(
        TimePeriod={"Start": start.isoformat(), "End": end.isoformat()},
        Granularity="DAILY",
        Metrics=["UnblendedCost"],
        Filter={"Dimensions": {"Key": "SERVICE", "Values": ["Amazon Elastic Compute Cloud - Compute"]}},
    )
    rows = []
    for item in response.get("ResultsByTime", []):
        rows.append({
            "date": item["TimePeriod"]["Start"],
            "service": "EC2",
            "unblended_cost_usd": float(item["Total"]["UnblendedCost"]["Amount"]),
        })
    return pd.DataFrame(rows)


def fetch_ec2_cpu_metrics(instance_ids: list[str], start: date, end: date, region: str = "us-east-1") -> pd.DataFrame:
    """Fetch daily average CPUUtilization from CloudWatch for selected instances."""
    import boto3

    client = boto3.client("cloudwatch", region_name=region)
    rows = []
    for instance_id in instance_ids:
        response = client.get_metric_statistics(
            Namespace="AWS/EC2",
            MetricName="CPUUtilization",
            Dimensions=[{"Name": "InstanceId", "Value": instance_id}],
            StartTime=pd.Timestamp(start, tz="UTC").to_pydatetime(),
            EndTime=pd.Timestamp(end, tz="UTC").to_pydatetime(),
            Period=86400,
            Statistics=["Average", "Maximum"],
        )
        for point in response.get("Datapoints", []):
            rows.append({
                "resource_id": instance_id,
                "timestamp": point["Timestamp"],
                "cpu_avg_pct": float(point.get("Average", 0.0)),
                "cpu_max_pct": float(point.get("Maximum", 0.0)),
            })
    return pd.DataFrame(rows)
