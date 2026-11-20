data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}
locals {
  oidc_url        = "token.actions.githubusercontent.com"
  github_subject  = "repo:${var.github_owner}/${var.github_repository}:ref:refs/heads/${var.github_branch}"
  ecr_arn         = var.ecr_repository_arn
  ecs_cluster_arn = "arn:${data.aws_partition.current.partition}:ecs:${var.region}:${data.aws_caller_identity.current.account_id}:cluster/${var.ecs_cluster_name}"
  ecs_service_arn = "arn:${data.aws_partition.current.partition}:ecs:${var.region}:${data.aws_caller_identity.current.account_id}:service/${var.ecs_cluster_name}/${var.ecs_service_name}"
}
resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://${local.oidc_url}"
  client_id_list = ["sts.amazonaws.com"]
}
resource "aws_iam_role" "github_actions" {
  name = "${var.project_name}-github-actions-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"

    Statement = [{
      Effect = "Allow"

      Principal = {
        Federated = aws_iam_openid_connect_provider.github.arn
      }

      Action = "sts:AssumeRoleWithWebIdentity"

      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          "token.actions.githubusercontent.com:sub" = local.github_subject
        }
      }
    }]
  })
}
resource "aws_iam_policy" "github_deployment" {
  name = "${var.project_name}-github-deployment-policy"
  policy = jsonencode({
    Version = "2012-10-17"

    Statement = [
      {
        Sid    = "TerraformStateAccess"
        Effect = "Allow"

        Action = [
          "s3:GetObject",
          "s3:PutObject"
        ]

        Resource = "arn:aws:s3:::finsight-finops-tfstate-locking-file/finsight/production/terraform.tfstate"
      },
      {
        Sid    = "TerraformLockFileAccess"
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
      },



      {
        Sid    = "ECRAuthentication"
        Effect = "Allow"

        Action = [
          "ecr:GetAuthorizationToken"
        ]

        Resource = "*"
      },
      {
        Sid    = "ECRImagePush"
        Effect = "Allow"

        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:BatchGetImage",
          "ecr:CompleteLayerUpload",
          "ecr:InitiateLayerUpload",
          "ecr:PutImage",
          "ecr:UploadLayerPart"
        ]

        Resource = local.ecr_arn
      },
      {
        Sid    = "ECSDeployment"
        Effect = "Allow"

        Action = [
          "ecs:RegisterTaskDefinition",
          "ecs:DescribeTaskDefinition",
          "ecs:UpdateService",
          "ecs:DescribeServices",
          "ecs:DescribeClusters"
        ]

        Resource = "*"
      },
      {
        Sid    = "PassECSTaskRoles"
        Effect = "Allow"

        Action = [
          "iam:PassRole"
        ]

        Resource = [
          var.execution_role_arn,
          var.task_role_arn
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
resource "aws_iam_role_policy_attachment" "github_deployment" {
  role       = aws_iam_role.github_actions.name
  policy_arn = aws_iam_policy.github_deployment.arn
}



# resource "aws_iam_openid_connect_provider" "this" {
#   url = "https://token.actions.githubusercontent.com"
#   client_id_list = ["sts.amazonaws.com"]
# }
# data "aws_iam_policy_document" "assume_role" {
#     statement {
#       effect = "Allow"
#         principals {
#           type = "Service"
#           identifiers = ["ec2.amazonaws.com"]
#         }
#         actions = ["sts:AssumeRole"]
#     }
# }
# resource "aws_iam_role" "role" {
#   name = "test-role"
#   assume_role_policy = data.aws_iam_policy_document.assume_role.json
# }
# data "aws_iam_policy_document" "policy" {
#   statement {
#     effect = ""
#   }
# }