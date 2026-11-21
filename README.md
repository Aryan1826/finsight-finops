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

## Connecting an AWS Account

FinSight supports three authentication methods for live AWS account telemetry:

### 1. AWS Access Key Credentials (Direct Entry)
Allows users to connect their AWS accounts directly by entering credentials into the web UI without configuring local AWS CLI profiles:
- **AWS Access Key ID**: 20-character identifier (e.g. `AKIA...` or `ASIA...`)
- **AWS Secret Access Key**: Masked password field
- **AWS Session Token (Optional)**: Masked field for temporary credentials generated via STS or AWS IAM Identity Center
- **AWS Region**: Selectable region (defaults to `ap-south-1`)

### 2. Cross-Account IAM Role (1-Click CloudFormation)
Connects securely via STS AssumeRole using an automated CloudFormation onboarding template with a cryptographically secure External ID.

### 3. Local AWS Profile
Connects via local AWS CLI profile configured in `~/.aws/credentials` or the default AWS SDK credential chain.

## Security Architecture & Non-Persistence

- **Strictly Read-Only:** FinSight only executes read-only AWS APIs (`Describe*`, `Get*`, `List*`). It contains zero destructive operations (no instance termination, volume deletion, or resource modification).
- **Zero Credential Persistence:** FinSight never saves AWS credentials to disk, files, databases, `.env`, JSON, YAML, or any persistent storage. Credentials and sessions are never cached across users or persisted with Streamlit cache decorators.
- **Immediate Disconnect:** Clicking **Disconnect AWS Account** completely purges connection state, caller identity, and telemetry from application memory.
- **Hosted Application Security Notice:** When FinSight is hosted in a shared environment (such as AWS ECS) and accessed over the web, credentials entered into the browser are transmitted to the application server and processed in server memory for the duration of the session. Streamlit processes session state on the server, and memory is not securely zeroized.
- **Least-Privilege Recommendation:** For hosted deployments, users are strongly advised to provide **temporary credentials** (with a session token) attached to a read-only policy (such as `SecurityAudit` or `ReadOnlyAccess`) rather than long-lived root or administrator access keys.
- **Confused Deputy Protection:** Mandatory unique `ExternalId` condition enforced in all cross-account trust policies.
- **Dry-Run Remediation:** All optimization actions are presented as non-destructive dry-run simulation previews (`--dry-run`).
