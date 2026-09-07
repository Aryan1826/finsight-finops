"""Rule-based EC2 rightsizing engine using historical utilization."""
from __future__ import annotations

import math
import pandas as pd

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


def recommend_rightsizing(
    df: pd.DataFrame,
    p95_threshold: float = 20.0,
    p95_hard_stop: float = 60.0,
    monthly_hours: int = 730,
) -> pd.DataFrame:
    """Recommend one-step downsize where p95 CPU is low enough and capacity headroom exists."""
    x = df[df["product/servicecode"] == "EC2"].copy()
    if x.empty:
        return pd.DataFrame()

    x["cost"] = pd.to_numeric(x["lineItem/UnblendedCost"], errors="coerce").fillna(0.0)
    rows = []
    for (rid, account), g in x.groupby(["lineItem/ResourceId", "lineItem/UsageAccountId"], dropna=False):
        current = str(g["product/instanceType"].dropna().iloc[0])
        if current not in DOWNSIZE_MAP:
            continue
        cpu = pd.to_numeric(g["cpu_utilization_avg"], errors="coerce").dropna()
        if cpu.empty:
            continue
        p95 = float(cpu.quantile(0.95))
        if not (p95 < p95_threshold and p95 < p95_hard_stop):
            continue
        recommended = DOWNSIZE_MAP[current]
        current_rate = INSTANCE_RATES.get(current, float(g["lineItem/UnblendedRate"].mean()))
        recommended_rate = INSTANCE_RATES.get(recommended)
        current_monthly = current_rate * monthly_hours
        recommended_monthly = recommended_rate * monthly_hours
        savings = max(0.0, current_monthly - recommended_monthly)
        rows.append({
            "account": account,
            "resource_id": rid,
            "region": g["product/region"].iloc[0],
            "current_instance_type": current,
            "recommended_instance_type": recommended,
            "p95_cpu_pct": round(p95, 2),
            "current_monthly_cost_usd": round(current_monthly, 2),
            "recommended_monthly_cost_usd": round(recommended_monthly, 2),
            "estimated_monthly_savings_usd": round(savings, 2),
            "reason": f"p95 CPU {p95:.1f}% is below {p95_threshold:.0f}% threshold",
        })
    return pd.DataFrame(rows).sort_values("estimated_monthly_savings_usd", ascending=False) if rows else pd.DataFrame()
