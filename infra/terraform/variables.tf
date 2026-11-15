variable "aws_profile" {
  description = "Local AWS CLI profile used by the AWS provider."
  type        = string
  default     = "vfl-build"
}

variable "extract_image_uri" {
  description = "ECR image URI (with @sha256 digest) for the vfl-extract Lambda, printed by lambda/build_push.ps1. Empty on the first apply: only the ECR repository and the data lake are created."
  type        = string
  default     = ""
}
