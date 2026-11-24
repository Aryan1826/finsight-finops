"""Configurable tagging governance rules."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, List, Optional

import pandas as pd
import yaml

logger = logging.getLogger(__name__)

# Column aliases for schema flexibility
RESOURCE_ID_ALIASES = [
    "lineItem/ResourceId", "ResourceId", "resource_id", "resourceId",
    "Resource_Id", "id", "allocation_id", "volume_id", "instance_id"
]
ACCOUNT_ID_ALIASES = [
    "lineItem/UsageAccountId", "UsageAccountId", "account_id", "account",
    "AccountId", "lineItem_UsageAccountId", "bill/PayerAccountId", "PayerAccountId"
]
SERVICE_CODE_ALIASES = [
    "product/servicecode", "servicecode", "ServiceCode", "service",
    "resource_type", "product_code", "lineItem/ProductCode", "ProductCode"
]
REGION_ALIASES = [
    "product/region", "region", "Region", "product_region", "product/Region",
    "AvailabilityZone"
]


def _find_column(df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
    """Find the first matching candidate column present in DataFrame."""
    for c in candidates:
        if c in df.columns:
            return c
    return None


def _find_tag_column(df: pd.DataFrame, tag_name: str) -> Optional[str]:
    """Find column containing values for the specified tag name across common conventions."""
    candidates = [
        f"resourceTags/user:{tag_name}",
        f"user:{tag_name}",
        f"tag:{tag_name}",
        f"tag_{tag_name}",
        f"tag/{tag_name}",
        tag_name,
        tag_name.lower(),
    ]
    # Special aliases for common tags
    if tag_name.lower() == "environment":
        candidates.extend(["env", "resourceTags/user:env", "tag:env"])
    elif tag_name.lower() in ("cost-center", "costcenter"):
        candidates.extend([
            "resourceTags/user:CostCenter", "resourceTags/user:Cost-Center",
            "CostCenter", "Cost-Center", "cost_center", "tag:CostCenter", "tag:Cost-Center"
        ])
    return _find_column(df, candidates)


def load_policy(path: str | Path = "config/tagging_policy.yaml") -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def evaluate_tagging_compliance(df: pd.DataFrame, policy: dict[str, Any]) -> tuple[pd.DataFrame, float]:
    """Return per-resource tag compliance and overall percentage.

    Safely handles:
    - Canonical CUR schema with 'lineItem/ResourceId', etc.
    - Live AWS multi-region inventory.
    - User uploaded or minimal datasets with alias column names.
    - Datasets with missing required metadata or tag columns.
    - Empty DataFrames.
    """
    required = policy.get("required_tags", ["Owner", "Environment", "Cost-Center"])
    allowed_values = policy.get("allowed_values", {})

    canonical_cols = [
        "lineItem/ResourceId",
        "lineItem/UsageAccountId",
        "product/servicecode",
        "product/region",
        *[f"resourceTags/user:{t}" for t in required],
        "compliant",
        "tagging_evaluable",
        "missing_or_invalid_tags",
    ]

    if not isinstance(df, pd.DataFrame) or df.empty:
        return pd.DataFrame(columns=canonical_cols), 100.0

    # 1. Resolve primary resource identifier
    rid_col = _find_column(df, RESOURCE_ID_ALIASES)
    account_col = _find_column(df, ACCOUNT_ID_ALIASES)
    service_col = _find_column(df, SERVICE_CODE_ALIASES)
    region_col = _find_column(df, REGION_ALIASES)

    # If no resource ID column exists, tagging cannot be evaluated per resource
    if rid_col is None:
        logger.warning("evaluate_tagging_compliance: No resource ID column found.")
        out = pd.DataFrame(columns=canonical_cols)
        return out, 100.0

    base = df.drop_duplicates(rid_col).copy()

    # Set canonical metadata columns
    base["lineItem/ResourceId"] = base[rid_col].fillna("").astype(str).str.strip()
    base["lineItem/UsageAccountId"] = base[account_col].fillna("").astype(str).str.strip() if account_col else "unknown"
    base["product/servicecode"] = base[service_col].fillna("").astype(str).str.strip() if service_col else "unknown"
    base["product/region"] = base[region_col].fillna("").astype(str).str.strip() if region_col else "unknown"

    def valid_value(tag: str, value: Any) -> bool:
        text = "" if pd.isna(value) else str(value).strip()
        if not text:
            return False
        allowed = allowed_values.get(tag)
        return True if not allowed else text in allowed

    tag_cols_found = 0
    for tag in required:
        canon_tag_col = f"resourceTags/user:{tag}"
        source_col = _find_tag_column(base, tag)
        if source_col is not None and source_col in base.columns:
            base[canon_tag_col] = base[source_col]
            tag_cols_found += 1
        else:
            base[canon_tag_col] = ""

        base[f"tag_ok_{tag}"] = base[canon_tag_col].apply(lambda v: valid_value(tag, v))

    # Determine if tagging data was evaluable (at least one tag column candidate existed in input)
    base["tagging_evaluable"] = tag_cols_found > 0

    ok_cols = [f"tag_ok_{tag}" for tag in required]
    base["compliant"] = base[ok_cols].all(axis=1)

    base["missing_or_invalid_tags"] = base.apply(
        lambda r: ", ".join(tag for tag in required if not bool(r[f"tag_ok_{tag}"])), axis=1
    )

    # Compliance percentage is computed on evaluable resources; if none evaluable, defaults to 100.0
    evaluable = base[base["tagging_evaluable"]]
    if len(evaluable) > 0:
        compliance = float(evaluable["compliant"].mean() * 100.0)
    else:
        compliance = 100.0

    keep = [
        "lineItem/ResourceId",
        "lineItem/UsageAccountId",
        "product/servicecode",
        "product/region",
        *[f"resourceTags/user:{t}" for t in required],
        "compliant",
        "tagging_evaluable",
        "missing_or_invalid_tags",
    ]
    return base[keep], compliance

