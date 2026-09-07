"""Configurable tagging governance rules."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import yaml


def load_policy(path: str | Path = "config/tagging_policy.yaml") -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def evaluate_tagging_compliance(df: pd.DataFrame, policy: dict[str, Any]) -> tuple[pd.DataFrame, float]:
    """Return per-resource tag compliance and overall percentage."""
    required = policy.get("required_tags", ["Owner", "Environment", "Cost-Center"])
    allowed_values = policy.get("allowed_values", {})
    base = df.drop_duplicates("lineItem/ResourceId").copy()

    def valid_value(tag: str, value: Any) -> bool:
        text = "" if pd.isna(value) else str(value).strip()
        if not text:
            return False
        allowed = allowed_values.get(tag)
        return True if not allowed else text in allowed

    for tag in required:
        col = f"resourceTags/user:{tag}"
        if col not in base.columns:
            base[col] = ""
        base[f"tag_ok_{tag}"] = base[col].apply(lambda v: valid_value(tag, v))

    ok_cols = [f"tag_ok_{tag}" for tag in required]
    base["compliant"] = base[ok_cols].all(axis=1)
    base["missing_or_invalid_tags"] = base.apply(
        lambda r: ", ".join(tag for tag in required if not bool(r[f"tag_ok_{tag}"])), axis=1
    )
    compliance = float(base["compliant"].mean() * 100) if len(base) else 100.0
    keep = ["lineItem/ResourceId", "lineItem/UsageAccountId", "product/servicecode", "product/region", *[f"resourceTags/user:{t}" for t in required], "compliant", "missing_or_invalid_tags"]
    return base[keep], compliance
