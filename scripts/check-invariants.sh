#!/usr/bin/env bash
set -euo pipefail

if rg -n "AdministratorAccess" terraform .github src; then
  echo "Broad AdministratorAccess is forbidden" >&2
  exit 1
fi
if rg -n "terraform[[:space:]]+apply" .github/workflows --glob '!aws-deploy.yml'; then
  echo "Terraform apply is restricted to the controlled deployment workflow" >&2
  exit 1
fi
rg -q "DEPLOY_DISPOSABLE_MILESTONE_2" .github/workflows/aws-deploy.yml
rg -q "environment: aws-deployment" .github/workflows/aws-deploy.yml
if rg -n "AKIA[0-9A-Z]{16}" --glob '!scripts/check-invariants.sh' .; then
  echo "Potential AWS access key found" >&2
  exit 1
fi
python3 -c 'import json; from dr_platform.evidence import verify_report; r=json.load(open("evidence/recovery-report.json")); assert r["schema"]["version"] == "2.1.0" and verify_report(r)'
python3 -c 'from pathlib import Path; from dr_platform.iam_contract import load_contract, validate_contract; assert not validate_contract(load_contract(Path("config/iam-action-paths.json")))'
