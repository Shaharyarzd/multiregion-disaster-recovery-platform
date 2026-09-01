data "aws_caller_identity" "current" { provider = aws.primary }

data "aws_partition" "current" { provider = aws.primary }

data "aws_iam_policy_document" "data_key" {
  provider = aws.primary
  statement {
    sid = "AccountDelegatedDataKeyAdministrationAndUse"
    actions = [
      "kms:CancelKeyDeletion",
      "kms:CreateAlias",
      "kms:CreateGrant",
      "kms:Decrypt",
      "kms:DescribeKey",
      "kms:DisableKey",
      "kms:DisableKeyRotation",
      "kms:EnableKey",
      "kms:EnableKeyRotation",
      "kms:Encrypt",
      "kms:GenerateDataKey",
      "kms:GenerateDataKeyWithoutPlaintext",
      "kms:GetKeyPolicy",
      "kms:GetKeyRotationStatus",
      "kms:ListGrants",
      "kms:ListKeyPolicies",
      "kms:ListResourceTags",
      "kms:PutKeyPolicy",
      "kms:ReEncryptFrom",
      "kms:ReEncryptTo",
      "kms:RevokeGrant",
      "kms:ScheduleKeyDeletion",
      "kms:TagResource",
      "kms:UntagResource",
      "kms:UpdateKeyDescription",
    ]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = ["arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:root"]
    }
  }
}

data "aws_iam_policy_document" "signing_key" {
  provider = aws.primary
  statement {
    sid = "AccountDelegatedSigningKeyAdministrationAndUse"
    actions = [
      "kms:CancelKeyDeletion",
      "kms:CreateGrant",
      "kms:DescribeKey",
      "kms:DisableKey",
      "kms:EnableKey",
      "kms:GetKeyPolicy",
      "kms:GetKeyRotationStatus",
      "kms:GetPublicKey",
      "kms:ListGrants",
      "kms:ListKeyPolicies",
      "kms:ListResourceTags",
      "kms:PutKeyPolicy",
      "kms:RevokeGrant",
      "kms:ScheduleKeyDeletion",
      "kms:Sign",
      "kms:TagResource",
      "kms:UntagResource",
      "kms:UpdateKeyDescription",
      "kms:Verify",
    ]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = ["arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:root"]
    }
  }
}

resource "aws_kms_key" "primary" {
  provider                = aws.primary
  description             = "${var.name} primary-region data key"
  deletion_window_in_days = 30
  enable_key_rotation     = true
  policy                  = data.aws_iam_policy_document.data_key.json
  tags                    = merge(var.tags, { KeyPurpose = "regional-data" })
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
  policy                  = data.aws_iam_policy_document.data_key.json
  tags                    = merge(var.tags, { KeyPurpose = "regional-data" })
}

resource "aws_kms_alias" "secondary" {
  provider      = aws.secondary
  name          = "alias/${var.name}-secondary"
  target_key_id = aws_kms_key.secondary.key_id
}

resource "aws_kms_key" "evidence_signing" {
  provider                 = aws.primary
  description              = "${var.name} recovery evidence signing key"
  key_usage                = "SIGN_VERIFY"
  customer_master_key_spec = "ECC_NIST_P256"
  deletion_window_in_days  = 30
  policy                   = data.aws_iam_policy_document.signing_key.json
  tags                     = merge(var.tags, { KeyPurpose = "evidence-signing" })
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

resource "aws_s3_bucket" "evidence" {
  provider            = aws.primary
  bucket              = "${var.name}-${data.aws_caller_identity.current.account_id}-evidence"
  object_lock_enabled = true
  tags                = var.tags
}

resource "aws_s3_bucket_versioning" "evidence" {
  provider = aws.primary
  bucket   = aws_s3_bucket.evidence.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "evidence" {
  provider = aws.primary
  bucket   = aws_s3_bucket.evidence.id
  rule {
    apply_server_side_encryption_by_default {
      kms_master_key_id = aws_kms_key.primary.arn
      sse_algorithm     = "aws:kms"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "evidence" {
  provider                = aws.primary
  bucket                  = aws_s3_bucket.evidence.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_object_lock_configuration" "evidence" {
  provider = aws.primary
  bucket   = aws_s3_bucket.evidence.id
  rule {
    default_retention {
      mode = "GOVERNANCE"
      days = 7
    }
  }
  depends_on = [aws_s3_bucket_versioning.evidence]
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
  provider = aws.primary
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
  provider = aws.primary
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
