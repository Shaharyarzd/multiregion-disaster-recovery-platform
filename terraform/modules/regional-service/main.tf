data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda" {
  name               = "${var.name}-${var.region}-app"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = var.tags
}

data "aws_iam_policy_document" "lambda" {
  statement {
    sid       = "TransactionsOnly"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:Scan"]
    resources = [var.table_arn]
  }
  statement {
    sid       = "StructuredLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.app.arn}:*"]
  }
  statement {
    sid       = "DrMetrics"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = ["Portfolio/DisasterRecovery"]
    }
  }
}

resource "aws_iam_role_policy" "lambda" {
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.lambda.json
}

resource "aws_cloudwatch_log_group" "app" {
  name              = "/aws/lambda/${var.name}-${var.region}"
  retention_in_days = 14
  tags              = var.tags
}

resource "aws_lambda_function" "app" {
  function_name    = "${var.name}-${var.region}"
  role             = aws_iam_role.lambda.arn
  runtime          = "python3.12"
  handler          = "dr_platform.application.handler"
  filename         = var.artifact_path
  source_code_hash = filebase64sha256(var.artifact_path)
  timeout          = 10
  memory_size      = 256
  environment {
    variables = {
      TABLE_NAME = var.table_name
      DR_REGION  = var.region
      LOG_LEVEL  = "INFO"
    }
  }
  depends_on = [aws_cloudwatch_log_group.app]
  tags       = var.tags
}

resource "aws_apigatewayv2_api" "app" {
  name          = "${var.name}-${var.region}"
  protocol_type = "HTTP"
  tags          = var.tags
}

resource "aws_apigatewayv2_integration" "app" {
  api_id                 = aws_apigatewayv2_api.app.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.app.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "routes" {
  for_each = toset([
    "GET /health",
    "GET /transactions",
    "GET /transactions/{id}",
    "POST /transactions",
  ])
  api_id    = aws_apigatewayv2_api.app.id
  route_key = each.value
  target    = "integrations/${aws_apigatewayv2_integration.app.id}"
}

resource "aws_apigatewayv2_stage" "default" {
  count       = var.create_stage ? 1 : 0
  api_id      = aws_apigatewayv2_api.app.id
  name        = "$default"
  auto_deploy = true
  default_route_settings {
    detailed_metrics_enabled = true
    throttling_burst_limit   = 20
    throttling_rate_limit    = 10
  }
  tags = var.tags
}

resource "aws_lambda_permission" "api" {
  statement_id  = "AllowApiGateway"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.app.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.app.execution_arn}/*/*"
}

resource "aws_cloudwatch_metric_alarm" "errors" {
  alarm_name          = "${var.name}-${var.region}-lambda-errors"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = 60
  statistic           = "Sum"
  threshold           = 0
  treat_missing_data  = "notBreaching"
  dimensions = {
    FunctionName = aws_lambda_function.app.function_name
  }
  tags = var.tags
}

resource "aws_cloudwatch_dashboard" "dr" {
  dashboard_name = "${var.name}-${var.region}-dr"
  dashboard_body = jsonencode({
    widgets = [{
      type = "metric", x = 0, y = 0, width = 12, height = 6,
      properties = {
        title  = "Regional API and DR evidence"
        region = var.region
        metrics = [
          ["AWS/Lambda", "Errors", "FunctionName", aws_lambda_function.app.function_name],
          [{ expression = "SEARCH('{Portfolio/DisasterRecovery,Project,Scenario,Region} MetricName=\"RegionHealthy\" Project=\"${var.name}\" Region=\"${var.region}\"', 'Maximum', 60)", id = "region_health" }],
          [{ expression = "SEARCH('{Portfolio/DisasterRecovery,Project,Scenario} MetricName=\"MeasuredRTO\" Project=\"${var.name}\"', 'Maximum', 60)", id = "rto" }],
          [{ expression = "SEARCH('{Portfolio/DisasterRecovery,Project,Scenario} MetricName=\"MeasuredRPO\" Project=\"${var.name}\"', 'Maximum', 60)", id = "rpo" }],
          [{ expression = "SEARCH('{Portfolio/DisasterRecovery,Project,Scenario} MetricName=\"ValidationResult\" Project=\"${var.name}\"', 'Minimum', 60)", id = "validation" }],
          [{ expression = "SEARCH('{Portfolio/DisasterRecovery,Project,Scenario} MetricName=\"RecoveryState\" Project=\"${var.name}\"', 'Maximum', 60)", id = "state" }],
          [{ expression = "SEARCH('{Portfolio/DisasterRecovery,Project,Scenario} MetricName=\"RestoreDuration\" Project=\"${var.name}\"', 'Maximum', 60)", id = "restore" }],
          [{ expression = "SEARCH('{Portfolio/DisasterRecovery,Project,Scenario} MetricName=\"ReplicationLag\" Project=\"${var.name}\"', 'Maximum', 60)", id = "replication_lag" }],
          [{ expression = "SEARCH('{Portfolio/DisasterRecovery,Project,Scenario} MetricName=\"LastSuccessfulDrill\" Project=\"${var.name}\"', 'Maximum', 60)", id = "last_drill" }],
          [{ expression = "SEARCH('{Portfolio/DisasterRecovery,Project,Scenario,Code} MetricName=\"FailureCode\" Project=\"${var.name}\"', 'Sum', 60)", id = "failures" }],
          ["AWS/DynamoDB", "ReplicationLatency", "TableName", var.table_name, "ReceivingRegion", var.region],
        ]
      }
    }]
  })
}
