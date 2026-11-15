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

data "aws_caller_identity" "current" {}

resource "random_id" "vfl_bucket_suffix" {
  byte_length = 3
}

# D2 (Spec_vfl-step10c): the extractor ships as a container image – PyMuPDF + pyarrow exceed the
# 250 MB unzipped zip/layer limit. Apply this repository first, push the image (lambda/build_push.ps1),
# then apply again with -var extract_image_uri=<digest URI>; the Lambda and its S3 trigger are created
# only when extract_image_uri is set.
resource "aws_ecr_repository" "vfl_extract" {
  name                 = "vfl-extract"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = true

  image_scanning_configuration {
    scan_on_push = true
  }
}

# Same-account image pull by the Lambda service, limited to the vfl-extract function.
resource "aws_ecr_repository_policy" "vfl_extract" {
  repository = aws_ecr_repository.vfl_extract.name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "VflLambdaPull"
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"]
      Condition = {
        StringLike = {
          "aws:sourceArn" = "arn:aws:lambda:us-east-1:${data.aws_caller_identity.current.account_id}:function:vfl-extract"
        }
      }
    }]
  })
}

resource "aws_ecr_lifecycle_policy" "vfl_extract" {
  repository = aws_ecr_repository.vfl_extract.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the last 3 images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 3
      }
      action = { type = "expire" }
    }]
  })
}

resource "aws_s3_bucket" "vfl_docs" {
  bucket        = "vfl-docs-${random_id.vfl_bucket_suffix.hex}"
  force_destroy = true
}

resource "aws_s3_bucket_versioning" "vfl_docs" {
  bucket = aws_s3_bucket.vfl_docs.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "vfl_docs" {
  bucket = aws_s3_bucket.vfl_docs.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "vfl_docs" {
  bucket                  = aws_s3_bucket.vfl_docs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "vfl_docs" {
  bucket = aws_s3_bucket.vfl_docs.id

  rule {
    id     = "vfl-expire-noncurrent-versions"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = 7
    }
  }

  rule {
    id     = "vfl-abort-incomplete-uploads"
    status = "Enabled"

    filter {}

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

resource "aws_cloudwatch_log_group" "vfl_extract" {
  # Lambda requires its standard /aws/lambda/ log group namespace.
  name              = "/aws/lambda/vfl-extract"
  retention_in_days = 7
}

resource "aws_iam_role" "vfl_extract" {
  name = "vfl-extract-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "vfl_extract" {
  name = "vfl-extract-policy"
  role = aws_iam_role.vfl_extract.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "${aws_s3_bucket.vfl_docs.arn}/raw/*"
      },
      {
        Effect   = "Allow"
        Action   = ["s3:PutObject"]
        Resource = "${aws_s3_bucket.vfl_docs.arn}/clean/*"
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.vfl_extract.arn}:*"
      }
    ]
  })
}

resource "aws_lambda_function" "vfl_extract" {
  count         = var.extract_image_uri == "" ? 0 : 1
  function_name = "vfl-extract"
  role          = aws_iam_role.vfl_extract.arn
  package_type  = "Image"
  image_uri     = var.extract_image_uri
  architectures = ["x86_64"]
  timeout       = 60
  memory_size   = 1024

  environment {
    variables = {
      CLEAN_PREFIX = "clean/"
    }
  }

  depends_on = [aws_cloudwatch_log_group.vfl_extract]
}

resource "aws_lambda_permission" "vfl_s3_invoke" {
  count          = length(aws_lambda_function.vfl_extract)
  statement_id   = "vfl-allow-s3-invoke"
  action         = "lambda:InvokeFunction"
  function_name  = aws_lambda_function.vfl_extract[0].function_name
  principal      = "s3.amazonaws.com"
  source_arn     = aws_s3_bucket.vfl_docs.arn
  source_account = data.aws_caller_identity.current.account_id
}

resource "aws_s3_bucket_notification" "vfl_raw_objects" {
  count  = length(aws_lambda_function.vfl_extract)
  bucket = aws_s3_bucket.vfl_docs.id

  lambda_function {
    lambda_function_arn = aws_lambda_function.vfl_extract[0].arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "raw/"
  }

  depends_on = [aws_lambda_permission.vfl_s3_invoke]
}

resource "aws_glue_catalog_database" "vfl_docs" {
  name = "vfl_docs"
}

resource "aws_glue_catalog_table" "vfl_documents_clean" {
  name          = "vfl_documents_clean"
  database_name = aws_glue_catalog_database.vfl_docs.name
  table_type    = "EXTERNAL_TABLE"

  # Partition projection: Athena derives year/doc_type partitions from the S3 layout
  # clean/year=YYYY/doc_type=<statement|minutes>/, so no MSCK REPAIR or crawler is needed.
  parameters = {
    EXTERNAL                     = "TRUE"
    classification               = "parquet"
    "projection.enabled"         = "true"
    "projection.year.type"       = "integer"
    "projection.year.range"      = "2010,2030"
    "projection.doc_type.type"   = "enum"
    "projection.doc_type.values" = "statement,minutes"
    "storage.location.template"  = "s3://${aws_s3_bucket.vfl_docs.bucket}/clean/year=$${year}/doc_type=$${doc_type}/"
  }

  storage_descriptor {
    location      = "s3://${aws_s3_bucket.vfl_docs.bucket}/clean/"
    input_format  = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"

    ser_de_info {
      name                  = "vfl-parquet-serde"
      serialization_library = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
    }

    columns {
      name = "doc_id"
      type = "string"
    }
    columns {
      name = "event_date"
      type = "date"
    }
    columns {
      name = "url"
      type = "string"
    }
    columns {
      name = "sha256"
      type = "string"
    }
    columns {
      name = "text"
      type = "string"
    }
    columns {
      name = "n_chars"
      type = "int"
    }
    columns {
      name = "page_count"
      type = "int"
    }
    columns {
      name = "extractor_version"
      type = "string"
    }
    columns {
      name = "extracted_at"
      type = "timestamp"
    }
    # D1: vote record + paragraph arrays so the indexer rebuilds the 10b chunks exactly.
    columns {
      name = "votes_against"
      type = "array<string>"
    }
    columns {
      name = "vote_tally"
      type = "string"
    }
    columns {
      name = "blocks_dropped"
      type = "int"
    }
    columns {
      name = "paragraph_texts"
      type = "array<string>"
    }
    columns {
      name = "paragraph_pages"
      type = "array<int>"
    }
  }

  partition_keys {
    name = "year"
    type = "string"
  }
  partition_keys {
    name = "doc_type"
    type = "string"
  }
}

resource "aws_athena_workgroup" "vfl_docs" {
  name          = "vfl-wg"
  force_destroy = true

  configuration {
    enforce_workgroup_configuration    = true
    publish_cloudwatch_metrics_enabled = true

    result_configuration {
      output_location = "s3://${aws_s3_bucket.vfl_docs.bucket}/athena-results/"
    }

    engine_version {
      selected_engine_version = "Athena engine version 3"
    }

    bytes_scanned_cutoff_per_query = 104857600
  }
}
