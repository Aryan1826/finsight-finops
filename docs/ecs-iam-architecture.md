# Amazon ECS IAM Architecture & Role Separation

This document specifies the IAM architecture and strict role separation for **FinSight** running on **Amazon ECS Express Mode with AWS Fargate**.

---

## 1. Overview of the Three ECS IAM Roles

In an ECS Express Mode / Fargate deployment, there are three distinct IAM roles. Each serves a specific purpose, preventing privilege escalation and enforcing least-privilege boundary separation.

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                       Hosting Account IAM Roles                             │
│                                                                             │
│  1. ECS Task Execution Role              2. FinSight Application Task Role  │
│     (ecsTaskExecutionRole)                  (FinSightProductionTaskRole)    │
│     ┌────────────────────────────┐          ┌───────────────────────────┐   │
│     │ • Pull ECR container image │          │ • boto3 application identity│   │
│     │ • Write CloudWatch logs    │          │ • Invokes sts:AssumeRole  │   │
│     └────────────────────────────┘          └─────────────┬─────────────┘   │
│                                                           │                 │
│  3. ECS Infrastructure Role                               │                 │
│     (ecsInfrastructureRoleForExpressServices)             │ sts:AssumeRole  │
│     ┌────────────────────────────┐                        │                 │
│     │ • Manage App Gateway &     │                        ▼                 │
│     │   infrastructure routing   │          ┌───────────────────────────┐   │
│     └────────────────────────────┘          │ Customer AWS Account      │   │
│                                             │ (FinSightReadOnlyRole)    │   │
│                                             └───────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Role 1: ECS Task Execution Role

- **Recommended Role Name:** `ecsTaskExecutionRole`
- **Assumed by (Trust Policy):**
  ```json
  {
    "Version": "2012-10-17",
    "Statement": [
      {
        "Effect": "Allow",
        "Principal": {
          "Service": "ecs-tasks.amazonaws.com"
        },
        "Action": "sts:AssumeRole"
      }
    ]
  }
  ```
- **Attached AWS Managed Policy:** `arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy`
- **Purpose & Scope:**
  - Used strictly by the **AWS ECS / Fargate container agent**.
  - Authorizes the infrastructure to authenticate to Amazon ECR and pull the `finsight-finops` container image.
  - Authorizes writing container standard output (`stdout`/`stderr`) to AWS CloudWatch Logs (`/ecs/finsight-finops`).
- **Security Boundary:** This role is **never accessible to application code** and cannot be used to make customer AWS API calls.

---

## 3. Role 2: FinSight Application Task Role

- **Recommended Role Name:** `FinSightProductionTaskRole`
- **Assumed by (Trust Policy):**
  ```json
  {
    "Version": "2012-10-17",
    "Statement": [
      {
        "Effect": "Allow",
        "Principal": {
          "Service": "ecs-tasks.amazonaws.com"
        },
        "Action": "sts:AssumeRole"
      }
    ]
  }
  ```
- **Custom Policy:** `FinSightAssumeCustomerRolePolicy`
  ```json
  {
    "Version": "2012-10-17",
    "Statement": [
      {
        "Sid": "AllowAssumeCustomerFinSightReadOnlyRoles",
        "Effect": "Allow",
        "Action": "sts:AssumeRole",
        "Resource": "arn:aws:iam::*:role/FinSightReadOnlyRole"
      }
    ]
  }
  ```
- **Purpose & Scope:**
  - This is the **runtime IAM identity** inherited by the FinSight Python container via the ECS credential provider (`AWS_CONTAINER_CREDENTIALS_RELATIVE_URI`).
  - When a user inputs a customer `Role ARN` and `External ID`, `utils/aws_client.py` uses this role as the source principal to call `sts:AssumeRole`.
  - The resulting short-lived session token provides scoped read-only access inside the target customer account.
- **Strict Least-Privilege Safeguards:**
  - **NO** EC2 read/write in the hosting account.
  - **NO** EBS or storage access in the hosting account.
  - **NO** CloudWatch or Cost Explorer permissions in the hosting account.
  - **NO** IAM administration, policy creation, or resource modification.
- **Wildcard Resource Scope Note:** The resource pattern `arn:aws:iam::*:role/FinSightReadOnlyRole` is a design necessity for multi-tenant SaaS where customer account IDs are dynamic and not known ahead of time. In future enterprise iterations, an authorization registry can dynamically constrain target account ARNs.

---

## 4. Role 3: ECS Express Mode Infrastructure Role

- **Recommended Role Name:** `ecsInfrastructureRoleForExpressServices`
- **Assumed by (Trust Policy):** `ecs.amazonaws.com`
- **Purpose & Scope:**
  - Used by **Amazon ECS Express Mode** to automatically manage networking, provision Application Gateway routes, and bind HTTPS endpoints to the Fargate tasks.
- **Security Boundary:** This role is internal to AWS ECS control plane automation. It is **never used** by the FinSight application.

---

## 5. Summary Matrix

| Role | Principal | Key Permissions | Used By |
| :--- | :--- | :--- | :--- |
| `ecsTaskExecutionRole` | `ecs-tasks.amazonaws.com` | `AmazonECSTaskExecutionRolePolicy` (ECR pull, CloudWatch logs) | ECS Container Agent |
| `FinSightProductionTaskRole` | `ecs-tasks.amazonaws.com` | `sts:AssumeRole` on `arn:aws:iam::*:role/FinSightReadOnlyRole` | FinSight Python Application (`boto3`) |
| `ecsInfrastructureRoleForExpressServices` | `ecs.amazonaws.com` | Express Mode infrastructure provisioning | ECS Express Service Controller |
