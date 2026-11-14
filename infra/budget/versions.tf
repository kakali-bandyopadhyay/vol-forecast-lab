terraform {
  required_version = ">= 1.17.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

# Separate root with its own local state: applied BEFORE infra/terraform and never destroyed,
# so the cost guardrail outlives every evidence run.
