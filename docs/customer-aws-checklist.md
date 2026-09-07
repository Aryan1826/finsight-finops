# Customer AWS Onboarding Checklist

Use this checklist to verify your AWS account is properly configured for FinSight read-only analysis.

---

## Onboarding Steps Checklist

- [ ] **1. Create IAM Role:** Created an IAM Role named `FinSightReadOnlyRole` in your target AWS account.
- [ ] **2. Attach Read-Only Policy:** Attached the least-privilege `FinSightReadOnlyPolicy` containing:
  - `ce:GetCostAndUsage`, `ce:GetCostAndUsageWithResources`
  - `ec2:DescribeInstances`, `ec2:DescribeVolumes`, `ec2:DescribeAddresses`, `ec2:DescribeTags`, `ec2:DescribeVpcs`, `ec2:DescribeSubnets`, `ec2:DescribeSecurityGroups`
  - `cloudwatch:GetMetricData`, `cloudwatch:GetMetricStatistics`, `cloudwatch:ListMetrics`
  - `sts:GetCallerIdentity`
- [ ] **3. Configure Trust Relationship:** Added a trust policy granting `sts:AssumeRole` access.
- [ ] **4. Configure FinSight Principal:** Replaced `<FINSIGHT_DEPLOYMENT_ROLE_ARN>` in the trust policy with the official FinSight deployment execution role ARN.
- [ ] **5. Configure External ID:** Replaced `<FINSIGHT_EXTERNAL_ID>` in the trust condition with your assigned tenant External ID.
- [ ] **6. Copy Role ARN:** Copied the full Role ARN (`arn:aws:iam::<CUSTOMER_ACCOUNT_ID>:role/FinSightReadOnlyRole`).
- [ ] **7. Select Target Region:** Selected your primary AWS region (e.g. `ap-south-1`, `us-east-1`).
- [ ] **8. Test Connection:** Entered Role ARN and External ID in FinSight UI and clicked **Connect & Fetch Data**.
- [ ] **9. Verify Connected Identity:** Confirmed the FinSight top status banner displays your exact 12-digit AWS Account ID and assumed role ARN.
