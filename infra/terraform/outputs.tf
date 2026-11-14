output "bucket_name" {
  description = "Name of the vfl document bucket."
  value       = aws_s3_bucket.vfl_docs.bucket
}

output "lambda_name" {
  description = "Name of the vfl extraction function."
  value       = aws_lambda_function.vfl_extract.function_name
}

output "workgroup_name" {
  description = "Name of the vfl Athena workgroup."
  value       = aws_athena_workgroup.vfl_docs.name
}
