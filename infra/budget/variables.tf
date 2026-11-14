variable "aws_profile" {
  description = "Local AWS CLI profile used by the AWS provider."
  type        = string
  default     = "vfl-build"
}

variable "monthly_limit_usd" {
  description = "Monthly cost budget for the account, in USD."
  type        = number
  default     = 10
}

variable "alert_email" {
  description = "Address that receives budget alerts. Set in terraform.tfvars (git-ignored) or TF_VAR_alert_email; never committed."
  type        = string
  sensitive   = true
}
