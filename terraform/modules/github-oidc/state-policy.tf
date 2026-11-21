resource "aws_iam_policy" "terraform_state" {
  name = "${var.project_name}-terraform-state-policy"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "TerraformStateObjects"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject"
        ]
        Resource = "arn:aws:s3:::finsight-finops-tfstate-locking-file/finsight/production/terraform.tfstate"
      },
      {
        Sid    = "TerraformLockFile"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject"
        ]
        Resource = "arn:aws:s3:::finsight-finops-tfstate-locking-file/finsight/production/terraform.tfstate.tflock"
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
      }
    ]
  })
}
resource "aws_iam_role_policy_attachment" "terraform_state" {
    role = aws_iam_role.github_actions.name
    policy_arn = aws_iam_policy.terraform_state.arn
}