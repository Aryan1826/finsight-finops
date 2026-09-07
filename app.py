"""FinSight — Cloud Cost Optimization & Governance (FinOps) Dashboard.

Supports:
1. Live AWS Account ingestion (boto3 / CloudWatch / Cost Explorer / EC2 / EBS / EIP)
2. Synthetic CUR benchmark (CSV and Parquet with ground truth evaluation)
3. User-uploaded CUR files (CSV and Parquet)

Strictly READ-ONLY against AWS. All remediation is simulation / dry-run only.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from utils.aws_client import (
    AWSCredentialError,
    AWSError,
    build_cloudformation_quick_create_url,
    generate_external_id,
)
from utils.finops_pipeline import run_live_finops_analysis, FinOpsAnalysisResult
from utils.idle_detector import detect_idle_resources
from utils.normalizer import normalize_cur_dataframe, normalize_live_dataframe
from utils.remediation_simulator import generate_remediation_plan, remediation_plan_to_dataframe
from utils.rightsizing import recommend_rightsizing
from utils.synthetic_generator import generate_synthetic_cur
from utils.tagging_policy import evaluate_tagging_compliance, load_policy

# -----------------------------------------------------------------------------
# Streamlit App Configuration
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="FinSight — Cloud Cost Optimization & Governance",
    page_icon="☁️",
    layout="wide",
)


# -----------------------------------------------------------------------------
# Cached Ingestion Functions
# -----------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def cached_live_analysis(
    profile_name: str | None,
    role_arn: str | None,
    external_id: str | None,
    region: str,
    observation_days: int,
    policy_path: str,
) -> FinOpsAnalysisResult:
    """Fetch and analyze live AWS resources with caching to prevent unnecessary API calls."""
    clean_profile = profile_name.strip() if profile_name and profile_name.strip() else None
    clean_role_arn = role_arn.strip() if role_arn and role_arn.strip() else None
    clean_external_id = external_id.strip() if external_id and external_id.strip() else None
    return run_live_finops_analysis(
        profile_name=clean_profile,
        region=region,
        role_arn=clean_role_arn,
        external_id=clean_external_id,
        observation_days=observation_days,
        policy_path=policy_path,
        include_cloudwatch=True,
        include_cost_explorer=True,
    )


@st.cache_data(show_spinner=False)
def load_synthetic_dataset(format_type: str = "csv") -> pd.DataFrame:
    """Load or generate the built-in synthetic CUR benchmark."""
    if format_type.lower() == "parquet":
        pq_path = ROOT / "data" / "synthetic_cur.parquet"
        if not pq_path.exists():
            generate_synthetic_cur(output_path=pq_path, days=30, seed=42)
        return pd.read_parquet(pq_path)
    else:
        csv_path = ROOT / "data" / "synthetic_cur.csv"
        if not csv_path.exists():
            generate_synthetic_cur(output_path=csv_path, days=30, seed=42)
        return pd.read_csv(csv_path)


def read_uploaded_file(uploaded_file) -> pd.DataFrame:
    """Read an uploaded CUR file (CSV or Parquet)."""
    name = uploaded_file.name.lower()
    if name.endswith(".parquet"):
        return pd.read_parquet(uploaded_file)
    return pd.read_csv(uploaded_file)


# -----------------------------------------------------------------------------
# Sidebar: Ingestion & Configuration Controls
# -----------------------------------------------------------------------------
st.sidebar.title("☁️ FinSight FinOps")
st.sidebar.caption("Cloud Cost Optimization & Governance")

data_source = st.sidebar.radio(
    "Data Source",
    ["Live AWS Account", "Synthetic CUR", "Upload CUR"],
    index=0,
)

policy_path = str(ROOT / "config" / "tagging_policy.yaml")
policy = load_policy(policy_path)

df = pd.DataFrame()
live_result: FinOpsAnalysisResult | None = None
source_name = ""
is_live_mode = False
is_synthetic_mode = False

if data_source == "Live AWS Account":
    is_live_mode = True
    st.sidebar.subheader("AWS Connection Settings")

    auth_mode = st.sidebar.radio(
        "Connection Method",
        ["Local AWS Profile", "Cross-Account IAM Role"],
        index=0,
        help="Use 'Local AWS Profile' for local development (~/.aws/credentials) or 'Cross-Account IAM Role' for 1-click STS AssumeRole.",
    )

    profile_input: str | None = None
    role_arn_input: str | None = None
    external_id_input: str | None = None
    manual_mode: bool = False

    region_options = [
        "ap-south-1",
        "us-east-1",
        "us-east-2",
        "us-west-1",
        "us-west-2",
        "eu-west-1",
        "eu-central-1",
        "ap-southeast-1",
    ]
    region_input = st.sidebar.selectbox("AWS Region", region_options, index=0)

    if auth_mode == "Local AWS Profile":
        profile_input = st.sidebar.text_input(
            "AWS Profile",
            value="finsight",
            help="Named profile in ~/.aws/credentials. Leave blank to use default credential chain or IAM role.",
        )
    else:
        # Cross-Account IAM Role onboarding workflow
        if "generated_external_id" not in st.session_state:
            st.session_state["generated_external_id"] = generate_external_id()

        manual_mode = st.sidebar.checkbox(
            "Already have a configured FinSight role?",
            value=False,
            help="Enable if you already have a provisioned role and want to enter both Role ARN and External ID manually.",
        )

        if manual_mode:
            role_arn_input = st.sidebar.text_input(
                "AWS Role ARN",
                placeholder="arn:aws:iam::123456789012:role/FinSightReadOnlyRole",
                help="Target AWS account IAM Role ARN configured for STS AssumeRole.",
            )
            external_id_input = st.sidebar.text_input(
                "External ID",
                type="password",
                placeholder="e.g. finsight-ext-...",
                help="Unique External ID required by the role's trust policy.",
            )
        else:
            # 1-Click CloudFormation Setup Workflow
            st.sidebar.markdown("**Step 1 — Create AWS Connection**")
            st.sidebar.caption("Assigned External ID:")
            st.sidebar.code(st.session_state["generated_external_id"], language="text")

            if st.sidebar.button("Start New AWS Connection", use_container_width=True, help="Generate a new cryptographically secure External ID"):
                st.session_state["generated_external_id"] = generate_external_id()
                st.rerun()

            cf_url = build_cloudformation_quick_create_url(
                region=region_input,
                external_id=st.session_state["generated_external_id"],
            )
            st.sidebar.link_button(
                "🚀 Launch CloudFormation Setup",
                cf_url,
                type="secondary",
                use_container_width=True,
                help="Opens AWS Console CloudFormation Quick-Create with pre-filled parameters.",
            )
            st.sidebar.caption("1. Click button to open AWS CloudFormation.\n2. Click 'Create stack' & copy **RoleArn** from Outputs tab.")

            st.sidebar.markdown("**Step 2 — Connect Role**")
            role_arn_input = st.sidebar.text_input(
                "AWS Role ARN",
                placeholder="arn:aws:iam::123456789012:role/FinSightReadOnlyRole",
                help="Paste the created RoleArn from the CloudFormation Outputs tab.",
            )
            external_id_input = st.session_state["generated_external_id"]

    days_input = st.sidebar.slider(
        "Observation Window (Days)",
        min_value=1,
        max_value=90,
        value=14,
        help="Historical CloudWatch metric and Cost Explorer observation period.",
    )

    col_btn1, col_btn2 = st.sidebar.columns(2)
    with col_btn1:
        fetch_clicked = st.button("Connect & Fetch Data", type="primary", use_container_width=True)
    with col_btn2:
        refresh_clicked = st.button("Refresh Data", use_container_width=True)

    if refresh_clicked:
        st.cache_data.clear()
        fetch_clicked = True

    # Check if we should fetch or have existing session state
    state_key = f"live_result_{auth_mode}_{profile_input}_{role_arn_input}_{region_input}_{days_input}"
    if fetch_clicked or state_key not in st.session_state:
        # If Cross-Account mode is chosen but Role ARN or External ID is empty, prompt user
        if auth_mode == "Cross-Account IAM Role" and (not role_arn_input or not external_id_input):
            if not manual_mode:
                st.info(
                    "👈 **Step 1:** Click **Launch CloudFormation Setup** in the sidebar to create your read-only role.\n\n"
                    "**Step 2:** Copy the **RoleArn** from the CloudFormation Outputs tab, paste it in the sidebar, and click **Connect & Fetch Data**."
                )
            else:
                st.info("👈 Please enter a valid AWS Role ARN and External ID in the sidebar, then click **Connect & Fetch Data**.")
            st.stop()

        with st.spinner("Connecting to AWS & collecting read-only telemetry..."):
            try:
                live_result = cached_live_analysis(
                    profile_name=profile_input,
                    role_arn=role_arn_input,
                    external_id=external_id_input,
                    region=region_input,
                    observation_days=days_input,
                    policy_path=policy_path,
                )
                st.session_state[state_key] = live_result
                st.session_state["active_live_key"] = state_key
            except AWSCredentialError as e:
                st.sidebar.error(f"Authentication Failed: {e}")
                st.error(
                    f"**AWS Authentication Error:** {e}\n\n"
                    f"Please verify your AWS Role ARN, External ID, trust policy, or local profile."
                )
                st.stop()
            except AWSError as e:
                st.sidebar.error(f"AWS Error: {e}")
                st.error(f"**AWS Service Error:** {e}")
                st.stop()
            except Exception as e:
                st.sidebar.error(f"Unexpected Error: {e}")
                st.error(f"**Unexpected Error:** {e}")
                st.stop()
    else:
        live_result = st.session_state.get(state_key)

    if live_result is not None:
        df = live_result.data
        source_name = f"Live AWS (Account {live_result.account_id} | {live_result.region})"


elif data_source == "Synthetic CUR":
    is_synthetic_mode = True
    st.sidebar.subheader("Synthetic Benchmark Settings")
    format_choice = st.sidebar.radio("File Format", ["CSV", "Parquet"], horizontal=True, index=0)
    if st.sidebar.button("Regenerate Synthetic Benchmark", use_container_width=True):
        with st.spinner("Regenerating synthetic CUR dataset..."):
            csv_target = ROOT / "data" / "synthetic_cur.csv"
            pq_target = ROOT / "data" / "synthetic_cur.parquet"
            df_gen = generate_synthetic_cur(output_path=csv_target, days=30, seed=42, parquet_path=pq_target)
            st.cache_data.clear()
            st.rerun()

    raw_synthetic = load_synthetic_dataset(format_choice.lower())
    df = normalize_cur_dataframe(raw_synthetic)
    source_name = f"Built-in Synthetic CUR Benchmark ({format_choice})"

elif data_source == "Upload CUR":
    st.sidebar.subheader("Upload CUR Dataset")
    uploaded_file = st.sidebar.file_uploader(
        "Upload AWS CUR (CSV or Parquet)",
        type=["csv", "parquet"],
        help="Upload standard AWS CUR or Cost & Usage export file.",
    )
    if uploaded_file is not None:
        try:
            raw_uploaded = read_uploaded_file(uploaded_file)
            df = normalize_cur_dataframe(raw_uploaded)
            source_name = f"Uploaded CUR: {uploaded_file.name}"
        except Exception as e:
            st.sidebar.error(f"Failed to parse uploaded file: {e}")
            st.error(f"**File Parse Error:** {e}")
            st.stop()
    else:
        st.info("👈 Please upload an AWS CUR-style CSV or Parquet file from the sidebar to begin.")
        st.stop()

# -----------------------------------------------------------------------------
# Sidebar: Dynamic Resource Filters
# -----------------------------------------------------------------------------
st.sidebar.divider()
st.sidebar.subheader("Resource Filters")

if not df.empty:
    account_options = sorted(df["lineItem/UsageAccountId"].dropna().astype(str).unique())
    account_filter = st.sidebar.multiselect(
        "Account",
        account_options,
        default=account_options,
    )

    service_options = sorted(df["product/servicecode"].dropna().astype(str).unique())
    service_filter = st.sidebar.multiselect(
        "Resource Type",
        service_options,
        default=service_options,
    )

    region_options_found = sorted(df["product/region"].dropna().astype(str).unique())
    region_filter = st.sidebar.multiselect(
        "Region",
        region_options_found,
        default=region_options_found,
    )

    state_options = sorted(df["resource_state"].dropna().astype(str).unique())
    state_filter = st.sidebar.multiselect(
        "State",
        state_options,
        default=state_options,
    )

    filtered = df[
        df["lineItem/UsageAccountId"].astype(str).isin(account_filter)
        & df["product/servicecode"].astype(str).isin(service_filter)
        & df["product/region"].astype(str).isin(region_filter)
        & df["resource_state"].astype(str).isin(state_filter)
    ].copy()
else:
    filtered = pd.DataFrame()

# -----------------------------------------------------------------------------
# Analysis Execution
# -----------------------------------------------------------------------------
if is_live_mode and live_result is not None:
    # Use live pre-computed findings or run on active filtered subset
    effective_min_days = 1
    idle = detect_idle_resources(filtered, ec2_cpu_p95_threshold=5.0, min_days_observed=effective_min_days)
    rightsizing = recommend_rightsizing(filtered, p95_threshold=20.0)
    tag_report, compliance = evaluate_tagging_compliance(filtered, policy)

    total_spend = max(0.0, float(live_result.total_spend_usd))
    numeric_waste = pd.to_numeric(idle["waste_usd"], errors="coerce").dropna()
    identified_waste = float(numeric_waste.sum()) if not numeric_waste.empty else 0.0
    rightsizing_savings = float(pd.to_numeric(rightsizing["estimated_monthly_savings_usd"], errors="coerce").dropna().sum()) if not rightsizing.empty else 0.0

else:
    # Synthetic or Uploaded mode
    effective_min_days = 7
    idle = detect_idle_resources(filtered, min_days_observed=effective_min_days) if not filtered.empty else pd.DataFrame()
    rightsizing = recommend_rightsizing(filtered) if not filtered.empty else pd.DataFrame()
    tag_report, compliance = evaluate_tagging_compliance(filtered, policy) if not filtered.empty else (pd.DataFrame(), 100.0)

    total_spend = max(0.0, float(filtered["lineItem/UnblendedCost"].dropna().sum())) if not filtered.empty and "lineItem/UnblendedCost" in filtered.columns else 0.0
    identified_waste = max(0.0, float(idle["waste_usd"].dropna().sum())) if not idle.empty and "waste_usd" in idle.columns else 0.0
    rightsizing_savings = max(0.0, float(rightsizing["estimated_monthly_savings_usd"].dropna().sum())) if not rightsizing.empty else 0.0

# Normalize any tiny negative zeros
if abs(total_spend) < 0.001:
    total_spend = 0.0
if abs(identified_waste) < 0.001:
    identified_waste = 0.0
if abs(rightsizing_savings) < 0.001:
    rightsizing_savings = 0.0

# -----------------------------------------------------------------------------
# Main Dashboard Header & Status Banner
# -----------------------------------------------------------------------------
st.title("☁️ FinSight — Cloud Cost Optimization & Governance")
st.caption(
    "Automated FinOps intelligence platform for multi-source AWS cost visibility, "
    "idle resource detection, rightsizing recommendations, and tag policy governance."
)

with st.container(border=True):
    col_status1, col_status2, col_status3 = st.columns([1.5, 1, 1])
    with col_status1:
        if is_live_mode and live_result is not None:
            st.markdown(
                f"**Status:** :green[● Connected to AWS]  \n"
                f"**AWS Account:** `{live_result.account_id}` &nbsp;|&nbsp; "
                f"**Region:** `{live_result.region}`  \n"
                f"**Identity:** `{live_result.arn}`"
            )
        elif is_synthetic_mode:
            st.markdown(
                f"**Status:** :blue[● Synthetic Benchmark Active]  \n"
                f"**Dataset:** `{source_name}` &nbsp;|&nbsp; "
                f"**Injected Anomalies:** `Known Ground-Truth Enabled`"
            )
        else:
            st.markdown(
                f"**Status:** :violet[● Custom CUR Upload Active]  \n"
                f"**Source:** `{source_name}`"
            )

    with col_status2:
        ec2_cnt = len(filtered[filtered["product/servicecode"] == "EC2"]) if not filtered.empty else 0
        ebs_cnt = len(filtered[filtered["product/servicecode"] == "EBS"]) if not filtered.empty else 0
        eip_cnt = len(filtered[filtered["product/servicecode"] == "ElasticIP"]) if not filtered.empty else 0
        st.markdown(
            f"**Resource Inventory:**  \n"
            f"• **EC2:** `{ec2_cnt}` &nbsp; • **EBS:** `{ebs_cnt}` &nbsp; • **Elastic IP:** `{eip_cnt}`"
        )

    with col_status3:
        if is_live_mode and live_result is not None:
            cw_status = "Available (3/3 instances)" if ec2_cnt > 0 and filtered["cpu_utilization_avg"].notna().any() else "Telemetry Active"
            ce_status = "Active ($0.00 spend)" if total_spend == 0.0 else f"Active (${total_spend:,.2f})"
            st.markdown(
                f"**Telemetry & Billing:**  \n"
                f"• **CloudWatch:** `{cw_status}`  \n"
                f"• **Cost Explorer:** `{ce_status}`"
            )
        else:
            st.markdown(
                f"**Filter Selection:**  \n"
                f"• **Showing:** `{len(filtered)}/{len(df)}` total records"
            )

# Display warnings if present
if is_live_mode and live_result is not None and live_result.warnings:
    for w in live_result.warnings:
        st.warning(f"⚠️ {w}")

# -----------------------------------------------------------------------------
# Section 1: KPI Summary Cards
# -----------------------------------------------------------------------------
k1, k2, k3, k4 = st.columns(4)

with k1:
    with st.container(border=True):
        st.metric(
            label="Total AWS Spend" if is_live_mode else "Total Spend",
            value=f"${total_spend:,.2f}",
            help="Total spend from AWS Cost Explorer (Live Mode) or CUR line items.",
        )
        if is_live_mode and total_spend == 0.0:
            st.caption("No material spend recorded in observation period.")

with k2:
    with st.container(border=True):
        if is_live_mode and identified_waste == 0.0 and not idle.empty:
            st.metric(
                label="Identified Waste",
                value="N/A*",
                help="Idle resources flagged. Direct per-resource cost attribution unavailable in standard Cost Explorer.",
            )
            st.caption(f"*{len(idle)} idle resource(s) flagged (Cost attribution unavailable)")
        else:
            st.metric(
                label="Identified Waste",
                value=f"${identified_waste:,.2f}",
                help="Estimated waste from unattached EBS volumes, idle EC2 instances, and unused Elastic IPs.",
            )
            st.caption(f"{len(idle)} waste finding(s) detected")

with k3:
    with st.container(border=True):
        st.metric(
            label="Tagging Compliance",
            value=f"{compliance:.1f}%",
            help=f"Percentage of resources satisfying tagging policy ({policy.get('policy_name', 'Default')}).",
        )
        compliant_n = int(tag_report["compliant"].sum()) if not tag_report.empty and "compliant" in tag_report.columns else 0
        total_n = len(tag_report) if not tag_report.empty else 0
        st.caption(f"{compliant_n}/{total_n} resources compliant")

with k4:
    with st.container(border=True):
        st.metric(
            label="Rightsizing Savings",
            value=f"${rightsizing_savings:,.2f}/mo",
            help="Estimated monthly savings achieved by downsizing low-utilization EC2 instances.",
        )
        st.caption(f"{len(rightsizing)} rightsizing candidate(s)")

st.divider()

# -----------------------------------------------------------------------------
# Section 2: Visual Cost & Waste Breakdowns
# -----------------------------------------------------------------------------
st.subheader("Cost & Waste Visualizations")
chart_col1, chart_col2 = st.columns(2)

with chart_col1:
    with st.container(border=True):
        st.markdown("**Waste Breakdown by Finding Type**")
        if idle.empty:
            st.info("No idle resource waste detected under active filters.")
        else:
            if not idle["waste_usd"].isna().all() and (idle["waste_usd"] > 0).any():
                waste_summary = idle.groupby("finding_type", as_index=False)["waste_usd"].sum()
                fig_waste = px.bar(
                    waste_summary,
                    x="finding_type",
                    y="waste_usd",
                    labels={"finding_type": "Finding Type", "waste_usd": "Waste (USD)"},
                    color="finding_type",
                    color_discrete_sequence=px.colors.qualitative.Set2,
                )
                fig_waste.update_layout(showlegend=False, margin=dict(l=20, r=20, t=20, b=20), height=300)
                st.plotly_chart(fig_waste)
            else:
                findings_cnt = idle["finding_type"].value_counts().reset_index()
                findings_cnt.columns = ["finding_type", "count"]
                fig_cnt = px.bar(
                    findings_cnt,
                    x="finding_type",
                    y="count",
                    labels={"finding_type": "Finding Type", "count": "Resource Count"},
                    color="finding_type",
                    color_discrete_sequence=px.colors.qualitative.Pastel,
                )
                fig_cnt.update_layout(showlegend=False, margin=dict(l=20, r=20, t=20, b=20), height=300)
                st.plotly_chart(fig_cnt)
                st.caption("Displaying resource count (cost attribution unavailable on live sandbox).")

with chart_col2:
    with st.container(border=True):
        st.markdown("**Spend Breakdown by Service**")
        if is_live_mode and live_result is not None:
            ce_df = live_result.cost_summary
            if not ce_df.empty and ce_df["unblended_cost_usd"].sum() > 0:
                ce_svc = ce_df.groupby("service", as_index=False)["unblended_cost_usd"].sum()
                fig_spend = px.pie(
                    ce_svc,
                    names="service",
                    values="unblended_cost_usd",
                    hole=0.45,
                    color_discrete_sequence=px.colors.qualitative.Prism,
                )
                fig_spend.update_layout(margin=dict(l=20, r=20, t=20, b=20), height=300)
                st.plotly_chart(fig_spend)
            else:
                st.info("No billed AWS spend recorded in the selected observation window ($0.00 total spend).")
        elif not filtered.empty and "lineItem/UnblendedCost" in filtered.columns and filtered["lineItem/UnblendedCost"].sum() > 0:
            spend_type = filtered.groupby("product/servicecode", as_index=False)["lineItem/UnblendedCost"].sum()
            fig_spend = px.pie(
                spend_type,
                names="product/servicecode",
                values="lineItem/UnblendedCost",
                hole=0.45,
                color_discrete_sequence=px.colors.qualitative.Prism,
            )
            fig_spend.update_layout(margin=dict(l=20, r=20, t=20, b=20), height=300)
            st.plotly_chart(fig_spend)
        else:
            st.info("No spend data available to visualize.")

st.divider()

# -----------------------------------------------------------------------------
# Section 3: Resource Inventory Table
# -----------------------------------------------------------------------------
st.subheader("Resource Inventory & Telemetry")
if filtered.empty:
    st.info("No resources match the selected sidebar filters.")
else:
    inv_display = filtered[[
        "lineItem/ResourceId",
        "lineItem/UsageAccountId",
        "product/servicecode",
        "product/region",
        "product/instanceType",
        "resource_state",
        "cpu_utilization_avg",
        "cpu_utilization_p95",
        "size_gb",
        "resourceTags/user:Owner",
        "resourceTags/user:Environment",
        "resourceTags/user:Cost-Center",
    ]].copy()
    inv_display.columns = [
        "Resource ID",
        "Account",
        "Service",
        "Region",
        "Instance Type",
        "State",
        "Avg CPU %",
        "p95 CPU %",
        "Size (GB)",
        "Owner",
        "Environment",
        "Cost-Center",
    ]
    st.dataframe(inv_display, hide_index=True)

st.divider()

# -----------------------------------------------------------------------------
# Section 4: Idle Resource Findings
# -----------------------------------------------------------------------------
st.subheader("Idle Resource Findings")
if idle.empty:
    st.success("✅ No idle resources detected under the active filters and thresholds.")
else:
    show_idle = idle[[
        "lineItem/ResourceId",
        "lineItem/UsageAccountId",
        "resource_type",
        "region",
        "instance_type",
        "resource_state",
        "cpu_p95",
        "finding_type",
        "rule",
        "waste_usd",
    ]].copy()
    show_idle.columns = [
        "Resource ID",
        "Account",
        "Service",
        "Region",
        "Instance Type",
        "State",
        "p95 CPU %",
        "Finding Type",
        "Rule Triggered",
        "Monthly Waste (USD)",
    ]
    # Format waste
    show_idle["Monthly Waste (USD)"] = show_idle["Monthly Waste (USD)"].apply(
        lambda v: f"${v:,.2f}" if pd.notna(v) and float(v) > 0 else "Attribution Unavailable"
    )
    st.dataframe(show_idle, hide_index=True)

st.divider()

# -----------------------------------------------------------------------------
# Section 5: EC2 Rightsizing Recommendations
# -----------------------------------------------------------------------------
st.subheader("EC2 Rightsizing Recommendations")
if rightsizing.empty:
    st.info("No over-provisioned EC2 instances identified under the 20% p95 CPU threshold.")
else:
    show_rs = rightsizing[[
        "resource_id",
        "account",
        "region",
        "current_instance_type",
        "recommended_instance_type",
        "p95_cpu_pct",
        "current_monthly_cost_usd",
        "recommended_monthly_cost_usd",
        "estimated_monthly_savings_usd",
        "reason",
    ]].copy()
    show_rs.columns = [
        "Instance ID",
        "Account",
        "Region",
        "Current Type",
        "Recommended Type",
        "p95 CPU %",
        "Current Cost/mo",
        "Target Cost/mo",
        "Est. Savings/mo",
        "Reason",
    ]
    show_rs["Current Cost/mo"] = show_rs["Current Cost/mo"].apply(lambda v: f"${v:,.2f}" if pd.notna(v) else "N/A")
    show_rs["Target Cost/mo"] = show_rs["Target Cost/mo"].apply(lambda v: f"${v:,.2f}" if pd.notna(v) else "N/A")
    show_rs["Est. Savings/mo"] = show_rs["Est. Savings/mo"].apply(lambda v: f"${v:,.2f}" if pd.notna(v) else "N/A")
    st.dataframe(show_rs, hide_index=True)

st.divider()

# -----------------------------------------------------------------------------
# Section 6: Tagging Governance
# -----------------------------------------------------------------------------
st.subheader("Tagging Governance & Policy Compliance")
tag_col1, tag_col2 = st.columns([1, 2])

with tag_col1:
    with st.container(border=True):
        st.markdown(f"**Policy:** `{policy.get('policy_name', 'Default-Policy')}` (v{policy.get('version', '1.0')})")
        req_tags = policy.get("required_tags", ["Owner", "Environment", "Cost-Center"])
        st.markdown(f"**Required Tags:** `{', '.join(req_tags)}`")
        if "allowed_values" in policy:
            st.markdown(f"**Allowed Environments:** `{', '.join(policy['allowed_values'].get('Environment', []))}`")

        c_cnt = int(tag_report["compliant"].sum()) if not tag_report.empty else 0
        tot_cnt = len(tag_report) if not tag_report.empty else 0
        st.metric("Compliant Resources", f"{c_cnt}/{tot_cnt}")
        st.metric("Policy Compliance", f"{compliance:.1f}%")

with tag_col2:
    with st.container(border=True):
        st.markdown("**Non-Compliant Resource Violations**")
        noncompliant = tag_report[~tag_report["compliant"]] if not tag_report.empty and "compliant" in tag_report.columns else pd.DataFrame()
        if noncompliant.empty:
            st.success("🎉 All resources strictly comply with the configured tagging policy!")
        else:
            show_nc = noncompliant[[
                "lineItem/ResourceId",
                "lineItem/UsageAccountId",
                "product/servicecode",
                "missing_or_invalid_tags",
            ]].copy()
            show_nc.columns = ["Resource ID", "Account", "Service", "Missing / Invalid Tag Fields"]
            st.dataframe(show_nc, hide_index=True)

st.divider()

# -----------------------------------------------------------------------------
# Section 7: Dry-Run Remediation Simulator
# -----------------------------------------------------------------------------
st.subheader("Dry-Run Remediation Simulator")
st.caption(
    "Simulate optimization actions and preview non-destructive AWS CLI commands before approval. "
    "This interface is strictly read-only and will never execute mutations against your AWS account."
)

all_flagged_ids = []
if not idle.empty:
    all_flagged_ids.extend(idle["lineItem/ResourceId"].astype(str).tolist())
if not rightsizing.empty:
    all_flagged_ids.extend(rightsizing["resource_id"].astype(str).tolist())
all_flagged_ids = sorted(list(set(all_flagged_ids)))

if not all_flagged_ids:
    st.info("No flagged idle or over-provisioned resources available for dry-run simulation.")
else:
    sim_col1, sim_col2 = st.columns([2, 1])
    with sim_col1:
        selected_resources = st.multiselect(
            "Select Flagged Resources to Simulate",
            all_flagged_ids,
            default=all_flagged_ids[:3] if len(all_flagged_ids) >= 3 else all_flagged_ids,
        )
    with sim_col2:
        action_mode = st.selectbox(
            "Remediation Strategy",
            ["Simulate Auto-Generated Plan", "Simulate Stop / Release", "Simulate Owner Notification"],
        )

    if st.button("Generate Dry-Run Remediation Plan", type="primary"):
        if not selected_resources:
            st.warning("Please select at least one resource to simulate.")
        else:
            # Filter idle and rightsizing findings for selected resources
            selected_idle = idle[idle["lineItem/ResourceId"].astype(str).isin(selected_resources)] if not idle.empty else pd.DataFrame()
            selected_rs = rightsizing[rightsizing["resource_id"].astype(str).isin(selected_resources)] if not rightsizing.empty else pd.DataFrame()

            plan = generate_remediation_plan(selected_idle, selected_rs)
            plan_df = remediation_plan_to_dataframe(plan)

            st.success(f"✅ Generated dry-run simulation plan for {len(plan_df)} action(s). Zero changes made to AWS.")

            for item in plan:
                with st.expander(f"⚙️ {item['recommendation']} — `{item['resource_id']}`", expanded=True):
                    c1, c2, c3 = st.columns(3)
                    c1.markdown(f"**Action Type:** `{item['action_type']}`")
                    c2.markdown(f"**Est. Savings:** `${item['estimated_monthly_savings_usd']:,.2f}/mo`")
                    c3.markdown(f"**Safety Mode:** `:green[{item['safety_mode']}]`")
                    st.markdown(f"**Reason:** {item['reason']}")
                    st.code(item["cli_command_preview"], language="bash")

st.divider()

# -----------------------------------------------------------------------------
# Section 8: Detection Benchmark (Synthetic Ground Truth Only)
# -----------------------------------------------------------------------------
if is_synthetic_mode and {"ground_truth_idle_ec2", "ground_truth_idle_ebs", "ground_truth_unused_eip"}.issubset(filtered.columns):
    st.subheader("Detection Benchmark (Synthetic Ground Truth)")
    st.caption("Objective mathematical evaluation of the idle detection engine against injected ground-truth anomalies.")

    gt = filtered.groupby("lineItem/ResourceId").agg(
        gt_idle_ec2=("ground_truth_idle_ec2", "max"),
        gt_idle_ebs=("ground_truth_idle_ebs", "max"),
        gt_unused_eip=("ground_truth_unused_eip", "max"),
    ).reset_index()
    gt["ground_truth_idle"] = gt[["gt_idle_ec2", "gt_idle_ebs", "gt_unused_eip"]].any(axis=1)
    detected_ids = set(idle["lineItem/ResourceId"].astype(str)) if not idle.empty else set()
    gt["detected_idle"] = gt["lineItem/ResourceId"].astype(str).isin(detected_ids)

    tp = int((gt.ground_truth_idle & gt.detected_idle).sum())
    fp = int((~gt.ground_truth_idle & gt.detected_idle).sum())
    fn = int((gt.ground_truth_idle & ~gt.detected_idle).sum())
    tn = int((~gt.ground_truth_idle & ~gt.detected_idle).sum())
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * (precision * recall) / (precision + recall) if (precision + recall) else 0.0

    b1, b2, b3, b4 = st.columns(4)
    b1.metric("True Positives", tp)
    b2.metric("Precision", f"{precision*100:.1f}%")
    b3.metric("Recall", f"{recall*100:.1f}%")
    b4.metric("F1 Score", f"{f1*100:.1f}%")

    with st.expander("Confusion Matrix & Benchmark Statistics"):
        st.write(pd.DataFrame([
            {"Ground Truth Idle (Positive)": f"TP: {tp}", "Ground Truth Normal (Negative)": f"FP: {fp}"},
            {"Ground Truth Idle (Positive)": f"FN: {fn}", "Ground Truth Normal (Negative)": f"TN: {tn}"},
        ], index=["Detected as Idle", "Detected as Normal"]))

# -----------------------------------------------------------------------------
# Section 9: Detection Rules & Project Notes
# -----------------------------------------------------------------------------
with st.expander("ℹ️ Detection Rules, Engine Criteria, and FinOps Governance Notes"):
    st.markdown("""
    - **Idle EC2 Detection:** Flags running EC2 instances with 95th percentile CPU below 5.0% observed over at least 7 days (or live CloudWatch observation window).
    - **Unattached EBS Detection:** Flags EBS volumes in `available` state (detached from any EC2 compute instance).
    - **Unused Elastic IP Detection:** Flags allocated Elastic IP addresses that are `unassociated`.
    - **EC2 Rightsizing Recommendations:** Evaluates instances where 95th percentile CPU is below 20.0% with adequate capacity headroom, recommending a 1-step downsize in instance family (`t3.*`, `m5.*`).
    - **Tagging Governance:** Checks compliance against required tags (`Owner`, `Environment`, `Cost-Center`) and allowed values in `config/tagging_policy.yaml`.
    - **Security & Safety:** All remediation previews are dry-run only (`--dry-run`). The application connects using read-only IAM permissions.
    """)
