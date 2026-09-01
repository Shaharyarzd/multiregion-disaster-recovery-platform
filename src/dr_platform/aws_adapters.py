"""Narrow AWS recovery interfaces; imports are lazy so local tests need no AWS SDK."""

from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from dr_platform.evidence import EvidenceVerifier, verify_report
from dr_platform.integrity import canonical_json
from dr_platform.observability import NAMESPACE, MetricDatum
from dr_platform.types import Transaction, iso, normalize_utc

TARGET_NAME = re.compile(r"^[A-Za-z0-9_.-]{3,255}$")


def recovery_target_name(source_name: str, run_id: str) -> str:
    suffix = re.sub(r"[^A-Za-z0-9_.-]", "-", run_id)[:20]
    separator = "-recovery-"
    source = re.sub(r"[^A-Za-z0-9_.-]", "-", source_name)
    project = source.removesuffix("-transactions")
    project = project[: 255 - len(separator) - len(suffix)]
    name = f"{project}{separator}{suffix}"
    if not TARGET_NAME.fullmatch(name):
        raise ValueError("invalid deterministic recovery target name")
    return name


@dataclass(frozen=True)
class RestoredTableProof:
    table_active: bool
    encryption_verified: bool
    pitr_enabled: bool
    tags_verified: bool
    ttl_verified: bool
    stream_verified: bool
    no_replicas: bool
    deletion_protection: bool

    @property
    def ready_for_validation(self) -> bool:
        return all(vars(self).values())

    def as_dict(self) -> dict[str, bool]:
        return {**vars(self), "ready_for_validation": self.ready_for_validation}


class DynamoRecoveryAdapter:
    def __init__(self, region: str) -> None:
        import boto3  # type: ignore[import-not-found]

        self.client = boto3.client("dynamodb", region_name=region)

    def restore_to_isolated_table(
        self,
        source_arn: str,
        target_name: str,
        recovery_point: datetime,
        *,
        kms_key_arn: str,
    ) -> str:
        if not TARGET_NAME.fullmatch(target_name) or "-recovery-" not in target_name:
            raise ValueError("PITR target must be an isolated recovery-prefixed table")
        response = self.client.restore_table_to_point_in_time(
            SourceTableArn=source_arn,
            TargetTableName=target_name,
            RestoreDateTime=normalize_utc(recovery_point),
            UseLatestRestorableTime=False,
            BillingModeOverride="PAY_PER_REQUEST",
            SSESpecificationOverride={
                "Enabled": True,
                "SSEType": "KMS",
                "KMSMasterKeyId": kms_key_arn,
            },
        )
        return str(response["TableDescription"]["TableArn"])

    def describe(self, table_name: str) -> dict[str, Any]:
        return dict(self.client.describe_table(TableName=table_name)["Table"])

    def configure_restored_table(
        self,
        table_name: str,
        *,
        expected_kms_key_arn: str,
        tags: dict[str, str],
        ttl_attribute: str | None = None,
        stream_view_type: str | None = None,
    ) -> RestoredTableProof:
        table = self.describe(table_name)
        arn = str(table["TableArn"])
        update: dict[str, Any] = {"TableName": table_name, "DeletionProtectionEnabled": True}
        if stream_view_type:
            update["StreamSpecification"] = {
                "StreamEnabled": True,
                "StreamViewType": stream_view_type,
            }
        self.client.update_table(**update)
        self.client.update_continuous_backups(
            TableName=table_name,
            PointInTimeRecoverySpecification={"PointInTimeRecoveryEnabled": True},
        )
        if ttl_attribute:
            self.client.update_time_to_live(
                TableName=table_name,
                TimeToLiveSpecification={"Enabled": True, "AttributeName": ttl_attribute},
            )
        if tags:
            self.client.tag_resource(
                ResourceArn=arn,
                Tags=[{"Key": key, "Value": value} for key, value in sorted(tags.items())],
            )
        refreshed = self.describe(table_name)
        backups = self.client.describe_continuous_backups(TableName=table_name)
        actual_tags = self.client.list_tags_of_resource(ResourceArn=arn).get("Tags", [])
        ttl = self.client.describe_time_to_live(TableName=table_name).get(
            "TimeToLiveDescription", {}
        )
        tag_map = {item["Key"]: item["Value"] for item in actual_tags}
        return RestoredTableProof(
            table_active=refreshed.get("TableStatus") == "ACTIVE",
            encryption_verified=refreshed.get("SSEDescription", {}).get("KMSMasterKeyArn")
            == expected_kms_key_arn,
            pitr_enabled=backups.get("ContinuousBackupsDescription", {})
            .get("PointInTimeRecoveryDescription", {})
            .get("PointInTimeRecoveryStatus")
            == "ENABLED",
            tags_verified=all(tag_map.get(key) == value for key, value in tags.items()),
            ttl_verified=(
                ttl.get("TimeToLiveStatus") == "ENABLED"
                and ttl.get("AttributeName") == ttl_attribute
                if ttl_attribute
                else ttl.get("TimeToLiveStatus", "DISABLED") in {"DISABLED", "DISABLING"}
            ),
            stream_verified=(
                refreshed.get("StreamSpecification", {}).get("StreamEnabled") is True
                and refreshed.get("StreamSpecification", {}).get("StreamViewType")
                == stream_view_type
                if stream_view_type
                else not refreshed.get("StreamSpecification", {}).get("StreamEnabled", False)
            ),
            no_replicas=not bool(refreshed.get("Replicas")),
            deletion_protection=refreshed.get("DeletionProtectionEnabled") is True,
        )


