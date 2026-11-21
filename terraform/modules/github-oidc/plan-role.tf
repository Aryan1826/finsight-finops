resource "aws_iam_role" "github_plan" {
    name = "${var.project_name}-github-plan-role"
    assume_role_policy = jsonencode({
        Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Federated = aws_iam_openid_connect_provider.github.arn
        }
        Action = "sts:AssumeRoleWithWebIdentity"

        Condition = {
          StringEquals = {
            "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
            "token.actions.githubusercontent.com:sub" = "repo:Aryan1826/finsight-finops:pull_request"
          }
        }
      }
    ]
    })
}