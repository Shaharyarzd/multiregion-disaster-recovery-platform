#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
state_dir="${repo_root}/.drctl"
report_path="${repo_root}/evidence/recovery-report.json"
failure_time="$(python3 -c 'from datetime import datetime, UTC; print(datetime.now(UTC).isoformat())')"
recovery_point="$(python3 -c 'from datetime import datetime, UTC, timedelta; print((datetime.now(UTC)-timedelta(seconds=2)).isoformat())')"

rm -f "${state_dir}/incident.json" "${report_path}"
drctl --state-dir "${state_dir}" declare \
  --scenario logical-data-corruption \
  --failure-time "${failure_time}" \
  --affected-region us-east-1
drctl --state-dir "${state_dir}" recover-data --recovery-point "${recovery_point}"
drctl --state-dir "${state_dir}" validate-recovery
drctl --state-dir "${state_dir}" promote \
  --approve \
  --approver local-portfolio-owner \
  --reference LOCAL-DRILL-ONLY
drctl --state-dir "${state_dir}" failback \
  --phase start \
  --approve \
  --approver local-portfolio-owner \
  --reference LOCAL-FAILBACK-START \
  --both-regions-healthy \
  --data-consistent
drctl --state-dir "${state_dir}" failback \
  --phase complete \
  --approve \
  --approver local-portfolio-owner \
  --reference LOCAL-FAILBACK-COMPLETE \
  --both-regions-healthy \
  --data-consistent
drctl --state-dir "${state_dir}" report --output "${report_path}"
echo "Generated controller-derived local evidence at ${report_path}"
