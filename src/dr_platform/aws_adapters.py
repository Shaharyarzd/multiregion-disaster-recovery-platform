"""Narrow AWS recovery interfaces; imports are lazy so local tests need no AWS SDK."""

from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any

from dr_platform.observability import NAMESPACE, MetricDatum


class DynamoRecoveryAdapter:
    def __init__(self, region: str) -> None:
        import boto3  # type: ignore[import-not-found]

        self.client = boto3.client("dynamodb", region_name=region)

    def restore_to_isolated_table(
        self, source_arn: str, target_name: str, recovery_point: datetime
    ) -> str:
        response = self.client.restore_table_to_point_in_time(
            SourceTableArn=source_arn,
            TargetTableName=target_name,
            RestoreDateTime=recovery_point,
            UseLatestRestorableTime=False,
        )
        return str(response["TableDescription"]["TableArn"])

    def describe(self, table_name: str) -> dict[str, Any]:
        return dict(self.client.describe_table(TableName=table_name)["Table"])


class S3RecoveryAdapter:
    def __init__(self, region: str) -> None:
        import boto3

        self.client = boto3.client("s3", region_name=region)

    def versions(self, bucket: str, prefix: str) -> list[dict[str, Any]]:
        response = self.client.list_object_versions(Bucket=bucket, Prefix=prefix)
        return list(response.get("Versions", []))

    def recover_version(
        self, source_bucket: str, key: str, version_id: str, quarantine_bucket: str
    ) -> None:
        self.client.copy_object(
            Bucket=quarantine_bucket,
            Key=key,
            CopySource={"Bucket": source_bucket, "Key": key, "VersionId": version_id},
            MetadataDirective="COPY",
        )


class CloudWatchEvidenceEmitter:
    def __init__(self, region: str) -> None:
        import boto3

        self.client = boto3.client("cloudwatch", region_name=region)

    def emit(self, metrics: list[MetricDatum]) -> None:
        if not metrics:
            return
        self.client.put_metric_data(
            Namespace=NAMESPACE,
            MetricData=[metric.as_cloudwatch() for metric in metrics],
        )


class KmsEvidenceSigner:
    def __init__(self, region: str, key_id: str) -> None:
        import boto3

        self.client = boto3.client("kms", region_name=region)
        self.key_id = key_id

    def sign_digest(self, digest_hex: str) -> dict[str, str]:
        response = self.client.sign(
            KeyId=self.key_id,
            Message=bytes.fromhex(digest_hex),
            MessageType="DIGEST",
            SigningAlgorithm="ECDSA_SHA_256",
        )
        return {
            "algorithm": "ECDSA_SHA_256",
            "key_id": self.key_id,
            "signature_base64": base64.b64encode(response["Signature"]).decode(),
        }

    def verify_digest(self, digest_hex: str, signature: dict[str, str]) -> bool:
        response = self.client.verify(
            KeyId=signature["key_id"],
            Message=bytes.fromhex(digest_hex),
            MessageType="DIGEST",
            Signature=base64.b64decode(signature["signature_base64"]),
            SigningAlgorithm=signature["algorithm"],
        )
        return bool(response["SignatureValid"])


class EvidenceArchiveAdapter:
    def __init__(self, region: str) -> None:
        import boto3

        self.client = boto3.client("s3", region_name=region)

    def archive(self, bucket: str, key: str, report_bytes: bytes) -> str:
        checksum = base64.b64encode(hashlib.sha256(report_bytes).digest()).decode()
        response = self.client.put_object(
            Bucket=bucket,
            Key=key,
            Body=report_bytes,
            ContentType="application/json",
            ChecksumAlgorithm="SHA256",
            ChecksumSHA256=checksum,
            ObjectLockMode="GOVERNANCE",
            ObjectLockRetainUntilDate=datetime.now(UTC) + timedelta(days=7),
        )
        return str(response["VersionId"])
