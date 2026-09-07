"""Canonical schema normalization layer for FinSight.

Translates diverse data sources (Synthetic CUR, Uploaded CUR, Live AWS API discovery)
into a unified canonical internal DataFrame format for analysis engines.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
import numpy as np
import pandas as pd

# Canonical column definitions required by FinSight analysis engines
CANONICAL_COLUMNS: List[str] = [
    "lineItem/ResourceId",
    "lineItem/UsageAccountId",
    "identity/TimeInterval",
    "product/servicecode",
    "product/region",
    "product/instanceType",
    "resource_state",
    "lineItem/UnblendedCost",
    "lineItem/UnblendedRate",
    "cpu_utilization_avg",
    "cpu_utilization_p95",
    "network_usage_gb",
    "size_gb",
    "resourceTags/user:Owner",
    "resourceTags/user:Environment",
    "resourceTags/user:Cost-Center",
]

# Alias map for header normalization (Target Column -> List of known aliases)
COLUMN_ALIASES: Dict[str, List[str]] = {
    "lineItem/ResourceId": [
        "lineItem/ResourceId",
        "ResourceId",
        "resource_id",
        "resourceId",
        "Resource_Id",
        "id",
        "allocation_id",
        "volume_id",
        "instance_id",
    ],
    "lineItem/UsageAccountId": [
        "lineItem/UsageAccountId",
        "UsageAccountId",
        "account_id",
        "account",
        "AccountId",
        "lineItem_UsageAccountId",
        "bill/PayerAccountId",
        "PayerAccountId",
    ],
    "identity/TimeInterval": [
        "identity/TimeInterval",
        "TimeInterval",
        "time_interval",
        "lineItem/UsageStartDate",
        "UsageStartDate",
        "usage_date",
        "date",
    ],
    "product/servicecode": [
        "product/servicecode",
        "servicecode",
        "ServiceCode",
        "service",
        "resource_type",
        "product_code",
        "lineItem/ProductCode",
        "ProductCode",
    ],
    "product/region": [
        "product/region",
        "region",
        "Region",
        "product_region",
        "product/Region",
        "AvailabilityZone",
    ],
    "product/instanceType": [
        "product/instanceType",
        "instanceType",
        "instance_type",
        "InstanceType",
        "product_instanceType",
    ],
    "resource_state": [
        "resource_state",
        "state",
        "State",
        "Status",
        "status",
        "instance_state",
    ],
    "lineItem/UnblendedCost": [
        "lineItem/UnblendedCost",
        "UnblendedCost",
        "unblended_cost",
        "cost",
        "Cost",
        "unblended_cost_usd",
        "monthly_cost",
    ],
    "lineItem/UnblendedRate": [
        "lineItem/UnblendedRate",
        "UnblendedRate",
        "unblended_rate",
        "rate",
        "hourly_rate",
        "Rate",
    ],
    "cpu_utilization_avg": [
        "cpu_utilization_avg",
        "cpu_avg",
        "avg_cpu",
        "CPUUtilization_Average",
        "cpu_profile",
        "cpu_avg_pct",
    ],
    "cpu_utilization_p95": [
        "cpu_utilization_p95",
        "cpu_p95",
        "p95_cpu",
        "CPUUtilization_p95",
        "cpu_max_pct",
    ],
    "network_usage_gb": [
        "network_usage_gb",
        "network_gb",
        "network_profile",
        "network_transfer_gb",
    ],
    "size_gb": [
        "size_gb",
        "size",
        "volume_size",
        "VolumeSize",
        "Size",
    ],
    "resourceTags/user:Owner": [
        "resourceTags/user:Owner",
        "user:Owner",
        "tag:Owner",
        "Owner",
        "tag_Owner",
        "owner",
        "tag/Owner",
    ],
    "resourceTags/user:Environment": [
        "resourceTags/user:Environment",
        "user:Environment",
        "tag:Environment",
        "Environment",
        "tag_Environment",
        "env",
        "environment",
        "tag/Environment",
    ],
    "resourceTags/user:Cost-Center": [
        "resourceTags/user:Cost-Center",
        "user:Cost-Center",
        "tag:Cost-Center",
        "Cost-Center",
        "CostCenter",
        "resourceTags/user:CostCenter",
        "user:CostCenter",
        "tag:CostCenter",
        "cost_center",
        "tag_Cost-Center",
        "tag_CostCenter",
        "tag/Cost-Center",
    ],
}

# Service name mapping to canonical names
SERVICE_MAPPING: Dict[str, str] = {
    "amazonec2": "EC2",
    "amazon ec2": "EC2",
    "ec2": "EC2",
    "amazon elastic compute cloud - compute": "EC2",
    "amazonec2-ebs": "EBS",
    "ebs": "EBS",
    "amazon elastic block store": "EBS",
    "amazon elastic compute cloud - elastic block store": "EBS",
    "amazonelasticip": "ElasticIP",
    "elasticip": "ElasticIP",
    "elastic ip": "ElasticIP",
    "eip": "ElasticIP",
}


def _normalize_service_name(val: Any) -> str:
    """Normalize service name strings to EC2, EBS, or ElasticIP."""
    if pd.isna(val) or val is None:
        return ""
    text = str(val).strip()
    lower = text.lower()
    return SERVICE_MAPPING.get(lower, text)


def _normalize_resource_state(val: Any) -> str:
    """Normalize state strings across services."""
    if pd.isna(val) or val is None:
        return "unknown"
    text = str(val).strip().lower()
    return text


def normalize_cur_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize any CUR DataFrame (synthetic, uploaded CSV/Parquet, or live) to canonical schema.

    Preserves original columns while guaranteeing canonical schema compliance.
    """
    if df.empty:
        return pd.DataFrame(columns=CANONICAL_COLUMNS)

    out = df.copy()

    # 1. Alias mapping: coalesce values across all matching alias columns into canonical column
    for canonical_col, aliases in COLUMN_ALIASES.items():
        existing_aliases = [col for col in aliases if col in out.columns]
        if existing_aliases:
            combined: Optional[pd.Series] = None
            if canonical_col in out.columns:
                combined = out[canonical_col].copy()
            for alias in existing_aliases:
                if alias == canonical_col:
                    continue
                if combined is None:
                    combined = out[alias].copy()
                else:
                    combined = combined.combine_first(out[alias])
            if combined is not None:
                out[canonical_col] = combined

    # 2. Guarantee existence of all canonical columns with default values
    defaults: Dict[str, Any] = {
        "lineItem/ResourceId": "",
        "lineItem/UsageAccountId": "000000000000",
        "identity/TimeInterval": "1970-01-01T00:00:00Z/1970-01-02T00:00:00Z",
        "product/servicecode": "EC2",
        "product/region": "us-east-1",
        "product/instanceType": "",
        "resource_state": "running",
        "lineItem/UnblendedCost": np.nan,
        "lineItem/UnblendedRate": np.nan,
        "cpu_utilization_avg": np.nan,
        "cpu_utilization_p95": np.nan,
        "network_usage_gb": np.nan,
        "size_gb": 0,
        "resourceTags/user:Owner": "",
        "resourceTags/user:Environment": "",
        "resourceTags/user:Cost-Center": "",
    }

    for col, default_val in defaults.items():
        if col not in out.columns:
            out[col] = default_val
        elif out[col].isna().any():
            if not (isinstance(default_val, float) and np.isnan(default_val)):
                out[col] = out[col].fillna(default_val)


    # 3. Clean string columns
    str_cols = [
        "lineItem/ResourceId",
        "lineItem/UsageAccountId",
        "product/region",
        "product/instanceType",
        "resourceTags/user:Owner",
        "resourceTags/user:Environment",
        "resourceTags/user:Cost-Center",
    ]
    for c in str_cols:
        out[c] = out[c].fillna("").astype(str).str.strip()

    # 4. Service code & State normalization
    out["product/servicecode"] = out["product/servicecode"].apply(_normalize_service_name)
    out["resource_state"] = out["resource_state"].apply(_normalize_resource_state)

    # 5. Numeric conversions
    float_cols = [
        "lineItem/UnblendedCost",
        "lineItem/UnblendedRate",
        "cpu_utilization_avg",
        "cpu_utilization_p95",
        "network_usage_gb",
    ]
    for c in float_cols:
        out[c] = pd.to_numeric(out[c], errors="coerce")

    # size_gb to integer
    out["size_gb"] = pd.to_numeric(out["size_gb"], errors="coerce").fillna(0).astype(int)

    # If cpu_utilization_p95 is missing but cpu_utilization_avg exists, fall back
    p95_mask = out["cpu_utilization_p95"].isna() & out["cpu_utilization_avg"].notna()
    out.loc[p95_mask, "cpu_utilization_p95"] = out.loc[p95_mask, "cpu_utilization_avg"]

    return out


def normalize_live_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize live AWS collected resource DataFrame."""
    return normalize_cur_dataframe(df)


def validate_canonical_schema(df: pd.DataFrame) -> Dict[str, Any]:
    """Validate that a DataFrame conforms to the canonical FinSight schema.

    Returns:
        Dict with keys:
        - is_valid: bool
        - missing_required: list of missing canonical column names
        - present_columns: list of present columns
        - extra_columns: list of non-canonical columns preserved
        - row_count: int
    """
    present = list(df.columns)
    missing = [col for col in CANONICAL_COLUMNS if col not in present]
    extra = [col for col in present if col not in CANONICAL_COLUMNS]

    return {
        "is_valid": len(missing) == 0,
        "missing_required": missing,
        "present_columns": present,
        "extra_columns": extra,
        "row_count": len(df),
    }
