terraform {
  required_version = ">= 1.10.0"
  backend "s3" {
    bucket = "finsight-finops-tfstate-locking-file"
    key = "finsight/production/terraform.tfstate"
    region = "sa-east-1"
    encrypt = true
    use_lockfile = true
  }
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = var.region
}

module "ecr" {
  source          = "../modules/ecr"
  repository_name = "finsight-finops"
}
module "vpc" {
  source = "../modules/vpc"
  project_name = "finsight-finops"
  vpc_cidr = var.vpc_cidr
}
module "iam" {
  source = "../modules/iam"
  project_name = "finsight-finops"
}
module "ecs" {
  source = "../modules/ecs"
  project_name = "finsight-finops"
  region = var.region
  vpc_id = module.vpc.vpc_id
  private_subnet_ids = module.vpc.private_subnet_ids
  ecr_repository_url = module.ecr.repository_url
  image_tag = "v1"
  execution_role_arn = module.iam.execution_role_arn
  task_role_arn = module.iam.task_role_arn
  cpu = 512
  memory = 1024
  desired_count = 0
  alb_security_group_id = module.alb.alb_security_group_id
  target_group_arn = module.alb.target_group_arn
}
module "alb" {
  source = "../modules/alb"
  project_name = "finsight-finops"
  vpc_id = module.vpc.vpc_id
  public_subnet_ids = module.vpc.public_subnet_ids
}