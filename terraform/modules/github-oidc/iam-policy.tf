resource "aws_iam_policy" "terraform_iam" {
    name = "${var.project_name}-terraform-iam-policy"
    policy = jsonencode({
        Version = "2012-10-17"
    Statement = [
      {
        Sid    = "IAMReadOperations"
        Effect = "Allow"
        Action = [
          "iam:Get*",
          "iam:List*"
        ]
        Resource = "*"
      },
      {
        Sid    = "ECSRoleManagement"
        Effect = "Allow"
        Action = [
          "iam:CreateRole",
          "iam:DeleteRole",
          "iam:UpdateRole",
          "iam:UpdateAssumeRolePolicy",
          "iam:TagRole",
          "iam:UntagRole"
        ]
        Resource = [
          "arn:aws:iam::575458732659:role/finsight-finops-*"
        ]
      },
      {
        Sid    = "IAMPolicyManagement"
        Effect = "Allow"
        Action = [
          "iam:CreatePolicy",
          "iam:DeletePolicy",
          "iam:CreatePolicyVersion",
          "iam:DeletePolicyVersion",
          "iam:SetDefaultPolicyVersion",
          "iam:TagPolicy",
          "iam:UntagPolicy"
        ]
        Resource = [
          "arn:aws:iam::575458732659:policy/finsight-finops-*"
        ]
      },
      {
        Sid    = "IAMRolePolicyAttachmentManagement"
        Effect = "Allow"
        Action = [
          "iam:AttachRolePolicy",
          "iam:DetachRolePolicy"
        ]
        Resource = [
          "arn:aws:iam::575458732659:role/finsight-finops-*"
        ]
      },
      {
        Sid    = "PassECSRoles"
        Effect = "Allow"
        Action = [
          "iam:PassRole"
        ]
        Resource = [
          "arn:aws:iam::575458732659:role/finsight-finops-*"
        ]
        Condition = {
          StringEquals = {
            "iam:PassedToService" = "ecs-tasks.amazonaws.com"
          }
        }
      }
    ]
    })
}
resource "aws_iam_role_policy_attachment" "terraform_iam" {
    role = aws_iam_role.github_actions.name
    policy_arn = aws_iam_policy.terraform_iam.arn
}