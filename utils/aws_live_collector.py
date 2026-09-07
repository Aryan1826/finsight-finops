"""Live AWS resource and metrics collector for FinSight.

Strictly READ-ONLY:
Uses describe_instances, describe_volumes, describe_addresses,
get_metric_data, and get_cost_and_usage to gather resource inventory,
utilization metrics, and spend without mutating any AWS resource.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
import math
import numpy as np
import pandas as pd
import botocore.exceptions

from utils.aws_client import (
    create_boto3_session,
    get_ce_client,
    get_cloudwatch_client,
    get_ec2_client,
    validate_credentials,
    AWSError,
)


def _extract_tags(tag_list: Optional[List[Dict[str, str]]]) -> Dict[str, str]:
    """Safely convert AWS [{'Key': ..., 'Value': ...}] tag list to canonical tag dict."""
    tags_dict: Dict[str, str] = {}
    if tag_list and isinstance(tag_list, list):
        for tag in tag_list:
            if isinstance(tag, dict) and "Key" in tag:
                k = str(tag.get("Key", "")).strip()
                v = str(tag.get("Value", "")).strip()
                tags_dict[k] = v

    # Extract governance tags with fallback for variations
    owner = tags_dict.get("Owner") or tags_dict.get("owner") or ""
    env = tags_dict.get("Environment") or tags_dict.get("environment") or tags_dict.get("env") or ""
    cc = tags_dict.get("Cost-Center") or tags_dict.get("CostCenter") or tags_dict.get("cost_center") or ""

    return {
        "resourceTags/user:Owner": owner,
        "resourceTags/user:Environment": env,
        "resourceTags/user:Cost-Center": cc,
    }



def collect_live_ec2_instances(
    ec2_client: Any,
    account_id: str,
    region: str,
) -> List[Dict[str, Any]]:
    """Collect EC2 instances using ec2:DescribeInstances (read-only with pagination)."""
    records: List[Dict[str, Any]] = []
    try:
        paginator = ec2_client.get_paginator("describe_instances")
        for page in paginator.paginate():
            for reservation in page.get("Reservations", []):
                for inst in reservation.get("Instances", []):
                    instance_id = str(inst.get("InstanceId", ""))
                    if not instance_id:
                        continue

                    raw_state = inst.get("State", {}).get("Name", "unknown").lower()
                    instance_type = str(inst.get("InstanceType", ""))
                    launch_time = inst.get("LaunchTime")
                    tags = _extract_tags(inst.get("Tags"))

                    record = {
                        "lineItem/ResourceId": instance_id,
                        "lineItem/UsageAccountId": account_id,
                        "product/servicecode": "EC2",
                        "product/region": region,
                        "product/instanceType": instance_type,
                        "resource_state": raw_state,
                        "lineItem/UnblendedCost": np.nan,
                        "lineItem/UnblendedRate": np.nan,
                        "cpu_utilization_avg": np.nan,
                        "cpu_utilization_p95": np.nan,
                        "network_usage_gb": np.nan,
                        "size_gb": 0,
                        "launch_time": launch_time.isoformat() if hasattr(launch_time, "isoformat") else str(launch_time or ""),
                        **tags,
                    }
                    records.append(record)
    except botocore.exceptions.ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "")
        error_msg = e.response.get("Error", {}).get("Message", str(e))
        raise AWSError(f"Failed to describe EC2 instances in {region} ({error_code}): {error_msg}") from e

    return records


def collect_live_ebs_volumes(
    ec2_client: Any,
    account_id: str,
    region: str,
) -> List[Dict[str, Any]]:
    """Collect EBS volumes using ec2:DescribeVolumes (read-only with pagination)."""
    records: List[Dict[str, Any]] = []
    try:
        paginator = ec2_client.get_paginator("describe_volumes")
        for page in paginator.paginate():
            for vol in page.get("Volumes", []):
                volume_id = str(vol.get("VolumeId", ""))
                if not volume_id:
                    continue

                raw_state = str(vol.get("State", "unknown")).lower()
                size_gb = int(vol.get("Size", 0))
                volume_type = str(vol.get("VolumeType", "gp3"))
                tags = _extract_tags(vol.get("Tags"))

                record = {
                    "lineItem/ResourceId": volume_id,
                    "lineItem/UsageAccountId": account_id,
                    "product/servicecode": "EBS",
                    "product/region": region,
                    "product/instanceType": "",
                    "resource_state": raw_state,
                    "lineItem/UnblendedCost": np.nan,
                    "lineItem/UnblendedRate": np.nan,
                    "cpu_utilization_avg": np.nan,
                    "cpu_utilization_p95": np.nan,
                    "network_usage_gb": np.nan,
                    "size_gb": size_gb,
                    "volume_type": volume_type,
                    **tags,
                }
                records.append(record)
    except botocore.exceptions.ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "")
        error_msg = e.response.get("Error", {}).get("Message", str(e))
        raise AWSError(f"Failed to describe EBS volumes in {region} ({error_code}): {error_msg}") from e

    return records


def collect_live_elastic_ips(
    ec2_client: Any,
    account_id: str,
    region: str,
) -> List[Dict[str, Any]]:
    """Collect Elastic IPs using ec2:DescribeAddresses (read-only)."""
    records: List[Dict[str, Any]] = []
    try:
        response = ec2_client.describe_addresses()
        for addr in response.get("Addresses", []):
            allocation_id = str(addr.get("AllocationId") or addr.get("PublicIp") or "")
            if not allocation_id:
                continue

            public_ip = str(addr.get("PublicIp", ""))
            association_id = addr.get("AssociationId")
            # If association_id is present, state is 'associated'; otherwise 'unassociated'
            state = "associated" if association_id else "unassociated"
            tags = _extract_tags(addr.get("Tags"))

            record = {
                "lineItem/ResourceId": allocation_id,
                "lineItem/UsageAccountId": account_id,
                "product/servicecode": "ElasticIP",
                "product/region": region,
                "product/instanceType": "",
                "resource_state": state,
                "lineItem/UnblendedCost": np.nan,
                "lineItem/UnblendedRate": np.nan,
                "cpu_utilization_avg": np.nan,
                "cpu_utilization_p95": np.nan,
                "network_usage_gb": np.nan,
                "size_gb": 0,
                "public_ip": public_ip,
                **tags,
            }
            records.append(record)
    except botocore.exceptions.ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "")
        error_msg = e.response.get("Error", {}).get("Message", str(e))
        raise AWSError(f"Failed to describe Elastic IPs in {region} ({error_code}): {error_msg}") from e

    return records


def collect_cloudwatch_ec2_metrics(
    cloudwatch_client: Any,
    instance_ids: List[str],
    start_time: datetime,
    end_time: datetime,
    period_seconds: int = 3600,
) -> Tuple[Dict[str, Dict[str, float]], List[str]]:
    """Batch-fetch historical EC2 CPU utilization using CloudWatch get_metric_data.

    Returns:
        Tuple of (metrics_by_instance_id, warnings_list)
        metrics_by_instance_id maps instance_id -> {
            'cpu_utilization_avg': float,
            'cpu_utilization_p95': float,
            'datapoints_count': int,
            'observed_days': int,
        }
    """
    metrics_map: Dict[str, Dict[str, float]] = {}
    warnings: List[str] = []

    if not instance_ids:
        return metrics_map, warnings

    # CloudWatch get_metric_data supports up to 500 MetricDataQueries per request
    batch_size = 400
    for chunk_start in range(0, len(instance_ids), batch_size):
        chunk = instance_ids[chunk_start : chunk_start + batch_size]
        queries = []
        id_to_instance = {}

        for idx, instance_id in enumerate(chunk):
            query_id = f"m_{idx}_{instance_id.replace('-', '_')}"
            id_to_instance[query_id] = instance_id
            queries.append({
                "Id": query_id,
                "MetricStat": {
                    "Metric": {
                        "Namespace": "AWS/EC2",
                        "MetricName": "CPUUtilization",
                        "Dimensions": [{"Name": "InstanceId", "Value": instance_id}],
                    },
                    "Period": period_seconds,
                    "Stat": "Average",
                },
                "ReturnData": True,
            })

        try:
            paginator = cloudwatch_client.get_paginator("get_metric_data") if hasattr(cloudwatch_client, "get_paginator") and "get_metric_data" in cloudwatch_client._service_model.operation_names else None
            
            # Simple pagination loop for get_metric_data
            next_token = None
            raw_results: Dict[str, List[float]] = {inst: [] for inst in chunk}
            timestamps_by_inst: Dict[str, set] = {inst: set() for inst in chunk}

            while True:
                params: Dict[str, Any] = {
                    "MetricDataQueries": queries,
                    "StartTime": start_time,
                    "EndTime": end_time,
                    "ScanBy": "TimestampAscending",
                }
                if next_token:
                    params["NextToken"] = next_token

                response = cloudwatch_client.get_metric_data(**params)
                for res in response.get("MetricDataResults", []):
                    q_id = res.get("Id", "")
                    inst_id = id_to_instance.get(q_id)
                    if inst_id:
                        values = res.get("Values", [])
                        timestamps = res.get("Timestamps", [])
                        raw_results[inst_id].extend([float(v) for v in values])
                        for ts in timestamps:
                            if hasattr(ts, "date"):
                                timestamps_by_inst[inst_id].add(ts.date())

                next_token = response.get("NextToken")
                if not next_token:
                    break

            for inst_id, vals in raw_results.items():
                if vals:
                    arr = np.array(vals, dtype=float)
                    p95 = float(np.percentile(arr, 95))
                    avg = float(np.mean(arr))
                    observed_days = len(timestamps_by_inst[inst_id]) or max(1, int((end_time - start_time).days))
                    metrics_map[inst_id] = {
                        "cpu_utilization_avg": round(avg, 2),
                        "cpu_utilization_p95": round(p95, 2),
                        "datapoints_count": len(vals),
                        "observed_days": observed_days,
                    }
                else:
                    metrics_map[inst_id] = {
                        "cpu_utilization_avg": np.nan,
                        "cpu_utilization_p95": np.nan,
                        "datapoints_count": 0,
                        "observed_days": 0,
                    }

        except botocore.exceptions.ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            error_msg = e.response.get("Error", {}).get("Message", str(e))
            warnings.append(f"CloudWatch CPU query failed ({error_code}): {error_msg}. CPU metrics set to NaN.")
            for inst_id in chunk:
                metrics_map[inst_id] = {
                    "cpu_utilization_avg": np.nan,
                    "cpu_utilization_p95": np.nan,
                    "datapoints_count": 0,
                    "observed_days": 0,
                }
        except Exception as e:
            warnings.append(f"Unexpected CloudWatch metric retrieval error: {e}. CPU metrics set to NaN.")
            for inst_id in chunk:
                metrics_map[inst_id] = {
                    "cpu_utilization_avg": np.nan,
                    "cpu_utilization_p95": np.nan,
                    "datapoints_count": 0,
                    "observed_days": 0,
                }

    return metrics_map, warnings


def collect_cost_explorer_spend(
    ce_client: Any,
    start_date: date,
    end_date: date,
    granularity: str = "DAILY",
) -> Tuple[pd.DataFrame, List[str]]:
    """Fetch aggregated unblended costs from AWS Cost Explorer.

    Preserves account/service-level spend separately from per-resource records.
    Returns:
        Tuple of (cost_dataframe, warnings_list)
    """
    warnings: List[str] = []
    rows: List[Dict[str, Any]] = []

    try:
        response = ce_client.get_cost_and_usage(
            TimePeriod={"Start": start_date.isoformat(), "End": end_date.isoformat()},
            Granularity=granularity,
            Metrics=["UnblendedCost"],
            GroupBy=[
                {"Type": "DIMENSION", "Key": "SERVICE"},
                {"Type": "DIMENSION", "Key": "USAGE_TYPE"},
            ],
        )

        for item in response.get("ResultsByTime", []):
            time_start = item.get("TimePeriod", {}).get("Start", "")
            time_end = item.get("TimePeriod", {}).get("End", "")
            for group in item.get("Groups", []):
                keys = group.get("Keys", ["", ""])
                service_name = keys[0] if len(keys) > 0 else "Unknown"
                usage_type = keys[1] if len(keys) > 1 else ""
                metrics = group.get("Metrics", {})
                amount = float(metrics.get("UnblendedCost", {}).get("Amount", 0.0))
                rows.append({
                    "date": time_start,
                    "date_end": time_end,
                    "service": service_name,
                    "usage_type": usage_type,
                    "unblended_cost_usd": amount,
                })
    except botocore.exceptions.ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "")
        error_msg = e.response.get("Error", {}).get("Message", str(e))
        warnings.append(f"AWS Cost Explorer query failed ({error_code}): {error_msg}. Spend records unavailable.")
    except Exception as e:
        warnings.append(f"Unexpected Cost Explorer error: {e}")

    df = pd.DataFrame(rows) if rows else pd.DataFrame(columns=["date", "date_end", "service", "usage_type", "unblended_cost_usd"])
    return df, warnings


def collect_live_aws_inventory(
    session: Optional[Any] = None,
    region: str = "us-east-1",
    observation_days: int = 14,
    include_cloudwatch: bool = True,
    include_cost_explorer: bool = False,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """Master collector: Discover EC2, EBS, EIP, and metrics across live AWS account.

    Strictly READ-ONLY. Returns:
        Tuple of (resources_dataframe, metadata_dict)
    """
    sess = session or create_boto3_session(region_name=region)
    caller_info = validate_credentials(session=sess, region_name=region)
    account_id = caller_info.get("account_id", "")

    ec2 = get_ec2_client(session=sess, region_name=region)
    all_warnings: List[str] = []

    # 1. Collect resources with per-service graceful error handling
    ec2_records: List[Dict[str, Any]] = []

    try:
        ec2_records = collect_live_ec2_instances(ec2, account_id, region)
    except AWSError as e:
        all_warnings.append(f"EC2 inventory collection skipped: {e}")
    except Exception as e:
        all_warnings.append(f"Unexpected EC2 collection error: {e}")

    ebs_records: List[Dict[str, Any]] = []
    try:
        ebs_records = collect_live_ebs_volumes(ec2, account_id, region)
    except AWSError as e:
        all_warnings.append(f"EBS volume collection skipped: {e}")
    except Exception as e:
        all_warnings.append(f"Unexpected EBS collection error: {e}")

    eip_records: List[Dict[str, Any]] = []
    try:
        eip_records = collect_live_elastic_ips(ec2, account_id, region)
    except AWSError as e:
        all_warnings.append(f"Elastic IP collection skipped: {e}")
    except Exception as e:
        all_warnings.append(f"Unexpected Elastic IP collection error: {e}")


    # 2. Collect CloudWatch CPU metrics for EC2 instances
    now_utc = datetime.now(timezone.utc)
    metric_start = now_utc - timedelta(days=observation_days)
    time_interval_str = f"{metric_start.date()}T00:00:00Z/{now_utc.date()}T00:00:00Z"

    if include_cloudwatch and ec2_records:
        cw = get_cloudwatch_client(session=sess, region_name=region)
        instance_ids = [r["lineItem/ResourceId"] for r in ec2_records]
        metrics_map, cw_warnings = collect_cloudwatch_ec2_metrics(
            cw, instance_ids, start_time=metric_start, end_time=now_utc
        )
        all_warnings.extend(cw_warnings)

        for r in ec2_records:
            m = metrics_map.get(r["lineItem/ResourceId"], {})
            r["cpu_utilization_avg"] = m.get("cpu_utilization_avg", np.nan)
            r["cpu_utilization_p95"] = m.get("cpu_utilization_p95", np.nan)
            r["observed_days"] = m.get("observed_days", observation_days if not math.isnan(m.get("cpu_utilization_avg", np.nan)) else 0)

    # 3. Collect Cost Explorer data if requested
    ce_df = pd.DataFrame()
    if include_cost_explorer:
        ce = get_ce_client(session=sess, region_name=region)
        ce_start = start_date or (now_utc.date() - timedelta(days=observation_days))
        ce_end = end_date or now_utc.date()
        ce_df, ce_warnings = collect_cost_explorer_spend(ce, start_date=ce_start, end_date=ce_end)
        all_warnings.extend(ce_warnings)

    # Combine all resource records
    all_records = ec2_records + ebs_records + eip_records
    df = pd.DataFrame(all_records) if all_records else pd.DataFrame()

    if not df.empty:
        df["identity/TimeInterval"] = time_interval_str

    meta = {
        "account_id": account_id,
        "arn": caller_info.get("arn", ""),
        "region": region,
        "observation_days": observation_days,
        "ec2_count": len(ec2_records),
        "ebs_count": len(ebs_records),
        "eip_count": len(eip_records),
        "total_resources": len(all_records),
        "warnings": all_warnings,
        "cost_explorer_summary": ce_df,
    }

    return df, meta
