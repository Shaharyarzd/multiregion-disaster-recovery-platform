#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
artifact_dir="${repo_root}/dist"
stage_dir="$(mktemp -d)"
trap 'rm -rf "${stage_dir}"' EXIT
mkdir -p "${artifact_dir}"
cp -R "${repo_root}/src/dr_platform" "${stage_dir}/dr_platform"
(cd "${stage_dir}" && zip -qr "${artifact_dir}/dr-app.zip" dr_platform)
echo "Built ${artifact_dir}/dr-app.zip"

