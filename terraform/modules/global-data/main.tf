data "aws_caller_identity" "current" {}

resource "aws_kms_key" "primary" {
  provider                = aws.primary
  description             = "${var.name} primary-region data key"
  deletion_window_in_days = 30
  enable_key_rotation     = true
  tags                    = var.tags
}

resource "aws_kms_alias" "primary" {
  provider      = aws.primary
  name          = "alias/${var.name}-primary"
  target_key_id = aws_kms_key.primary.key_id
}

resource "aws_kms_key" "secondary" {
  provider                = aws.secondary
  description             = "${var.name} secondary-region data key"
  deletion_window_in_days = 30
  enable_key_rotation     = true
  tags                    = var.tags
}

resource "aws_kms_alias" "secondary" {
  provider      = aws.secondary
  name          = "alias/${var.name}-secondary"
  target_key_id = aws_kms_key.secondary.key_id
}

resource "aws_dynamodb_table" "transactions" {
  provider         = aws.primary
  name             = "${var.name}-transactions"
  billing_mode     = "PAY_PER_REQUEST"
  hash_key         = "transaction_id"
  stream_enabled   = true
  stream_view_type = "NEW_AND_OLD_IMAGES"

  attribute {
    name = "transaction_id"
    type = "S"
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = aws_kms_key.primary.arn
  }

  point_in_time_recovery {
    enabled = true
  }

  replica {
    region_name            = var.secondary_region
    kms_key_arn            = aws_kms_key.secondary.arn
    point_in_time_recovery = true
    propagate_tags         = true
  }

  lifecycle {
    prevent_destroy = true
  }
  tags = var.tags
}

resource "aws_s3_bucket" "primary" {
  provider = aws.primary
  bucket   = "${var.name}-${data.aws_caller_identity.current.account_id}-${var.primary_region}"
  tags     = var.tags
}

resource "aws_s3_bucket" "secondary" {
  provider = aws.secondary
  bucket   = "${var.name}-${data.aws_caller_identity.current.account_id}-${var.secondary_region}"
  tags     = var.tags
}

resource "aws_s3_bucket_versioning" "primary" {
  provider = aws.primary
  bucket   = aws_s3_bucket.primary.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_versioning" "secondary" {
  provider = aws.secondary
  bucket   = aws_s3_bucket.secondary.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "primary" {
  provider = aws.primary
  bucket   = aws_s3_bucket.primary.id
  rule {
    apply_server_side_encryption_by_default {
      kms_master_key_id = aws_kms_key.primary.arn
      sse_algorithm     = "aws:kms"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "secondary" {
  provider = aws.secondary
  bucket   = aws_s3_bucket.secondary.id
  rule {
    apply_server_side_encryption_by_default {
      kms_master_key_id = aws_kms_key.secondary.arn
      sse_algorithm     = "aws:kms"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "primary" {
  provider                = aws.primary
  bucket                  = aws_s3_bucket.primary.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_public_access_block" "secondary" {
  provider                = aws.secondary
  bucket                  = aws_s3_bucket.secondary.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

data "aws_iam_policy_document" "replication_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["s3.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "replication" {
  provider           = aws.primary
  name               = "${var.name}-s3-replication"
  assume_role_policy = data.aws_iam_policy_document.replication_assume.json
  tags               = var.tags
}

data "aws_iam_policy_document" "replication" {
  statement {
    sid       = "ReadSourceVersions"
    actions   = ["s3:GetReplicationConfiguration", "s3:ListBucket"]
    resources = [aws_s3_bucket.primary.arn]
  }
  statement {
    sid = "ReadEncryptedVersions"
    actions = [
      "s3:GetObjectVersionForReplication",
      "s3:GetObjectVersionAcl",
      "s3:GetObjectVersionTagging",
    ]
    resources = ["${aws_s3_bucket.primary.arn}/*"]
  }
  statement {
    sid       = "ReplicateOnlyToDestination"
    actions   = ["s3:ReplicateObject", "s3:ReplicateDelete", "s3:ReplicateTags"]
    resources = ["${aws_s3_bucket.secondary.arn}/*"]
  }
  statement {
    sid       = "UseScopedKmsKeys"
    actions   = ["kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey"]
    resources = [aws_kms_key.primary.arn, aws_kms_key.secondary.arn]
  }
}

resource "aws_iam_role_policy" "replication" {
  provider = aws.primary
  role     = aws_iam_role.replication.id
  policy   = data.aws_iam_policy_document.replication.json
}

resource "aws_s3_bucket_replication_configuration" "primary_to_secondary" {
  provider = aws.primary
  depends_on = [
    aws_s3_bucket_versioning.primary,
    aws_s3_bucket_versioning.secondary,
  ]
  role   = aws_iam_role.replication.arn
  bucket = aws_s3_bucket.primary.id
  rule {
    id     = "supporting-data-cross-region"
    status = "Enabled"
    filter { prefix = "supporting-data/" }
    destination {
      bucket        = aws_s3_bucket.secondary.arn
      storage_class = "STANDARD"
      encryption_configuration {
        replica_kms_key_id = aws_kms_key.secondary.arn
      }
    }
    source_selection_criteria {
      sse_kms_encrypted_objects { status = "Enabled" }
    }
    delete_marker_replication { status = "Disabled" }
  }
}

