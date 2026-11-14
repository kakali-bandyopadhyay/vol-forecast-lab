provider "aws" {
  region  = "us-east-1"
  profile = var.aws_profile

  default_tags {
    tags = {
      project    = "vol-forecast-lab"
      managed_by = "terraform"
    }
  }
}

# Account-wide monthly cost budget: alerts at 50% and 80% of actual spend and 100% of forecast spend.
resource "aws_budgets_budget" "vfl_monthly" {
  name         = "vfl-monthly-cost"
  budget_type  = "COST"
  limit_amount = format("%.2f", var.monthly_limit_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 50
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.alert_email]
  }

  lifecycle {
    prevent_destroy = true
  }
}
