data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

resource "aws_iam_openid_connect_provider" "github" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]
  tags            = var.tags
}

data "aws_iam_policy_document" "deploy_assume" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repository}:environment:${var.deployment_environment}"]
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
    sid = "ManagePortfolioRuntimeOnly"
    actions = [
      "apigateway:*",
      "lambda:*",
      "logs:*",
      "cloudwatch:*",
      "dynamodb:DescribeTable",
      "dynamodb:UpdateTable",
      "s3:GetBucket*",
      "s3:ListBucket",
      "kms:DescribeKey",
      "iam:GetRole",
      "iam:PassRole",
    ]
    resources = ["arn:${data.aws_partition.current.partition}:*:*:${data.aws_caller_identity.current.account_id}:*"]
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
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repository}:environment:${var.recovery_environment}"]
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
    sid = "RestoreIsolatedDynamoTable"
    actions = [
      "dynamodb:DescribeContinuousBackups",
      "dynamodb:DescribeTable",
      "dynamodb:ListBackups",
      "dynamodb:RestoreTableToPointInTime",
      "dynamodb:Scan",
    ]
    resources = [
      "arn:${data.aws_partition.current.partition}:dynamodb:*:${data.aws_caller_identity.current.account_id}:table/${var.resource_prefix}*"
    ]
  }
  statement {
    sid       = "RecoverVersionedObjects"
    actions   = ["s3:GetObjectVersion", "s3:ListBucketVersions", "s3:PutObject"]
    resources = ["arn:${data.aws_partition.current.partition}:s3:::${var.resource_prefix}*"]
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

