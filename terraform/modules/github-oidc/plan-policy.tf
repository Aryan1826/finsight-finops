resource "aws_iam_policy" "github_plan_readonly" {
    name = "${var.project_name}-github-plan-readonly-policy"
    policy = jsonencode({
         Version = "2012-10-17"
    Statement = [
      {
        Sid    = "TerraformStateRead"
        Effect = "Allow"
        Action = [
          "s3:GetObject"
        ]
        Resource = "arn:aws:s3:::finsight-finops-tfstate-locking-file/finsight/production/terraform.tfstate"
      },
      {
        Sid    = "TerraformStateBucketList"
        Effect = "Allow"
        Action = [
          "s3:ListBucket"
        ]
        Resource = "arn:aws:s3:::finsight-finops-tfstate-locking-file"
        Condition = {
          StringLike = {
            "s3:prefix" = [
              "finsight/production/terraform.tfstate",
              "finsight/production/terraform.tfstate.tflock"
            ]
          }
        }
      },
      {
        Sid    = "TerraformLockManagement"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject"
        ]
        Resource = "arn:aws:s3:::finsight-finops-tfstate-locking-file/finsight/production/terraform.tfstate.tflock"
      },
      {
        Sid    = "AWSIdentityRead"
        Effect = "Allow"
        Action = [
          "sts:GetCallerIdentity"
        ]
        Resource = "*"
      },
      {
        Sid    = "NetworkRead"
        Effect = "Allow"
        Action = [
          "ec2:Describe*",
          "ec2:Get*"
        ]
        Resource = "*"
      },
      {
        Sid    = "ECSRead"
        Effect = "Allow"
        Action = [
          "ecs:Describe*",
          "ecs:List*"
        ]
        Resource = "*"
      },
      {
        Sid    = "ALBRead"
        Effect = "Allow"
        Action = [
          "elasticloadbalancing:Describe*"
        ]
        Resource = "*"
      },
      {
        Sid    = "ECRRead"
        Effect = "Allow"
        Action = [
          "ecr:Describe*",
          "ecr:Get*",
          "ecr:List*"
        ]
        Resource = "*"
      },
      {
        Sid    = "CloudWatchRead"
        Effect = "Allow"
        Action = [
          "logs:Describe*",
          "logs:Get*",
          "logs:List*"
        ]
        Resource = "*"
      },
      {
        Sid    = "IAMRead"
        Effect = "Allow"
        Action = [
          "iam:Get*",
          "iam:List*"
        ]
        Resource = "*"
      }
    ]
    })
}
resource "aws_iam_role_policy_attachment" "github_plan_readonly" {
  role = aws_iam_role.github_plan.name
  policy_arn = aws_iam_policy.github_plan_readonly.arn
}