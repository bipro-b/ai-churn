# Terraform = Infrastructure as Code. Senior engineers NEVER click around the
# AWS console for production infra. Everything is declared here, version-controlled,
# peer-reviewed, and reproducible. `terraform destroy` tears it all down so you
# never get a surprise bill from forgotten resources.

terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.80"
    }
  }

  # For a team, you'd store state in S3 + DynamoDB lock. For solo learning,
  # local state is fine. Uncomment to use remote state:
  #
  # backend "s3" {
  #   bucket         = "your-tf-state-bucket"
  #   key            = "aiops-churn/terraform.tfstate"
  #   region         = "us-east-1"
  #   dynamodb_table = "terraform-locks"
  #   encrypt        = true
  # }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project   = "aiops-churn"
      ManagedBy = "terraform"
      Owner     = var.owner
    }
  }
}
