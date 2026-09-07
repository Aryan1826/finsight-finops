# FinSight — Cloud Cost Optimization & Governance (FinOps)

FinSight is a cloud cost optimization and governance intelligence platform. It provides automated idle resource detection, historical CloudWatch-based rightsizing recommendations, YAML-driven tagging governance, and safe dry-run remediation simulation across multiple ingestion modes.

## Ingestion Modes Supported

1. **Live AWS Account (`boto3`):**
   - Read-only inventory discovery across EC2, EBS, and Elastic IP
   - 14-day CloudWatch CPU metric telemetry (`get_metric_data` with batched queries)
   - AWS Cost Explorer spend tracking (`get_cost_and_usage`)
2. **Synthetic AWS CUR Benchmark:**
   - Deterministic CUR dataset with injected ground-truth anomalies in CSV and Parquet formats
   - Objective benchmark metrics (Precision, Recall, F1 score)
3. **User-Uploaded CUR Data:**
   - Supports custom AWS CUR billing exports in `.csv` or `.parquet` format

## Project Structure

```text
finsight_finops/
├── app.py                                   # Streamlit multi-source FinOps dashboard
├── requirements.txt                         # Project dependencies
├── README.md                                # Documentation
├── templates/
│   └── finsight-readonly-role.template.yaml # 1-Click CloudFormation onboarding template
├── config/
│   └── tagging_policy.yaml                  # Tagging governance rules & allowed values
├── data/
│   ├── synthetic_cur.csv                    # Synthetic CUR dataset (CSV)
│   └── synthetic_cur.parquet                # Synthetic CUR dataset (Parquet)
├── utils/
│   ├── __init__.py
│   ├── aws_client.py                        # Read-only AWS session, STS validation & CF URL builder
│   ├── aws_live_collector.py                # Live EC2, EBS, EIP, CloudWatch, & Cost Explorer ingestion
│   ├── normalizer.py                        # Common schema normalizer & alias resolver
│   ├── finops_pipeline.py                   # End-to-end backend pipeline orchestrator
│   ├── idle_detector.py                     # Rule-based idle resource detection engine
│   ├── rightsizing.py                       # Historical CPU rightsizing engine
│   ├── tagging_policy.py                    # Tag policy compliance engine
│   ├── synthetic_generator.py               # Synthetic CUR generator (CSV & Parquet)
│   └── remediation_simulator.py             # Safe dry-run remediation plan & CLI generator
├── scripts/
│   └── test_live_pipeline.py                # Standalone CLI test tool for live AWS verification
└── tests/
    ├── test_app_ui.py                       # UI import and definition tests
    ├── test_aws_collector_mock.py           # Mocked AWS collector & safety tests
    ├── test_cloudformation_onboarding.py    # CloudFormation 1-click onboarding & template tests
    ├── test_finops_pipeline.py              # Pipeline integration tests
    ├── test_multi_tenant_auth.py            # Multi-tenant STS authentication & isolation tests
    ├── test_normalizer.py                   # Schema normalization & Parquet tests
    └── test_engines.py                      # Core analysis engine baseline tests
```

## Setup & Running

### 1. Environment Setup

```bash
# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate  # macOS/Linux

# Install dependencies
pip install -r requirements.txt
```

### 2. Test Live AWS Pipeline via CLI (Read-Only)

```bash
python scripts/test_live_pipeline.py --profile finsight --region ap-south-1 --days 14
```

### 3. Run Automated Tests

```bash
pytest tests/ -v
```

### 4. Launch the Streamlit Dashboard

```bash
streamlit run app.py
```

The application will start on `http://localhost:8501`.

## Connect an AWS Account (1-Click CloudFormation)

FinSight provides a **1-Click AWS CloudFormation Onboarding** workflow to connect any customer AWS account securely:

1. **Open FinSight:** Under **Data Source**, select **Live AWS Account**, then choose **Cross-Account IAM Role**.
2. **Review External ID:** FinSight automatically generates a cryptographically secure random External ID (e.g., `finsight-ext-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`).
3. **Launch CloudFormation:** Click **🚀 Launch CloudFormation Setup**. This opens the AWS CloudFormation Quick-Create console with pre-filled parameters.
4. **Create Stack:** Acknowledge IAM resource creation and click **Create stack**.
5. **Copy Role ARN:** Once the stack is `CREATE_COMPLETE`, copy the `RoleArn` from the CloudFormation **Outputs** tab:
   ```text
   arn:aws:iam::<CUSTOMER_ACCOUNT_ID>:role/FinSightReadOnlyRole
   ```
6. **Connect & Fetch Data:** Paste the copied Role ARN into FinSight and click **Connect & Fetch Data**. FinSight assumes the role via `sts:AssumeRole` with the matching External ID and loads your read-only FinOps intelligence dashboard.

*(Manual IAM setup is also supported by selecting "Already have a configured FinSight role?" in the sidebar).*

For the full security architecture and manual fallback instructions, see:
- 📖 [Customer AWS Setup Guide](docs/customer-aws-setup.md)
- 📋 [Customer AWS Onboarding Checklist](docs/customer-aws-checklist.md)

## Security & Safety

- **Strictly Read-Only:** FinSight only calls `Describe*`, `Get*`, and `List*` AWS APIs. It contains zero mutation or destructive operations.
- **Zero Permanent Key Storage:** No long-lived `AWS_ACCESS_KEY_ID` or `AWS_SECRET_ACCESS_KEY` are accepted or stored. All cross-account access uses short-lived in-memory STS session tokens.
- **Confused Deputy Protection:** Mandatory unique `ExternalId` condition enforced in all cross-account trust policies.
- **Dry-Run Remediation:** All optimization actions are presented as non-destructive dry-run simulation previews.
