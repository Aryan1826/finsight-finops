"""Rule-based EC2 rightsizing engine using historical utilization.

Safely supports:
1. Synthetic/CUR datasets (with canonical or alias column names).
2. Normalized internal DataFrames.
3. Live AWS EC2 inventory with CloudWatch CPU metrics.
4. Missing service, resource ID, instance type, rate, or CPU columns without raising KeyError.
"""
from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

INSTANCE_RATES = {
    "t3.nano": 0.0052,
    "t3.micro": 0.0104,
    "t3.small": 0.0208,
    "t3.medium": 0.0416,
    "t3.large": 0.0832,
    "t3.xlarge": 0.1664,
    "t3.2xlarge": 0.3328,
    "m5.large": 0.096,
    "m5.xlarge": 0.192,
    "m5.2xlarge": 0.384,
    "m5.4xlarge": 0.768,
}
DOWNSIZE_MAP = {
    "t3.2xlarge": "t3.xlarge", "t3.xlarge": "t3.large", "t3.large": "t3.medium",
    "t3.medium": "t3.small", "t3.small": "t3.micro", "t3.micro": "t3.nano",
    "m5.4xlarge": "m5.2xlarge", "m5.2xlarge": "m5.xlarge", "m5.xlarge": "m5.large",
}

# Column aliases for schema flexibility
SERVICE_CODE_ALIASES = [
    "product/servicecode", "servicecode", "ServiceCode", "service",
    "resource_type", "product_code", "lineItem/ProductCode", "ProductCode"
]
RESOURCE_ID_ALIASES = [
    "lineItem/ResourceId", "ResourceId", "resource_id", "resourceId",
    "Resource_Id", "id", "instance_id"
]
ACCOUNT_ID_ALIASES = [
    "lineItem/UsageAccountId", "UsageAccountId", "account_id", "account",
    "AccountId", "lineItem_UsageAccountId", "bill/PayerAccountId", "PayerAccountId"
]
INSTANCE_TYPE_ALIASES = [
    "product/instanceType", "instanceType", "instance_type", "InstanceType",
    "product_instanceType"
]
REGION_ALIASES = [
    "product/region", "region", "Region", "product_region", "product/Region",
    "AvailabilityZone"
]
RATE_ALIASES = [
    "lineItem/UnblendedRate", "UnblendedRate", "unblended_rate", "rate",
    "hourly_rate", "Rate"
]
CPU_AVG_ALIASES = [
    "cpu_utilization_avg", "cpu_avg", "avg_cpu", "CPUUtilization_Average",
    "cpu_profile", "cpu_avg_pct"
]
CPU_P95_ALIASES = [
    "cpu_utilization_p95", "cpu_p95", "p95_cpu", "CPUUtilization_p95",
    "cpu_max_pct"
]


