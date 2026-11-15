output "bucket_name" {
  description = "Name of the vfl document bucket."
  value       = aws_s3_bucket.vfl_docs.bucket
}

output "lambda_name" {
  description = "Name of the vfl extraction function."
  value       = one(aws_lambda_function.vfl_extract[*].function_name)
}

output "workgroup_name" {
  description = "Name of the vfl Athena workgroup."
  value       = aws_athena_workgroup.vfl_docs.name
}

output "ecr_repository_url" {
  description = "ECR repository for the vfl-extract image (used by lambda/build_push.ps1)."
  value       = aws_ecr_repository.vfl_extract.repository_url
}
