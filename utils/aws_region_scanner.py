"""Dynamic all-region AWS resource scanner for FinSight.

Strictly READ-ONLY:
  - Discovers enabled regions via EC2 ``DescribeRegions`` with ``AllRegions=True``.
  - Scans each eligible region for EC2 instances, EBS volumes, Elastic IPs, and
    CloudWatch utilisation metrics.
  - Cost Explorer is a global billing service and is intentionally called only once
    (from ``finops_pipeline.py``), not per-region.
  - Zero write, update, delete, or remediation actions are performed.
  - Duplicate resources cannot appear because each region scan uses a separate
    regional client and resource IDs are region-scoped by design.

Usage::

    from utils.aws_region_scanner import discover_enabled_regions, scan_all_regions

    regions = discover_enabled_regions(session)
    df, meta = scan_all_regions(session, regions)
"""
from __future__ import annotations

import logging
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import botocore.exceptions
import numpy as np
import pandas as pd

from utils.aws_client import AWSError, get_cloudwatch_client, get_ec2_client
from utils.aws_live_collector import (
    collect_cloudwatch_ec2_metrics,
    collect_live_ebs_volumes,
    collect_live_ec2_instances,
    collect_live_elastic_ips,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Region status constants (mirrors EC2 DescribeRegions OptInStatus values)
# ---------------------------------------------------------------------------
REGION_STATUS_OPT_IN_NOT_REQUIRED = "opt-in-not-required"   # always available
REGION_STATUS_OPTED_IN = "opted-in"                          # explicitly enabled
REGION_STATUS_NOT_OPTED_IN = "not-opted-in"                  # disabled / skipped

# Regions we consider safe to scan (the account has access to them)
_ELIGIBLE_OPT_IN_STATUSES = {REGION_STATUS_OPT_IN_NOT_REQUIRED, REGION_STATUS_OPTED_IN}

# Conservative concurrency: enough to be fast, low enough to avoid throttling.
_DEFAULT_MAX_WORKERS = 5

# Simple exponential back-off for throttled retries
_RETRY_DELAYS = [1.0, 2.0, 4.0]


# ---------------------------------------------------------------------------
# Region discovery
# ---------------------------------------------------------------------------

def discover_enabled_regions(
    session: Any,
    base_region: str = "us-east-1",
    include_all: bool = True,
) -> List[Dict[str, str]]:
    """Discover all AWS regions available to the connected account.

    Uses ``ec2:DescribeRegions`` with ``AllRegions=True`` so that opt-in regions
    appear in the list (with their opt-in status), then filters to only those the
    account has actually enabled.

    Args:
        session:    An authenticated ``boto3.Session``.
        base_region: Region used to make the DescribeRegions call (any enabled
                     region works; ``us-east-1`` is universally available).
        include_all: When True, pass ``AllRegions=True`` to discover opt-in regions
                     alongside standard ones.

    Returns:
        List of dicts, each with keys:
            ``region``, ``opt_in_status``, ``endpoint``
    """
    ec2 = session.client("ec2", region_name=base_region)
    params: Dict[str, Any] = {}
    if include_all:
        params["AllRegions"] = True

    regions: List[Dict[str, str]] = []
    for attempt, delay in enumerate([0.0] + _RETRY_DELAYS):
        if delay:
            time.sleep(delay)
        try:
            resp = ec2.describe_regions(**params)
            for r in resp.get("Regions", []):
                status = r.get("OptInStatus", REGION_STATUS_OPT_IN_NOT_REQUIRED)
                regions.append({
                    "region": r["RegionName"],
                    "opt_in_status": status,
                    "endpoint": r.get("Endpoint", ""),
                })
            break
        except botocore.exceptions.ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            msg = e.response.get("Error", {}).get("Message", str(e))
            if code == "RequestExpired" or "Throttling" in code:
                logger.warning("DescribeRegions throttled (attempt %d): %s", attempt + 1, msg)
                if attempt >= len(_RETRY_DELAYS):
                    raise AWSError(f"DescribeRegions failed after retries: {msg}") from e
                continue
            raise AWSError(f"DescribeRegions failed ({code}): {msg}") from e

    return regions


def filter_eligible_regions(
    all_regions: List[Dict[str, str]],
    selected_regions: Optional[List[str]] = None,
) -> Tuple[List[str], List[Dict[str, str]]]:
    """Split region list into eligible and skipped.

    Args:
        all_regions:       Output of :func:`discover_enabled_regions`.
        selected_regions:  When provided, only scan these regions (must be a subset
                           of ``all_regions``).  ``None`` means scan all eligible.

    Returns:
        (eligible_region_names, skipped_region_records)
    """
    eligible: List[str] = []
    skipped: List[Dict[str, str]] = []

    for r in all_regions:
        name = r["region"]
        status = r.get("opt_in_status", REGION_STATUS_OPT_IN_NOT_REQUIRED)

        if status not in _ELIGIBLE_OPT_IN_STATUSES:
            skipped.append({**r, "skip_reason": f"opt-in status = {status}"})
            continue

        if selected_regions is not None and name not in selected_regions:
            skipped.append({**r, "skip_reason": "not in selected region list"})
            continue

        eligible.append(name)

    return eligible, skipped


# ---------------------------------------------------------------------------
# Per-region scanning
# ---------------------------------------------------------------------------

def _scan_single_region(
    session: Any,
    region: str,
    account_id: str,
    observation_days: int,
    include_cloudwatch: bool,
    metric_start: datetime,
    metric_end: datetime,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Collect EC2, EBS, EIP (and optionally CloudWatch) for a single region.

    Returns:
        (resource_records, warning_messages)
    """
    records: List[Dict[str, Any]] = []
    warnings: List[str] = []

    ec2_client = None
    try:
        ec2_client = get_ec2_client(session=session, region_name=region)
    except Exception as e:
        warnings.append(f"[{region}] Failed to create EC2 client: {e}")
        return records, warnings

    # EC2 instances
    ec2_records: List[Dict[str, Any]] = []
    try:
        ec2_records = collect_live_ec2_instances(ec2_client, account_id, region)
        records.extend(ec2_records)
    except AWSError as e:
        warnings.append(f"[{region}] EC2 inventory skipped: {e}")
    except botocore.exceptions.ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        msg = e.response.get("Error", {}).get("Message", str(e))
        warnings.append(f"[{region}] EC2 inventory ClientError ({code}): {msg}")
    except Exception as e:
        warnings.append(f"[{region}] EC2 inventory unexpected error: {e}")

    # EBS volumes
    try:
        ebs_records = collect_live_ebs_volumes(ec2_client, account_id, region)
        records.extend(ebs_records)
    except AWSError as e:
        warnings.append(f"[{region}] EBS volume collection skipped: {e}")
    except botocore.exceptions.ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        msg = e.response.get("Error", {}).get("Message", str(e))
        warnings.append(f"[{region}] EBS volume ClientError ({code}): {msg}")
    except Exception as e:
        warnings.append(f"[{region}] EBS volume unexpected error: {e}")

    # Elastic IPs
    try:
        eip_records = collect_live_elastic_ips(ec2_client, account_id, region)
        records.extend(eip_records)
    except AWSError as e:
        warnings.append(f"[{region}] Elastic IP collection skipped: {e}")
    except botocore.exceptions.ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        msg = e.response.get("Error", {}).get("Message", str(e))
        warnings.append(f"[{region}] Elastic IP ClientError ({code}): {msg}")
    except Exception as e:
        warnings.append(f"[{region}] Elastic IP unexpected error: {e}")

    # CloudWatch CPU metrics (EC2 instances only, regional)
    if include_cloudwatch and ec2_records:
        try:
            cw_client = get_cloudwatch_client(session=session, region_name=region)
            instance_ids = [r["lineItem/ResourceId"] for r in ec2_records]
            metrics_map, cw_warns = collect_cloudwatch_ec2_metrics(
                cw_client, instance_ids,
                start_time=metric_start, end_time=metric_end,
            )
            warnings.extend(cw_warns)
            for rec in ec2_records:
                rid = rec["lineItem/ResourceId"]
                m = metrics_map.get(rid, {})
                rec["cpu_utilization_avg"] = m.get("cpu_utilization_avg", np.nan)
                rec["cpu_utilization_p95"] = m.get("cpu_utilization_p95", np.nan)
                obs = m.get("observed_days", 0)
                rec["observed_days"] = obs if obs else observation_days
        except Exception as e:
            warnings.append(
                f"[{region}] CloudWatch metrics error: {e}. CPU metrics set to NaN."
            )

    return records, warnings


# ---------------------------------------------------------------------------
# Multi-region coordinator
# ---------------------------------------------------------------------------

def scan_all_regions(
    session: Any,
    eligible_regions: List[str],
    account_id: str,
    observation_days: int = 14,
    include_cloudwatch: bool = True,
    max_workers: int = _DEFAULT_MAX_WORKERS,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """Scan every eligible region in parallel and merge results into one DataFrame.

    Args:
        session:          Authenticated ``boto3.Session``.
        eligible_regions: Region names to scan (from :func:`filter_eligible_regions`).
        account_id:       AWS account ID (string) for tagging records.
        observation_days: Look-back window in days for CloudWatch metrics.
        include_cloudwatch: Whether to fetch CloudWatch CPU data.
        max_workers:      Thread-pool concurrency cap.

    Returns:
        Tuple of (merged_dataframe, metadata_dict)
        ``metadata_dict`` includes:
            - ``regions_scanned``: list of successfully scanned region names
            - ``regions_failed``: list of regions with errors
            - ``region_errors``: dict mapping region -> list of warning strings
            - ``ec2_count``, ``ebs_count``, ``eip_count``, ``total_resources``
    """
    if not eligible_regions:
        return pd.DataFrame(), {
            "regions_scanned": [],
            "regions_failed": [],
            "region_errors": {},
            "ec2_count": 0,
            "ebs_count": 0,
            "eip_count": 0,
            "total_resources": 0,
            "warnings": ["No eligible regions to scan."],
        }

    now_utc = datetime.now(timezone.utc)
    metric_start = now_utc - timedelta(days=observation_days)
    time_interval_str = (
        f"{metric_start.date()}T00:00:00Z/{now_utc.date()}T00:00:00Z"
    )

    all_records: List[Dict[str, Any]] = []
    region_errors: Dict[str, List[str]] = {}
    regions_scanned: List[str] = []
    regions_failed: List[str] = []
    all_warnings: List[str] = []
    ec2_total = ebs_total = eip_total = 0

    # Fan-out across regions using a bounded thread pool.
    futures = {}
    with ThreadPoolExecutor(max_workers=min(max_workers, len(eligible_regions))) as pool:
        for region in eligible_regions:
            fut = pool.submit(
                _scan_single_region,
                session, region, account_id,
                observation_days, include_cloudwatch,
                metric_start, now_utc,
            )
            futures[fut] = region

        for fut in as_completed(futures):
            region = futures[fut]
            try:
                records, warns = fut.result()
                if warns:
                    region_errors[region] = warns
                    all_warnings.extend(warns)

                if records:
                    # Tag each record with the time interval used for the scan.
                    for rec in records:
                        rec.setdefault("identity/TimeInterval", time_interval_str)
                    # Count by service type
                    for rec in records:
                        svc = rec.get("product/servicecode", "")
                        if svc == "EC2":
                            ec2_total += 1
                        elif svc == "EBS":
                            ebs_total += 1
                        elif svc == "ElasticIP":
                            eip_total += 1
                    all_records.extend(records)
                    regions_scanned.append(region)
                else:
                    # Warn but don't treat zero resources as failure —
                    # the region may be simply empty.
                    if warns:
                        regions_failed.append(region)
                        all_warnings.append(
                            f"[{region}] Region scan produced errors; "
                            "see region_errors for details."
                        )
                    else:
                        # Empty region — still counts as scanned.
                        regions_scanned.append(region)

            except Exception as e:
                msg = f"[{region}] Unexpected scan failure: {e}"
                logger.error(msg)
                regions_failed.append(region)
                region_errors.setdefault(region, []).append(msg)
                all_warnings.append(msg)

    df = pd.DataFrame(all_records) if all_records else pd.DataFrame()
    if not df.empty and "identity/TimeInterval" not in df.columns:
        df["identity/TimeInterval"] = time_interval_str

    meta: Dict[str, Any] = {
        "regions_scanned": sorted(regions_scanned),
        "regions_failed": sorted(regions_failed),
        "region_errors": region_errors,
        "ec2_count": ec2_total,
        "ebs_count": ebs_total,
        "eip_count": eip_total,
        "total_resources": len(all_records),
        "warnings": all_warnings,
    }
    return df, meta