def _find_column(df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
    """Find the first matching candidate column present in DataFrame."""
    for c in candidates:
        if c in df.columns:
            return c
    return None


def recommend_rightsizing(
    df: pd.DataFrame,
    p95_threshold: float = 20.0,
    p95_hard_stop: float = 60.0,
    monthly_hours: int = 730,
) -> pd.DataFrame:
    """Recommend one-step downsize where p95 CPU is low enough and capacity headroom exists.

    Returns an empty DataFrame safely if:
    - Input DataFrame is empty.
    - No EC2 resources can be identified.
    - CPU utilization metrics are missing or unavailable.
    """
    if not isinstance(df, pd.DataFrame) or df.empty:
        return pd.DataFrame()

    # 1. Identify service column to isolate EC2 instances
    service_col = _find_column(df, SERVICE_CODE_ALIASES)
    if service_col is not None:
        # Match case-insensitively for "ec2" or standard full strings
        service_series = df[service_col].fillna("").astype(str).str.strip().str.lower()
        ec2_mask = service_series.isin([
            "ec2",
            "amazonec2",
            "amazon ec2",
            "amazon elastic compute cloud - compute",
        ])
        x = df[ec2_mask].copy()
    else:
        # If no service code column exists, check if instance type column exists
        itype_col = _find_column(df, INSTANCE_TYPE_ALIASES)
        if itype_col is not None:
            # Assume rows with a recognized EC2 instance type in DOWNSIZE_MAP or INSTANCE_RATES
            known_types = set(INSTANCE_RATES.keys()).union(set(DOWNSIZE_MAP.keys()))
            x = df[df[itype_col].fillna("").astype(str).isin(known_types)].copy()
        else:
            logger.warning("recommend_rightsizing: No service code or instance type column found.")
            return pd.DataFrame()

    if x.empty:
        return pd.DataFrame()

    # 2. Identify required resource identifier columns
    rid_col = _find_column(x, RESOURCE_ID_ALIASES)
    if rid_col is None:
        logger.warning("recommend_rightsizing: No resource ID column found in dataset.")
        return pd.DataFrame()

    account_col = _find_column(x, ACCOUNT_ID_ALIASES)
    itype_col = _find_column(x, INSTANCE_TYPE_ALIASES)
    region_col = _find_column(x, REGION_ALIASES)
    rate_col = _find_column(x, RATE_ALIASES)
    cpu_avg_col = _find_column(x, CPU_AVG_ALIASES)
    cpu_p95_col = _find_column(x, CPU_P95_ALIASES)

    # If instance type column is missing, cannot recommend rightsizing
    if itype_col is None:
        logger.warning("recommend_rightsizing: No instance type column found.")
        return pd.DataFrame()

    # If both CPU avg and CPU p95 columns are missing, cannot evaluate utilization
    if cpu_avg_col is None and cpu_p95_col is None:
        logger.info("recommend_rightsizing: CPU utilization metrics unavailable for rightsizing.")
        return pd.DataFrame()

    group_cols = [rid_col]
    if account_col is not None:
        group_cols.append(account_col)

    rows = []
    for group_key, g in x.groupby(group_cols, dropna=False):
        if isinstance(group_key, tuple):
            rid = str(group_key[0])
            account = str(group_key[1]) if len(group_key) > 1 and pd.notna(group_key[1]) else "unknown"
        else:
            rid = str(group_key)
            account = str(g[account_col].dropna().iloc[0]) if account_col is not None and not g[account_col].dropna().empty else "unknown"

        current_val = g[itype_col].dropna()
        if current_val.empty:
            continue
        current = str(current_val.iloc[0]).strip()
        if current not in DOWNSIZE_MAP:
            continue

        # Extract p95 CPU: prefer precomputed cpu_utilization_p95 if valid, else calculate from cpu_utilization_avg
        p95 = None
        if cpu_p95_col is not None:
            p95_series = pd.to_numeric(g[cpu_p95_col], errors="coerce").dropna()
            if not p95_series.empty:
                p95 = float(p95_series.iloc[0])

        if p95 is None or math.isnan(p95):
            if cpu_avg_col is not None:
                cpu_series = pd.to_numeric(g[cpu_avg_col], errors="coerce").dropna()
                if not cpu_series.empty:
                    p95 = float(cpu_series.quantile(0.95))

        if p95 is None or math.isnan(p95):
            continue

        if not (p95 < p95_threshold and p95 < p95_hard_stop):
            continue

        recommended = DOWNSIZE_MAP[current]

        # Determine rate
        current_rate = None
        if current in INSTANCE_RATES:
            current_rate = INSTANCE_RATES[current]
        elif rate_col is not None:
            rate_series = pd.to_numeric(g[rate_col], errors="coerce").dropna()
            if not rate_series.empty:
                current_rate = float(rate_series.mean())

        recommended_rate = INSTANCE_RATES.get(recommended)
        if current_rate is None or recommended_rate is None:
            continue

        current_monthly = current_rate * monthly_hours
        recommended_monthly = recommended_rate * monthly_hours
        savings = max(0.0, current_monthly - recommended_monthly)

        region_val = "unknown"
        if region_col is not None and not g[region_col].dropna().empty:
            region_val = str(g[region_col].dropna().iloc[0])

        rows.append({
            "account": account,
            "resource_id": rid,
            "region": region_val,
            "current_instance_type": current,
            "recommended_instance_type": recommended,
            "p95_cpu_pct": round(p95, 2),
            "current_monthly_cost_usd": round(current_monthly, 2),
            "recommended_monthly_cost_usd": round(recommended_monthly, 2),
            "estimated_monthly_savings_usd": round(savings, 2),
            "reason": f"p95 CPU {p95:.1f}% is below {p95_threshold:.0f}% threshold",
        })

    if not rows:
        return pd.DataFrame()

    return pd.DataFrame(rows).sort_values("estimated_monthly_savings_usd", ascending=False)

