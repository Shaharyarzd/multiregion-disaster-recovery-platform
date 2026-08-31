#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_root}"
"${repo_root}/scripts/build-lambda.sh"

for stack in bootstrap global region-a region-b; do
  terraform -chdir="terraform/stacks/${stack}" init -backend=false -input=false
  terraform -chdir="terraform/stacks/${stack}" validate
done
