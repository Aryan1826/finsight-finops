terraform {
  required_version = ">= 1.10.0"
  required_providers {
    aws = {
        source = "hashicorp/aws"
        version = "~> 6.0"
    }
  }
}
provider "aws" {
  region = var.region
}
//data "aws_caller_identity" "current" {}

resource "aws_s3_bucket" "terraform_state" {
    bucket = "finsight-finops-tfstate-locking-file"
    lifecycle {
      prevent_destroy = true
    }
}
resource "aws_s3_bucket_versioning" "terraform_state" {
  bucket = aws_s3_bucket.terraform_state.id
  versioning_configuration {
    status = "Enabled"
  }
}
resource "aws_s3_bucket_server_side_encryption_configuration" "terraform_state" {
  bucket = aws_s3_bucket.terraform_state.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}
resource "aws_s3_bucket_public_access_block" "terraform_state" {
    bucket = aws_s3_bucket.terraform_state.id
    block_public_policy = true
    block_public_acls = true
    ignore_public_acls = true
    restrict_public_buckets = true
}
output "state_bucket_name" {
    value = aws_s3_bucket.terraform_state.id
}