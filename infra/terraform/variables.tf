variable "aws_profile" {
  description = "Local AWS CLI profile used by the AWS provider."
  type        = string
  default     = "vfl-build"
}

variable "pandas_layer_arn" {
  description = "AWS SDK for pandas 3.16.1 managed layer, Python 3.12 x86_64, us-east-1 layer version 27. Override only when intentionally changing the runtime layer."
  type        = string
  default     = "arn:aws:lambda:us-east-1:336392948345:layer:AWSSDKPandas-Python312:27"
}
