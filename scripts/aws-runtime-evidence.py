#!/usr/bin/env python3
"""Sign, archive, and exact-version verify AWS runtime evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import boto3

from dr_platform.aws_adapters import EvidenceArchiveAdapter, KmsEvidenceSigner
from dr_platform.evidence import build_report, verify_report
from dr_platform.integrity import canonical_json
from dr_platform.store import LocalIncidentStore

ACCOUNT = "293951765338"
REGION = "us-east-1"
BUCKET = "portfolio-dr-293951765338-evidence"
SIGNING_KEY = "arn:aws:kms:us-east-1:293951765338:key/de713abd-c143-49ac-8b9b-92d8d5aa2f8c"


def archive_and_verify(
    adapter: EvidenceArchiveAdapter,
    s3: Any,
    bucket: str,
    key: str,
    payload: bytes,
    retain_until: datetime,
) -> dict[str, Any]:
    version = adapter.archive(
        bucket,
        key,
        payload,
        retain_until=retain_until,
        expected_bucket_owner=ACCOUNT,
    )
    response = s3.get_object(
        Bucket=bucket,
        Key=key,
        VersionId=version,
        ExpectedBucketOwner=ACCOUNT,
    )
    read_back = response["Body"].read()
    retention = s3.get_object_retention(
        Bucket=bucket,
        Key=key,
        VersionId=version,
        ExpectedBucketOwner=ACCOUNT,
    )["Retention"]
    if read_back != payload or retention.get("Mode") != "GOVERNANCE":
        raise RuntimeError("Object Lock exact-version read-back failed")
    return {
        "key": key,
        "version_id": version,
        "sha256": hashlib.sha256(read_back).hexdigest(),
        "retention_mode": retention["Mode"],
        "retain_until": retention["RetainUntilDate"].astimezone(UTC).isoformat(),
        "read_back_equal": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    identity = boto3.client("sts").get_caller_identity()
    if identity["Account"] != ACCOUNT:
        raise RuntimeError("wrong AWS account")
    incident = LocalIncidentStore(args.work_dir).load()
    signer = KmsEvidenceSigner(REGION, SIGNING_KEY)
    report = build_report(incident, signer)
    if not verify_report(report, signer):
        raise RuntimeError("KMS signature verification failed before archive")
    report_bytes = canonical_json(report)
    unsigned_body = {key: value for key, value in report.items() if key != "integrity"}
    digest = hashlib.sha256(canonical_json(unsigned_body)).hexdigest()
    if digest != report["integrity"]["report_sha256"]:
        raise RuntimeError("canonical report digest mismatch")
    signature = report["integrity"]["signature"]
    signature_bytes = canonical_json(signature)
    digest_bytes = (digest + "\n").encode()
    adapter = EvidenceArchiveAdapter(REGION)
    s3 = boto3.client("s3", region_name=REGION)
    retain_until = datetime.now(UTC) + timedelta(days=7)
    prefix = f"evidence/{incident.scenario_id}/{incident.run_id}"
    archived = {
        "report": archive_and_verify(
            adapter, s3, BUCKET, f"{prefix}/recovery-report.json", report_bytes, retain_until
        ),
        "signature": archive_and_verify(
            adapter, s3, BUCKET, f"{prefix}/recovery-report.sig.json", signature_bytes, retain_until
        ),
        "digest": archive_and_verify(
            adapter, s3, BUCKET, f"{prefix}/recovery-report.sha256", digest_bytes, retain_until
        ),
    }
    downloaded = s3.get_object(
        Bucket=BUCKET,
        Key=archived["report"]["key"],
        VersionId=archived["report"]["version_id"],
        ExpectedBucketOwner=ACCOUNT,
    )["Body"].read()
    downloaded_report = json.loads(downloaded)
    if not verify_report(downloaded_report, signer):
        raise RuntimeError("KMS signature verification failed after exact-version read-back")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "recovery-report.json").write_bytes(report_bytes + b"\n")
    (args.output_dir / "recovery-report.sig.json").write_bytes(signature_bytes + b"\n")
    (args.output_dir / "recovery-report.sha256").write_bytes(digest_bytes)
    proof = {
        "schema": "com.portfolio.dr.archive-proof/1.0.0",
        "status": "PASS",
        "account": ACCOUNT,
        "bucket": BUCKET,
        "run_id": incident.run_id,
        "scenario_id": incident.scenario_id,
        "canonical_report_sha256": digest,
        "kms_signing_key": SIGNING_KEY,
        "kms_signature_verified_before_upload": True,
        "kms_signature_verified_after_read_back": True,
        "objects": archived,
    }
    (args.output_dir / "archive-proof.json").write_text(
        json.dumps(proof, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps({"event": "EVIDENCE_ARCHIVED", "status": "PASS", **proof}))


if __name__ == "__main__":
    main()
