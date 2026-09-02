"""Regression checks for the approval-gated production reconciliation boundary."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "terraform/modules/github-oidc/main.tf").read_text(encoding="utf-8")
DEPLOY_WORKFLOW = (ROOT / ".github/workflows/aws-deploy.yml").read_text(encoding="utf-8")
PLAN_WORKFLOW = (ROOT / ".github/workflows/aws-plan.yml").read_text(encoding="utf-8")
STAGE_SCRIPT = (ROOT / "scripts/apply-regional-stage.sh").read_text(encoding="utf-8")
REGIONAL_SERVICE = (ROOT / "terraform/modules/regional-service/main.tf").read_text(encoding="utf-8")
DEPLOY = SOURCE.split('data "aws_iam_policy_document" "deploy"', 1)[1].split(
    'resource "aws_iam_role_policy" "deploy"', 1
)[0]
RECOVERY = SOURCE.split('data "aws_iam_policy_document" "recovery"', 1)[1].split(
    'resource "aws_iam_role_policy" "recovery"', 1
)[0]


def test_deploy_mutation_exists_only_in_guarded_replica_bootstrap() -> None:
    infrastructure = DEPLOY.split('sid = "ManageProjectDynamoInfrastructure"', 1)[1].split(
        "\n  }", 1
    )[0]
    assert "dynamodb:BatchWriteItem" not in infrastructure
    assert "dynamodb:DeleteItem" not in infrastructure
    assert "dynamodb:PutItem" not in infrastructure
    assert "dynamodb:UpdateItem" not in infrastructure
    temporary = DEPLOY.split('sid       = "TemporaryEmptySecondaryReplicaBootstrap"', 1)[1].split(
        "\n    }", 1
    )[0]
    assert (
        'actions   = ["dynamodb:BatchWriteItem", "dynamodb:DeleteItem", '
        '"dynamodb:PutItem", "dynamodb:UpdateItem"]' in temporary
    )
    assert "${var.secondary_region}" in temporary
    assert ":table/${var.resource_prefix}-transactions" in temporary
    assert "var.temporary_replica_update_item ? [1] : []" in DEPLOY
    assert '"dynamodb:DescribeTimeToLive"' in infrastructure
    assert '"s3:GetBucketCORS"' in DEPLOY
    assert '"s3:GetAccelerateConfiguration"' in DEPLOY
    assert '"s3:GetBucketLogging"' in DEPLOY
    assert '"s3:GetLifecycleConfiguration"' in DEPLOY
    assert '"s3:GetBucketRequestPayment"' in DEPLOY
    assert '"s3:GetBucketWebsite"' in DEPLOY
    role_management = DEPLOY.split('sid = "ManagePortfolioLambdaRoles"', 1)[1].split("\n  }", 1)[0]
    assert 'role/${var.resource_prefix}-*"' not in role_management
    assert "role/${var.resource_prefix}-*-app" in role_management
    assert "role/${var.resource_prefix}-s3-replication" in role_management


def test_resume_repairs_only_verified_tainted_resources_and_blocks_destroy() -> None:
    assert DEPLOY_WORKFLOW.count("terraform untaint") == 5
    assert "terraform untaint module.data.aws_dynamodb_table.transactions" in DEPLOY_WORKFLOW
    assert "terraform untaint module.data.aws_kms_key.evidence_signing" in DEPLOY_WORKFLOW
    assert "terraform untaint module.data.aws_s3_bucket.primary" in DEPLOY_WORKFLOW
    assert "terraform untaint module.data.aws_s3_bucket.secondary" in DEPLOY_WORKFLOW
    assert "terraform untaint module.data.aws_s3_bucket.evidence" in DEPLOY_WORKFLOW
    assert "'Table.TableStatus'" in DEPLOY_WORKFLOW
    assert "'Table.ItemCount'" in DEPLOY_WORKFLOW
    assert "'KeyMetadata.KeyState'" in DEPLOY_WORKFLOW
    assert DEPLOY_WORKFLOW.count("list-object-versions") == 3
    assert DEPLOY_WORKFLOW.count("head-bucket") == 3
    assert "ObjectLockConfiguration.ObjectLockEnabled" in DEPLOY_WORKFLOW
    assert 'index("delete")' in DEPLOY_WORKFLOW


def test_api_tag_on_create_is_region_and_required_tag_scoped() -> None:
    create_primary = DEPLOY.split('sid       = "CreateTaggedPrimaryPortfolioHttpApi"', 1)[1].split(
        "\n  }", 1
    )[0]
    create_secondary = DEPLOY.split('sid       = "CreateTaggedSecondaryPortfolioHttpApi"', 1)[
        1
    ].split("\n  }", 1)[0]
    primary = DEPLOY.split('sid       = "TagPrimaryPortfolioHttpApiOnCreate"', 1)[1].split(
        "\n  }", 1
    )[0]
    secondary = DEPLOY.split('sid       = "TagSecondaryPortfolioHttpApiOnCreate"', 1)[1].split(
        "\n  }", 1
    )[0]
    for statement in (create_primary, primary):
        assert "${var.primary_region}" in statement
        assert 'values   = ["active-a"]' in statement
    for statement in (create_secondary, secondary):
        assert "${var.secondary_region}" in statement
        assert 'values   = ["active-b"]' in statement
    assert "%2Fv2%2Fapis%2F*" in primary
    assert "%2Fv2%2Fapis%2F*" in secondary
    assert "::/apis" in create_primary
    assert "::/apis" in create_secondary
    for statement in (create_primary, create_secondary, primary, secondary):
        assert 'actions   = ["apigateway:POST"]' in statement
        assert 'variable = "aws:RequestTag/Project"' in statement
        assert 'variable = "aws:RequestTag/DataClassification"' in statement
        assert 'values   = ["SYNTHETIC"]' in statement
        assert 'test     = "ForAllValues:StringEquals"' in statement
        assert 'variable = "aws:TagKeys"' in statement
        assert 'values   = ["Project", "RegionRole", "DataClassification"]' in statement


def test_cloudwatch_alarm_tag_read_is_two_region_and_prefix_scoped() -> None:
    statement = DEPLOY.split('sid = "ManagePortfolioAlarms"', 1)[1].split("\n  }", 1)[0]
    assert '"cloudwatch:ListTagsForResource"' in statement
    assert statement.count("alarm:${var.resource_prefix}-*") == 2
    assert "cloudwatch:${var.primary_region}:" in statement
    assert "cloudwatch:${var.secondary_region}:" in statement
    assert "cloudwatch:*:" not in statement


def test_api_stage_creation_uses_only_captured_exact_api_ids() -> None:
    assert "::/apis/*/stages" not in DEPLOY
    assert 'for_each = var.primary_api_id == "" ? [] : [var.primary_api_id]' in DEPLOY
    assert 'for_each = var.secondary_api_id == "" ? [] : [var.secondary_api_id]' in DEPLOY
    assert DEPLOY.count('actions   = ["apigateway:POST", "apigateway:PUT"]') == 2
    assert "apigateway:${var.primary_region}::/apis/${statement.value}/stages" in DEPLOY
    assert "apigateway:${var.secondary_region}::/apis/${statement.value}/stages" in DEPLOY


def test_deployment_is_two_phase_and_rejects_regional_destroy() -> None:
    assert "provision_stages:" in DEPLOY_WORKFLOW
    assert "default: false" in DEPLOY_WORKFLOW
    assert DEPLOY_WORKFLOW.count("./scripts/apply-regional-stage.sh") == 2
    assert 'index("delete")' in DEPLOY_WORKFLOW
    assert "Refusing regional plan containing destroy or replacement actions" in STAGE_SCRIPT
    assert "stage_address='module.service.aws_apigatewayv2_stage.default[0]'" in STAGE_SCRIPT
    assert 'and $changes[0].change.actions == ["delete"]' in STAGE_SCRIPT
    assert (
        '--arg primary_api_id "$(terraform -chdir=terraform/stacks/region-a output -raw api_id)"'
        in DEPLOY_WORKFLOW
    )
    assert (
        '--arg secondary_api_id "$(terraform -chdir=terraform/stacks/region-b output -raw api_id)"'
        in DEPLOY_WORKFLOW
    )
    assert "count       = var.create_stage ? 1 : 0" in REGIONAL_SERVICE


def test_lambda_runtime_uses_only_its_regional_table_and_data_key() -> None:
    statement = REGIONAL_SERVICE.split('sid       = "DecryptOnlyRegionalDynamoDataKey"', 1)[
        1
    ].split("\n  }", 1)[0]
    assert 'actions   = ["kms:Decrypt"]' in statement
    assert "resources = [var.table_kms_key_arn]" in statement
    assert "kms:Encrypt" not in statement
    assert "kms:GenerateDataKey" not in statement
    assert (
        '"$(terraform -chdir=terraform/stacks/global output -raw table_arn)" \\\n'
        '            "$(terraform -chdir=terraform/stacks/global output -raw primary_kms_key_arn)"'
        in DEPLOY_WORKFLOW
    )
    assert (
        '"$(terraform -chdir=terraform/stacks/global output -raw secondary_table_arn)" \\\n'
        '            "$(terraform -chdir=terraform/stacks/global output -raw secondary_kms_key_arn)"'
        in DEPLOY_WORKFLOW
    )


def test_stage_tags_are_verified_before_traffic_and_failures_roll_back() -> None:
    assert "auto_deploy = var.stage_traffic_enabled" in REGIONAL_SERVICE
    assert "tags = var.stage_tags_enabled ? var.tags : {}" in REGIONAL_SERVICE
    assert "!var.stage_traffic_enabled || var.stage_tags_enabled" in REGIONAL_SERVICE
    assert STAGE_SCRIPT.index('-var="stage_traffic_enabled=false"') < STAGE_SCRIPT.index(
        "verify_exact_tags stage-tag-verify.tfplan"
    )
    assert STAGE_SCRIPT.index("verify_exact_tags stage-tag-verify.tfplan") < STAGE_SCRIPT.index(
        '-var="stage_traffic_enabled=true"'
    )
    assert "trap rollback_stage ERR" in STAGE_SCRIPT
    assert 'terraform -chdir="$stack_dir" apply' in STAGE_SCRIPT
    assert '"DataClassification": "SYNTHETIC"' in STAGE_SCRIPT
    assert '"Project": "portfolio-dr"' in STAGE_SCRIPT
    assert '"RegionRole": $region_role' in STAGE_SCRIPT
    assert "portfolio-dr-aws-control-plane" in DEPLOY_WORKFLOW
    assert "portfolio-dr-aws-control-plane" in PLAN_WORKFLOW
    assert "cancel-in-progress: false" in DEPLOY_WORKFLOW


def test_api_stage_tag_on_create_is_encoded_region_and_required_tag_scoped() -> None:
    primary = DEPLOY.split('sid       = "TagPrimaryPortfolioHttpApiStageOnCreate"', 1)[1].split(
        "\n  }", 1
    )[0]
    secondary = DEPLOY.split('sid       = "TagSecondaryPortfolioHttpApiStageOnCreate"', 1)[1].split(
        "\n  }", 1
    )[0]
    for statement, region, role in (
        (primary, "${var.primary_region}", "active-a"),
        (secondary, "${var.secondary_region}", "active-b"),
    ):
        assert 'actions   = ["apigateway:POST"]' in statement
        assert statement.count(region) == 2
        assert "::/tags/arn:${data.aws_partition.current.partition}:apigateway:" in statement
        assert "::/apis/${statement.value}/stages/$default" in statement
        assert "%2Fapis" not in statement
        assert "/apis/*/stages" not in statement
        assert 'variable = "aws:RequestTag/Project"' in statement
        assert 'variable = "aws:RequestTag/DataClassification"' in statement
        assert 'values   = ["SYNTHETIC"]' in statement
        assert 'variable = "aws:RequestTag/RegionRole"' in statement
        assert f'values   = ["{role}"]' in statement
        assert 'test     = "ForAllValues:StringEquals"' in statement
        assert 'variable = "aws:TagKeys"' in statement
        assert 'values   = ["Project", "RegionRole", "DataClassification"]' in statement

    assert DEPLOY.count('for_each = var.primary_api_id == "" ? [] : [var.primary_api_id]') == 2
    assert DEPLOY.count('for_each = var.secondary_api_id == "" ? [] : [var.secondary_api_id]') == 2


def test_recovery_mutation_is_exact_table_region_and_synthetic_key_scoped() -> None:
    statement = RECOVERY.split('sid = "ApprovalGatedSyntheticProductionReconciliation"', 1)[
        1
    ].split("\n  }", 1)[0]
    for action in (
        "dynamodb:DeleteItem",
        "dynamodb:GetItem",
        "dynamodb:PutItem",
        "dynamodb:UpdateItem",
    ):
        assert action in statement
    assert statement.count(":table/${var.resource_prefix}-transactions") == 2
    assert "${var.primary_region}" in statement
    assert "${var.secondary_region}" in statement
    assert 'variable = "dynamodb:LeadingKeys"' in statement
    assert 'values   = ["txn-*"]' in statement
    assert "BatchWriteItem" not in statement


def test_isolated_target_actions_use_only_reviewed_prefix() -> None:
    statement = RECOVERY.split('sid = "ConfigureAndValidateIsolatedRecoveryTargets"', 1)[1].split(
        "\n  }", 1
    )[0]
    assert statement.count(":table/${var.resource_prefix}-recovery-*") == 2
    assert ":table/${var.resource_prefix}-transactions" not in statement
