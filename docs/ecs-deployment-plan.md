# Amazon ECS Express Mode Deployment Plan

> [!IMPORTANT]
> **Planning & Preparation Only:**
> This document details the step-by-step procedure for deploying FinSight on Amazon ECS Express Mode with AWS Fargate. **Do not execute these provisioning commands until Phase 7B.**

---

## Deployment Step-by-Step Sequence

```text
Step 1: ECR Repo ──► Step 2-4: IAM Roles ──► Step 5-7: Build & Push Container
                                                                 │
                                                                 ▼
Step 11: Verify Cross-Account ◄── Step 10: Test App ◄── Step 8-9: Create ECS Service
```

---

### Step 1 — Create Amazon ECR Repository
Create a private container repository named `finsight-finops` in the hosting AWS region (e.g. `ap-south-1`):
```bash
aws ecr create-repository \
  --repository-name finsight-finops \
  --region ap-south-1 \
  --image-scanning-configuration scanOnPush=true
```

---

### Step 2 — Create ECS Task Execution Role
Create `ecsTaskExecutionRole` and attach the AWS-managed policy allowing ECR image pulls and CloudWatch container logging:
```bash
# Trust policy for ecs-tasks.amazonaws.com
aws iam create-role \
  --role-name ecsTaskExecutionRole \
  --assume-role-policy-document file://config/ecs-tasks-trust-policy.json

# Attach managed execution policy
aws iam attach-role-policy \
  --role-name ecsTaskExecutionRole \
  --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy
```

---

### Step 3 — Create FinSight Application Task Role
Create `FinSightProductionTaskRole` with permission to assume customer `FinSightReadOnlyRole` roles:
```bash
# Trust policy for ecs-tasks.amazonaws.com
aws iam create-role \
  --role-name FinSightProductionTaskRole \
  --assume-role-policy-document file://config/ecs-tasks-trust-policy.json

# Attach custom least-privilege AssumeRole policy
aws iam put-role-policy \
  --role-name FinSightProductionTaskRole \
  --policy-name FinSightAssumeCustomerRolePolicy \
  --policy-document file://config/finsight-task-policy.json
```

---

### Step 4 — Create ECS Express Mode Infrastructure Role
Ensure the service-linked role or infrastructure role for ECS Express Mode is created:
```bash
aws iam create-role \
  --role-name ecsInfrastructureRoleForExpressServices \
  --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ecs.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
```

---

### Step 5 — Build Production Docker Image Locally
```bash
docker build -t finsight-finops:latest .
```

---

### Step 6 — Tag Docker Image for Amazon ECR
```bash
docker tag finsight-finops:latest <ACCOUNT_ID>.dkr.ecr.ap-south-1.amazonaws.com/finsight-finops:latest
```

---

### Step 7 — Authenticate and Push Image to ECR
```bash
aws ecr get-login-password --region ap-south-1 | \
  docker login --username AWS --password-stdin <ACCOUNT_ID>.dkr.ecr.ap-south-1.amazonaws.com

docker push <ACCOUNT_ID>.dkr.ecr.ap-south-1.amazonaws.com/finsight-finops:latest
```

---

### Step 8 — Create ECS Express Mode Service
Provision the ECS Express Mode service running on AWS Fargate with the following specifications:
- **Compute:** AWS Fargate
- **vCPU:** `1 vCPU` (1024 CPU units)
- **Memory:** `2 GB` (2048 MB)
- **Container Port:** `8501` (TCP)
- **Desired Tasks:** `1` (minimum)
- **Execution Role:** `ecsTaskExecutionRole`
- **Task Role:** `FinSightProductionTaskRole`
- **Health Check Path:** `/_stcore/health`
- **Network Ingress:** Internet-facing (HTTPS enabled)

---

### Step 9 — Verify HTTPS Endpoint & Routing
Retrieve the auto-generated public HTTPS URL provided by ECS Express Mode:
```bash
# Check service status and fetch active public HTTPS endpoint URL
aws ecs describe-services --cluster <CLUSTER_NAME> --services finsight-finops-service
```

---

### Step 10 — Test Streamlit Web Interface
1. Open the public HTTPS URL in a web browser.
2. Verify all UI elements, charts, and data source switchers (Synthetic CUR, Uploaded CUR, Live AWS Account) load properly.
3. Test the built-in health endpoint:
   ```bash
   curl -I https://<ECS_EXPRESS_URL>/_stcore/health
   ```
   *Expected output: HTTP 200 OK.*

---

### Step 11 — Test Customer Cross-Account AssumeRole Connection
1. In the live web dashboard, select **Live AWS Account** $\to$ **Cross-Account IAM Role**.
2. Enter a test customer `Role ARN`, `External ID`, and `Region`.
3. Click **Connect & Fetch Data**.
4. Confirm the banner displays the verified customer account ID and assumed role identity from AWS STS `GetCallerIdentity`.
