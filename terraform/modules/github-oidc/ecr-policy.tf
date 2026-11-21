resource "aws_iam_policy" "terraform_ecr" {
    name = "${var.project_name}-terraform-ecr-policy"
    policy = jsonencode({
         Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ECRReadOperations"
        Effect = "Allow"
        Action = [
          "ecr:DescribeRepositories",
          "ecr:DescribeImages",
          "ecr:ListImages",
          "ecr:GetRepositoryPolicy",
          "ecr:GetLifecyclePolicy",
          "ecr:GetLifecyclePolicyPreview",
          "ecr:ListTagsForResource"
        ]
        Resource = "*"
      },
      {
        Sid    = "ECRRepositoryManagement"
        Effect = "Allow"
        Action = [
          "ecr:CreateRepository",
          "ecr:DeleteRepository",
          "ecr:PutImageTagMutability",
          "ecr:PutImageScanningConfiguration",
          "ecr:PutEncryptionConfiguration",
          "ecr:TagResource",
          "ecr:UntagResource"
        ]
        Resource = "*"
      },
      {
        Sid    = "ECRDockerPushPull"
        Effect = "Allow"
        Action = [
          "ecr:GetAuthorizationToken",
          "ecr:BatchCheckLayerAvailability",
          "ecr:InitiateLayerUpload",
          "ecr:UploadLayerPart",
          "ecr:CompleteLayerUpload",
          "ecr:PutImage",
          "ecr:BatchGetImage",
          "ecr:GetDownloadUrlForLayer"
        ]
        Resource = "*"
      }
    ]
    })
}
resource "aws_iam_role_policy_attachment" "terraform_ecr" {
  role = aws_iam_role.github_actions.name
  policy_arn = aws_iam_policy.terraform_ecr.arn
}