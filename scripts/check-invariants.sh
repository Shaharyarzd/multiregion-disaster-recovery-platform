#!/usr/bin/env bash
set -euo pipefail

if rg -n "AdministratorAccess" terraform .github src; then
  echo "Broad AdministratorAccess is forbidden" >&2
  exit 1
fi
if rg -n "terraform[[:space:]]+apply" .github/workflows; then
  echo "Automated Terraform apply is forbidden" >&2
  exit 1
fi
if rg -n "AKIA[0-9A-Z]{16}" --glob '!scripts/check-invariants.sh' .; then
  echo "Potential AWS access key found" >&2
  exit 1
fi
python3 -c 'import json; from dr_platform.evidence import verify_report; r=json.load(open("evidence/recovery-report.json")); assert r["schema"]["version"] == "2.0.0" and verify_report(r)'

