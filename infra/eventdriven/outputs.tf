output "topic_arn" {
  value = aws_sns_topic.doc_events.arn
}

output "queue_url" {
  value = aws_sqs_queue.main.url
}

output "queue_arn" {
  value = aws_sqs_queue.main.arn
}

output "dlq_url" {
  value = aws_sqs_queue.dlq.url
}

output "dlq_arn" {
  value = aws_sqs_queue.dlq.arn
}

output "table_name" {
  value = aws_dynamodb_table.registry.name
}

output "function_name" {
  value = aws_lambda_function.registry.function_name
}
