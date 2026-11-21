resource "aws_iam_policy" "terraform_discovery" {
  name = "${var.project_name}-terraform-discovery-policy"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AWSAccountIdentityDiscovery"
        Effect = "Allow"
        Action = [
          "sts:GetCallerIdentity"
        ]
        Resource = "*"
      }
    ]
  })
}
resource "aws_iam_role_policy_attachment" "terraform_discovery" {
  role = aws_iam_role.github_actions.name
  policy_arn = aws_iam_policy.terraform_discovery.arn
}