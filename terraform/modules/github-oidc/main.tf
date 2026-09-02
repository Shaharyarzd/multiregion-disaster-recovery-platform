data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

data "aws_iam_policy_document" "deploy_assume" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [var.github_oidc_provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["${var.github_oidc_subject_prefix}:environment:${var.deployment_environment}"]
    }
  }
}

resource "aws_iam_role" "deploy" {
  name               = "${var.name}-github-deploy"
  assume_role_policy = data.aws_iam_policy_document.deploy_assume.json
  tags               = var.tags
}

data "aws_iam_policy_document" "deploy" {
  statement {
    sid = "ManagePortfolioLambda"
    actions = [
      "lambda:AddPermission",
      "lambda:CreateFunction",
      "lambda:DeleteFunction",
      "lambda:GetFunction",
      "lambda:GetFunctionCodeSigningConfig",
      "lambda:GetPolicy",
      "lambda:ListVersionsByFunction",
      "lambda:RemovePermission",
      "lambda:TagResource",
      "lambda:UntagResource",
      "lambda:UpdateFunctionCode",
      "lambda:UpdateFunctionConfiguration",
    ]
    resources = ["arn:${data.aws_partition.current.partition}:lambda:*:${data.aws_caller_identity.current.account_id}:function:${var.resource_prefix}-*"]
  }
  statement {
    sid       = "ReadHttpApisForTerraform"
    actions   = ["apigateway:GET"]
    resources = ["arn:${data.aws_partition.current.partition}:apigateway:*::/apis*"]
  }
  statement {
    sid       = "CreateTaggedPortfolioHttpApis"
    actions   = ["apigateway:POST"]
    resources = ["arn:${data.aws_partition.current.partition}:apigateway:*::/apis"]
    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/Project"
      values   = [var.resource_prefix]
    }
  }
  statement {
    sid       = "ManageTaggedPortfolioHttpApis"
    actions   = ["apigateway:DELETE", "apigateway:PATCH", "apigateway:POST", "apigateway:PUT"]
    resources = ["arn:${data.aws_partition.current.partition}:apigateway:*::/apis/*"]
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/Project"
      values   = [var.resource_prefix]
    }
  }
  statement {
    sid       = "ReadRuntimeObservability"
    actions   = ["cloudwatch:DescribeAlarms", "logs:DescribeLogGroups"]
    resources = ["*"]
  }
  statement {
    sid = "ManagePortfolioAlarms"
    actions = [
      "cloudwatch:DeleteAlarms",
      "cloudwatch:PutMetricAlarm",
    ]
    resources = ["arn:${data.aws_partition.current.partition}:cloudwatch:*:${data.aws_caller_identity.current.account_id}:alarm:${var.resource_prefix}-*"]
  }
  statement {
    sid = "ManagePortfolioDashboards"
    actions = [
      "cloudwatch:DeleteDashboards",
      "cloudwatch:GetDashboard",
      "cloudwatch:PutDashboard",
    ]
    resources = ["arn:${data.aws_partition.current.partition}:cloudwatch::${data.aws_caller_identity.current.account_id}:dashboard/${var.resource_prefix}-*"]
  }
  statement {
    sid = "ManagePortfolioLogGroups"
    actions = [
      "logs:CreateLogGroup",
      "logs:DeleteLogGroup",
      "logs:ListTagsForResource",
      "logs:PutRetentionPolicy",
      "logs:TagResource",
      "logs:UntagResource",
    ]
    resources = ["arn:${data.aws_partition.current.partition}:logs:*:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/${var.resource_prefix}-*"]
  }
  statement {
    sid = "ManagePortfolioLambdaRoles"
    actions = [
      "iam:CreateRole",
      "iam:DeleteRole",
      "iam:DeleteRolePolicy",
      "iam:GetRole",
      "iam:GetRolePolicy",
      "iam:ListAttachedRolePolicies",
      "iam:ListRolePolicies",
      "iam:PutRolePolicy",
      "iam:TagRole",
      "iam:UntagRole",
      "iam:UpdateAssumeRolePolicy",
    ]
    resources = [
      "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:role/${var.resource_prefix}-*-app",
      "arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:role/${var.resource_prefix}-s3-replication",
    ]
  }
  statement {
    sid       = "PassOnlyPortfolioLambdaRoles"
    actions   = ["iam:PassRole"]
    resources = ["arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:role/${var.resource_prefix}-*"]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["lambda.amazonaws.com"]
    }
  }
  statement {
    sid       = "PassOnlyPortfolioS3ReplicationRole"
    actions   = ["iam:PassRole"]
    resources = ["arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:role/${var.resource_prefix}-s3-replication"]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["s3.amazonaws.com"]
    }
  }
  statement {
    sid       = "ReadSharedRuntimeDependencies"
    actions   = ["dynamodb:DescribeTable", "kms:DescribeKey", "s3:GetBucketLocation", "s3:ListBucket"]
    resources = ["*"]
  }
  statement {
    sid = "ManageProjectDynamoInfrastructure"
    actions = [
      "dynamodb:CreateTable",
      "dynamodb:CreateTableReplica",
      "dynamodb:DeleteTable",
      "dynamodb:DescribeContinuousBackups",
      "dynamodb:DescribeTable",
      "dynamodb:DescribeTimeToLive",
      "dynamodb:GetItem",
      "dynamodb:ListTagsOfResource",
      "dynamodb:Query",
      "dynamodb:Scan",
      "dynamodb:TagResource",
      "dynamodb:UntagResource",
      "dynamodb:UpdateContinuousBackups",
      "dynamodb:UpdateTable",
    ]
    resources = [
      "arn:${data.aws_partition.current.partition}:dynamodb:${var.primary_region}:${data.aws_caller_identity.current.account_id}:table/${var.resource_prefix}-*",
      "arn:${data.aws_partition.current.partition}:dynamodb:${var.secondary_region}:${data.aws_caller_identity.current.account_id}:table/${var.resource_prefix}-*",
    ]
  }
  dynamic "statement" {
    for_each = var.temporary_replica_update_item ? [1] : []
    content {
      sid       = "TemporaryEmptySecondaryReplicaBootstrap"
      actions   = ["dynamodb:BatchWriteItem", "dynamodb:DeleteItem", "dynamodb:PutItem", "dynamodb:UpdateItem"]
      resources = ["arn:${data.aws_partition.current.partition}:dynamodb:${var.secondary_region}:${data.aws_caller_identity.current.account_id}:table/${var.resource_prefix}-transactions"]
    }
  }
  statement {
    sid = "ManageProjectS3Buckets"
    actions = [
      "s3:CreateBucket",
      "s3:DeleteBucket",
      "s3:DeleteBucketPolicy",
      "s3:GetEncryptionConfiguration",
      "s3:GetLifecycleConfiguration",
      "s3:GetBucketObjectLockConfiguration",
      "s3:GetBucketAcl",
      "s3:GetAccelerateConfiguration",
      "s3:GetBucketCORS",
      "s3:GetBucketLogging",
      "s3:GetBucketPolicy",
      "s3:GetBucketPublicAccessBlock",
      "s3:GetBucketRequestPayment",
      "s3:GetBucketWebsite",
      "s3:GetReplicationConfiguration",
      "s3:GetBucketTagging",
      "s3:GetBucketVersioning",
      "s3:ListBucket",
      "s3:ListBucketVersions",
      "s3:PutEncryptionConfiguration",
      "s3:PutBucketObjectLockConfiguration",
      "s3:PutBucketPolicy",
      "s3:PutBucketPublicAccessBlock",
      "s3:PutReplicationConfiguration",
      "s3:PutBucketTagging",
      "s3:PutBucketVersioning",
    ]
    resources = ["arn:${data.aws_partition.current.partition}:s3:::${var.resource_prefix}-*"]
  }
  statement {
    sid       = "DeleteProjectS3Objects"
    actions   = ["s3:DeleteObject", "s3:DeleteObjectVersion"]
    resources = ["arn:${data.aws_partition.current.partition}:s3:::${var.resource_prefix}-*/*"]
  }
  statement {
    sid       = "CreateTaggedProjectKmsKeys"
    actions   = ["kms:CreateKey"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/Project"
      values   = [var.resource_prefix]
    }
  }
  statement {
    sid = "ManageTaggedProjectKmsKeys"
    actions = [
      "kms:DescribeKey",
      "kms:EnableKeyRotation",
      "kms:GetKeyPolicy",
      "kms:GetKeyRotationStatus",
      "kms:ListGrants",
      "kms:ListResourceTags",
      "kms:PutKeyPolicy",
      "kms:ScheduleKeyDeletion",
      "kms:TagResource",
      "kms:UntagResource",
      "kms:UpdateKeyDescription",
    ]
    resources = ["arn:${data.aws_partition.current.partition}:kms:*:${data.aws_caller_identity.current.account_id}:key/*"]
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/Project"
      values   = [var.resource_prefix]
    }
  }
  statement {
    sid       = "CreateOnlyAwsResourceKmsGrants"
    actions   = ["kms:CreateGrant"]
    resources = ["arn:${data.aws_partition.current.partition}:kms:*:${data.aws_caller_identity.current.account_id}:key/*"]
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/Project"
      values   = [var.resource_prefix]
    }
    condition {
      test     = "Bool"
      variable = "kms:GrantIsForAWSResource"
      values   = ["true"]
    }
  }
  statement {
    sid = "ManageProjectKmsAliases"
    actions = [
      "kms:CreateAlias",
      "kms:DeleteAlias",
      "kms:UpdateAlias",
    ]
    resources = [
      "arn:${data.aws_partition.current.partition}:kms:*:${data.aws_caller_identity.current.account_id}:alias/${var.resource_prefix}-*",
      "arn:${data.aws_partition.current.partition}:kms:*:${data.aws_caller_identity.current.account_id}:key/*",
    ]
  }
  statement {
    sid       = "ReadKmsAliases"
    actions   = ["kms:ListAliases"]
    resources = ["*"]
  }
  statement {
    sid       = "DenyRecoveryPromotion"
    effect    = "Deny"
    actions   = ["dynamodb:RestoreTableToPointInTime", "route53:ChangeResourceRecordSets"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "deploy" {
  role   = aws_iam_role.deploy.id
  policy = data.aws_iam_policy_document.deploy.json
}

data "aws_iam_policy_document" "recovery_assume" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [var.github_oidc_provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["${var.github_oidc_subject_prefix}:environment:${var.recovery_environment}"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "recovery" {
  name                 = "${var.name}-github-recovery"
  assume_role_policy   = data.aws_iam_policy_document.recovery_assume.json
  max_session_duration = 3600
  tags                 = var.tags
}

data "aws_iam_policy_document" "recovery" {
  statement {
    sid = "ReadAndRestoreProductionSource"
    actions = [
      "dynamodb:DescribeContinuousBackups",
      "dynamodb:DescribeTable",
      "dynamodb:ListTagsOfResource",
      "dynamodb:RestoreTableToPointInTime",
      "dynamodb:Scan",
    ]
    resources = [
      "arn:${data.aws_partition.current.partition}:dynamodb:${var.primary_region}:${data.aws_caller_identity.current.account_id}:table/${var.resource_prefix}-transactions",
      "arn:${data.aws_partition.current.partition}:dynamodb:${var.secondary_region}:${data.aws_caller_identity.current.account_id}:table/${var.resource_prefix}-transactions",
    ]
  }
  statement {
    sid = "ConfigureAndValidateIsolatedRecoveryTargets"
    actions = [
      "dynamodb:DeleteTable",
      "dynamodb:DescribeContinuousBackups",
      "dynamodb:DescribeTable",
      "dynamodb:DescribeTimeToLive",
      "dynamodb:GetItem",
      "dynamodb:ListTagsOfResource",
      "dynamodb:PutItem",
      "dynamodb:Scan",
      "dynamodb:TagResource",
      "dynamodb:UpdateContinuousBackups",
      "dynamodb:UpdateTable",
      "dynamodb:UpdateTimeToLive",
    ]
    resources = [
      "arn:${data.aws_partition.current.partition}:dynamodb:${var.primary_region}:${data.aws_caller_identity.current.account_id}:table/${var.resource_prefix}-recovery-*",
      "arn:${data.aws_partition.current.partition}:dynamodb:${var.secondary_region}:${data.aws_caller_identity.current.account_id}:table/${var.resource_prefix}-recovery-*",
    ]
  }
  statement {
    sid = "ApprovalGatedSyntheticProductionReconciliation"
    actions = [
      "dynamodb:DeleteItem",
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
    ]
    resources = [
      "arn:${data.aws_partition.current.partition}:dynamodb:${var.primary_region}:${data.aws_caller_identity.current.account_id}:table/${var.resource_prefix}-transactions",
      "arn:${data.aws_partition.current.partition}:dynamodb:${var.secondary_region}:${data.aws_caller_identity.current.account_id}:table/${var.resource_prefix}-transactions",
    ]
    condition {
      test     = "ForAllValues:StringLike"
      variable = "dynamodb:LeadingKeys"
      values   = ["txn-*"]
    }
  }
  statement {
    sid       = "ListVersionedRecoveryBuckets"
    actions   = ["s3:ListBucketVersions"]
    resources = ["arn:${data.aws_partition.current.partition}:s3:::${var.resource_prefix}-*"]
  }
  statement {
    sid = "RecoverVersionedObjects"
    actions = [
      "s3:GetObjectVersion",
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
    ]
    resources = ["arn:${data.aws_partition.current.partition}:s3:::${var.resource_prefix}-*/*"]
  }
  statement {
    sid       = "UseProjectDataKeysForRecovery"
    actions   = ["kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey"]
    resources = ["arn:${data.aws_partition.current.partition}:kms:*:${data.aws_caller_identity.current.account_id}:key/*"]
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/Project"
      values   = [var.resource_prefix]
    }
  }
  statement {
    sid = "ReadRecoverySignals"
    actions = [
      "cloudwatch:GetMetricData",
      "cloudwatch:GetMetricStatistics",
      "cloudwatch:ListMetrics",
    ]
    resources = ["*"]
  }
  statement {
    sid       = "EmitEvidenceMetrics"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = ["Portfolio/DisasterRecovery"]
    }
  }
}

resource "aws_iam_role_policy" "recovery" {
  role   = aws_iam_role.recovery.id
  policy = data.aws_iam_policy_document.recovery.json
}

data "aws_iam_policy_document" "evidence_assume" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [var.github_oidc_provider_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["${var.github_oidc_subject_prefix}:environment:${var.evidence_environment}"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "evidence" {
  name                 = "${var.name}-github-evidence"
  assume_role_policy   = data.aws_iam_policy_document.evidence_assume.json
  max_session_duration = 3600
  tags                 = var.tags
}

data "aws_iam_policy_document" "evidence" {
  statement {
    sid       = "SignAndVerifyEvidence"
    actions   = ["kms:Sign", "kms:Verify", "kms:GetPublicKey", "kms:DescribeKey"]
    resources = ["arn:${data.aws_partition.current.partition}:kms:*:${data.aws_caller_identity.current.account_id}:key/*"]
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/KeyPurpose"
      values   = ["evidence-signing"]
    }
  }
  statement {
    sid = "ArchiveAndReadBackEvidence"
    actions = [
      "s3:GetBucketObjectLockConfiguration",
      "s3:GetObject",
      "s3:GetObjectRetention",
      "s3:ListBucket",
      "s3:PutObject",
      "s3:PutObjectRetention",
    ]
    resources = [
      "arn:${data.aws_partition.current.partition}:s3:::${var.resource_prefix}-*-evidence",
      "arn:${data.aws_partition.current.partition}:s3:::${var.resource_prefix}-*-evidence/evidence/*",
    ]
  }
  statement {
    sid       = "NeverBypassEvidenceRetention"
    effect    = "Deny"
    actions   = ["s3:BypassGovernanceRetention"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "evidence" {
  role   = aws_iam_role.evidence.id
  policy = data.aws_iam_policy_document.evidence.json
}
