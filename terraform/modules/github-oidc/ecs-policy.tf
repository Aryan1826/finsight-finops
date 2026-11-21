resource "aws_iam_policy" "terraform_ecs" {
    name = "${var.project_name}-terraform-ecs-policy"
    policy = jsonencode({
         Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ECSReadOperations"
        Effect = "Allow"
        Action = [
          "ecs:Describe*",
          "ecs:List*"
        ]
        Resource = "*"
      },
      {
        Sid    = "ECSClusterManagement"
        Effect = "Allow"
        Action = [
          "ecs:CreateCluster",
          "ecs:DeleteCluster",
          "ecs:UpdateCluster",
          "ecs:TagResource",
          "ecs:UntagResource"
        ]
        Resource = "*"
      },
      {
        Sid    = "ECSTaskDefinitionManagement"
        Effect = "Allow"
        Action = [
          "ecs:RegisterTaskDefinition",
          "ecs:DeregisterTaskDefinition",
          "ecs:TagResource",
          "ecs:UntagResource"
        ]
        Resource = "*"
      },
      {
        Sid    = "ECSServiceManagement"
        Effect = "Allow"
        Action = [
          "ecs:CreateService",
          "ecs:DeleteService",
          "ecs:UpdateService",
          "ecs:TagResource",
          "ecs:UntagResource"
        ]
        Resource = "*"
      }
    ]
    })
}
resource "aws_iam_role_policy_attachment" "terraform_ecs" {
  role = aws_iam_role.github_actions.name
  policy_arn = aws_iam_policy.terraform_ecs.arn
}