class DynamoReplayTarget:
    """Low-level DynamoDB conditional target for idempotent bounded replay."""

    def __init__(self, region: str, table_name: str) -> None:
        import boto3

        self.client = boto3.client("dynamodb", region_name=region)
        self.table_name = table_name

    def get(self, transaction_id: str) -> Transaction | None:
        response = self.client.get_item(
            TableName=self.table_name,
            Key={"transaction_id": {"S": transaction_id}},
            ConsistentRead=True,
        )
        item = response.get("Item")
        if not item:
            return None
        return Transaction(
            transaction_id=item["transaction_id"]["S"],
            timestamp=datetime.fromisoformat(item["timestamp"]["S"].replace("Z", "+00:00")),
            region=item["region"]["S"],
            amount_cents=int(item["amount_cents"]["N"]),
            payload=item["payload"]["S"],
        )

    def put_if_absent(self, transaction: Transaction) -> bool:
        try:
            self.client.put_item(
                TableName=self.table_name,
                Item={
                    "transaction_id": {"S": transaction.transaction_id},
                    "timestamp": {"S": str(iso(transaction.timestamp))},
                    "region": {"S": transaction.region},
                    "amount_cents": {"N": str(transaction.amount_cents)},
                    "payload": {"S": transaction.payload},
                },
                ConditionExpression="attribute_not_exists(transaction_id)",
            )
        except Exception as error:
            response = getattr(error, "response", {})
            code = response.get("Error", {}).get("Code") if isinstance(response, dict) else None
            if code == "ConditionalCheckFailedException":
                return False
            raise
        return True


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

    @staticmethod
    def object_key(scenario_id: str, run_id: str) -> str:
        safe = re.compile(r"^[A-Za-z0-9_.-]+$")
        if not safe.fullmatch(scenario_id) or not safe.fullmatch(run_id):
            raise ValueError("unsafe evidence object identifier")
        return f"evidence/{scenario_id}/{run_id}/recovery-report.json"

    def archive(
        self,
        bucket: str,
        key: str,
        report_bytes: bytes,
        *,
        retain_until: datetime | None = None,
        expected_bucket_owner: str | None = None,
    ) -> str:
        checksum = base64.b64encode(hashlib.sha256(report_bytes).digest()).decode()
        request: dict[str, Any] = dict(
            Bucket=bucket,
            Key=key,
            Body=report_bytes,
            ContentType="application/json",
            ChecksumAlgorithm="SHA256",
            ChecksumSHA256=checksum,
            ObjectLockMode="GOVERNANCE",
            ObjectLockRetainUntilDate=normalize_utc(
                retain_until or datetime.now(UTC) + timedelta(days=7)
            ),
        )
        if expected_bucket_owner:
            request["ExpectedBucketOwner"] = expected_bucket_owner
        response = self.client.put_object(**request)
        return str(response["VersionId"])

    def verify_archived_report(
        self,
        bucket: str,
        key: str,
        version_id: str,
        verifier: EvidenceVerifier,
    ) -> bool:
        response = self.client.get_object(Bucket=bucket, Key=key, VersionId=version_id)
        raw = response["Body"].read()
        retention = self.client.get_object_retention(
            Bucket=bucket, Key=key, VersionId=version_id
        ).get("Retention", {})
        import json

        report = json.loads(raw)
        canonical_match = raw.rstrip(b"\n") == canonical_json(report)
        return bool(
            canonical_match
            and retention.get("Mode") == "GOVERNANCE"
            and retention.get("RetainUntilDate")
            and verify_report(report, verifier)
        )
