variable "aws_profile" {
  type    = string
  default = "vfl-build"
}

variable "accepted_schema_versions" {
  type    = string
  default = "1"

  validation {
    condition     = can(regex("^[0-9]+(,[0-9]+)*$", var.accepted_schema_versions))
    error_message = "Use comma-separated numeric schema versions, such as 1 or 1,2."
  }
}
