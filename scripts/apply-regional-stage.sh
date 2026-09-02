#!/usr/bin/env bash
set -Eeuo pipefail

stack_dir=${1:?stack directory required}
region_role=${2:?region role required}
provision_stages=${3:?provision_stages required}
table_name=${4:?table name required}
table_arn=${5:?table ARN required}
stage_address='module.service.aws_apigatewayv2_stage.default[0]'

common_vars=(
  -var="table_name=$table_name"
  -var="table_arn=$table_arn"
)

plan_has_delete() {
  terraform -chdir="$stack_dir" show -json "$1" |
    jq -e 'any(.resource_changes[]?; (.change.actions | index("delete")) != null)' >/dev/null
}

apply_guarded_plan() {
  local plan_file=$1
  if plan_has_delete "$plan_file"; then
    echo "Refusing regional plan containing destroy or replacement actions" >&2
    return 1
  fi
  terraform -chdir="$stack_dir" apply -input=false -auto-approve "$plan_file"
}

rollback_stage() {
  local original_status=$?
  trap - ERR
  echo "Stage tagging failed; deleting the inert stage before failing" >&2
  terraform -chdir="$stack_dir" plan -input=false -out=stage-rollback.tfplan \
    "${common_vars[@]}" \
    -var="create_stage=false" \
    -var="stage_tags_enabled=false" \
    -var="stage_traffic_enabled=false"
  terraform -chdir="$stack_dir" show -json stage-rollback.tfplan |
    jq -e --arg address "$stage_address" '
      [.resource_changes[]? | select(.change.actions != ["no-op"])] as $changes
      | ($changes | length) == 1
        and $changes[0].address == $address
        and $changes[0].change.actions == ["delete"]
    ' >/dev/null
  terraform -chdir="$stack_dir" apply -input=false -auto-approve stage-rollback.tfplan
  if terraform -chdir="$stack_dir" state list | grep -Fqx "$stage_address"; then
    echo "Stage rollback did not remove the Terraform state entry" >&2
    exit 1
  fi
  exit "$original_status"
}

verify_exact_tags() {
  local plan_file=$1
  terraform -chdir="$stack_dir" show -json "$plan_file" |
    jq -e --arg address "$stage_address" --arg region_role "$region_role" '
      first(
        .. | objects
        | select(.address? == $address)
        | .values.tags
      ) == {
        "DataClassification": "SYNTHETIC",
        "Project": "portfolio-dr",
        "RegionRole": $region_role
      }
    ' >/dev/null
}

terraform -chdir="$stack_dir" init -input=false

if [[ "$provision_stages" != "true" ]]; then
  terraform -chdir="$stack_dir" plan -input=false -out=regional.tfplan \
    "${common_vars[@]}" \
    -var="create_stage=false" \
    -var="stage_tags_enabled=false" \
    -var="stage_traffic_enabled=false"
  apply_guarded_plan regional.tfplan
  exit 0
fi

# Phase 1: create an inert stage. It has no direct tags, no deployment, and cannot serve traffic.
terraform -chdir="$stack_dir" plan -input=false -out=stage-create.tfplan \
  "${common_vars[@]}" \
  -var="create_stage=true" \
  -var="stage_tags_enabled=false" \
  -var="stage_traffic_enabled=false"
apply_guarded_plan stage-create.tfplan

# Any failure before exact tag verification removes the inert stage and its state entry.
trap rollback_stage ERR

# Phase 2: apply direct tags idempotently while traffic remains disabled.
terraform -chdir="$stack_dir" plan -input=false -out=stage-tag.tfplan \
  "${common_vars[@]}" \
  -var="create_stage=true" \
  -var="stage_tags_enabled=true" \
  -var="stage_traffic_enabled=false"
apply_guarded_plan stage-tag.tfplan

# A normal no-diff plan refreshes remote state. Exit code 0 is required before tag inspection.
trap - ERR
set +e
terraform -chdir="$stack_dir" plan -input=false -detailed-exitcode -out=stage-tag-verify.tfplan \
  "${common_vars[@]}" \
  -var="create_stage=true" \
  -var="stage_tags_enabled=true" \
  -var="stage_traffic_enabled=false"
verify_status=$?
set -e
trap rollback_stage ERR
[[ "$verify_status" -eq 0 ]]
verify_exact_tags stage-tag-verify.tfplan
trap - ERR

# Phase 3: only a verified, tagged stage may become traffic eligible.
terraform -chdir="$stack_dir" plan -input=false -out=stage-enable.tfplan \
  "${common_vars[@]}" \
  -var="create_stage=true" \
  -var="stage_tags_enabled=true" \
  -var="stage_traffic_enabled=true"
apply_guarded_plan stage-enable.tfplan

set +e
terraform -chdir="$stack_dir" plan -input=false -detailed-exitcode -out=stage-final-verify.tfplan \
  "${common_vars[@]}" \
  -var="create_stage=true" \
  -var="stage_tags_enabled=true" \
  -var="stage_traffic_enabled=true"
final_status=$?
set -e
[[ "$final_status" -eq 0 ]]
verify_exact_tags stage-final-verify.tfplan
