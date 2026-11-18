provider "aws" {
  region  = "us-east-1"
  profile = var.aws_profile

  default_tags {
    tags = {
      project = "vol-forecast-lab"
      step    = "10d"
    }
  }
}

resource "aws_sns_topic" "doc_events" {
  name              = "vfl-doc-events"
  kms_master_key_id = "alias/aws/sns"
}

resource "aws_sqs_queue" "dlq" {
  name                      = "vfl-doc-registry-dlq"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue" "main" {
  name                       = "vfl-doc-registry"
  visibility_timeout_seconds = 60
  message_retention_seconds  = 345600
  sqs_managed_sse_enabled    = true
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn
    maxReceiveCount     = 3
  })
}

resource "aws_sqs_queue_redrive_allow_policy" "dlq" {
  queue_url = aws_sqs_queue.dlq.id
  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.main.arn]
  })
}

resource "aws_sqs_queue_policy" "main" {
  queue_url = aws_sqs_queue.main.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "sns.amazonaws.com" }
      Action    = "sqs:SendMessage"
      Resource  = aws_sqs_queue.main.arn
      Condition = { ArnEquals = { "aws:SourceArn" = aws_sns_topic.doc_events.arn } }
    }]
  })
}

resource "aws_sns_topic_subscription" "registry" {
  topic_arn            = aws_sns_topic.doc_events.arn
  protocol             = "sqs"
  endpoint             = aws_sqs_queue.main.arn
  raw_message_delivery = true
  depends_on           = [aws_sqs_queue_policy.main]
}

resource "aws_dynamodb_table" "registry" {
  name                        = "vfl-doc-registry"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "pk"
  range_key                   = "sk"
  deletion_protection_enabled = false

  attribute {
    name = "pk"
    type = "S"
  }

  attribute {
    name = "sk"
    type = "S"
  }

  point_in_time_recovery {
    enabled = false
  }
}

resource "aws_cloudwatch_log_group" "registry" {
  name              = "/aws/lambda/vfl-doc-registry"
  retention_in_days = 7
}

resource "aws_iam_role" "registry" {
  name = "vfl-doc-registry-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "registry" {
  name = "vfl-doc-registry-policy"
  role = aws_iam_role.registry.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes",
          "sqs:ChangeMessageVisibility",
        ]
        Resource = aws_sqs_queue.main.arn
      },
      {
        Effect   = "Allow"
        Action   = "dynamodb:PutItem"
        Resource = aws_dynamodb_table.registry.arn
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.registry.arn}:*"
      },
    ]
  })
}

data "archive_file" "registry" {
  type        = "zip"
  source_dir  = "${path.module}/../../lambda/doc_registry"
  output_path = "${path.module}/build/doc_registry.zip"

  excludes = ["__pycache__"]
}

resource "aws_lambda_function" "registry" {
  function_name    = "vfl-doc-registry"
  role             = aws_iam_role.registry.arn
  runtime          = "python3.13"
  handler          = "handler.lambda_handler"
  memory_size      = 128
  timeout          = 10
  filename         = data.archive_file.registry.output_path
  source_code_hash = data.archive_file.registry.output_base64sha256

  environment {
    variables = {
      TABLE_NAME               = aws_dynamodb_table.registry.name
      ACCEPTED_SCHEMA_VERSIONS = var.accepted_schema_versions
    }
  }

  depends_on = [aws_cloudwatch_log_group.registry, aws_iam_role_policy.registry]
}

resource "aws_lambda_event_source_mapping" "registry" {
  event_source_arn                   = aws_sqs_queue.main.arn
  function_name                      = aws_lambda_function.registry.arn
  batch_size                         = 10
  maximum_batching_window_in_seconds = 1
  function_response_types            = ["ReportBatchItemFailures"]

  scaling_config {
    maximum_concurrency = 2
  }
}

resource "aws_cloudwatch_metric_alarm" "dlq_depth" {
  alarm_name          = "vfl-doc-registry-dlq-depth"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  dimensions = {
    QueueName = aws_sqs_queue.dlq.name
  }
}
