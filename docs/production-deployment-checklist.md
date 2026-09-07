# FinSight Production Deployment Readiness Checklist (Amazon ECS Express Mode)

This checklist tracks the architecture preparation and future deployment tasks for **FinSight** on **Amazon ECS Express Mode with AWS Fargate**.

---

## 1. Architecture & Design Readiness (Phase 7A)

- [x] **Production Hosting Architecture Selected:** Amazon ECS Express Mode + AWS Fargate selected as the modern, managed container hosting platform.
- [x] **Three IAM Roles Designed:**
  - `ecsTaskExecutionRole` (ECR pull, CloudWatch logs)
  - `FinSightProductionTaskRole` (`sts:AssumeRole` into customer accounts)
  - `ecsInfrastructureRoleForExpressServices` (Express Mode infrastructure provisioning)
- [x] **Customer Role & Trust Policy Documented:** Documented with `<FINSIGHT_PRODUCTION_TASK_ROLE_ARN>` and `<FINSIGHT_EXTERNAL_ID>` placeholders.
- [x] **External ID Strategy Documented:** Confused deputy protection designed; in-memory transmission without persistent secret logging.
- [x] **Container Port Defined:** Port `8501` (TCP) designated for Streamlit and WebSockets traffic.
- [x] **Health Check Endpoint Configured:** Streamlit `/_stcore/health` endpoint configured for container and service health checks.
- [x] **Dockerfile & .dockerignore Verified:** Python 3.11-slim, non-root user (`appuser`), port 8501 exposed, secrets and local credentials excluded.
- [x] **Zero Hardcoded Secrets & Accounts:** Verified that no AWS access keys or hardcoded development accounts exist in runtime code.
- [x] **Strict Read-Only Enforcement:** Verified that destructive AWS APIs are completely absent.
- [x] **Multi-Tenant Isolation Tested:** 43 automated tests passing in `pytest`.

---

## 2. Production AWS Infrastructure Deployment Tasks (Phase 7B — Pending)

> [!NOTE]
> The following items represent cloud provisioning steps to be executed during Phase 7B upon user authorization.

- [ ] **ECR Repository:** Provision private repository `finsight-finops` in target hosting region.
- [ ] **ECR Container Image:** Build, tag, and push production Docker image to ECR.
- [ ] **ECS Task Execution Role:** Create `ecsTaskExecutionRole` with `AmazonECSTaskExecutionRolePolicy`.
- [ ] **FinSight Application Task Role:** Create `FinSightProductionTaskRole` with `sts:AssumeRole` policy on `arn:aws:iam::*:role/FinSightReadOnlyRole`.
- [ ] **ECS Express Mode Infrastructure Role:** Ensure `ecsInfrastructureRoleForExpressServices` is configured.
- [ ] **ECS Express Mode Service:** Provision Express Mode service on Fargate (1 vCPU, 2 GB RAM, port 8501).
- [ ] **HTTPS Endpoint Verification:** Confirm public HTTPS URL is active and healthy.
- [ ] **Streamlit Web Application Verification:** Verify full web dashboard functionality.
- [ ] **Customer Cross-Account AssumeRole Verification:** Validate real cross-account connection via AWS STS.
- [ ] **Post-Deployment Security Scan:** Verify zero exposed credentials or destructive capabilities in production.
