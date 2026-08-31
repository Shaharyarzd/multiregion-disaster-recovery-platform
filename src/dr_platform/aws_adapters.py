"""Narrow AWS recovery interfaces; imports are lazy so local tests need no AWS SDK."""

from __future__ import annotations

from datetime import datetime
from typing import Any


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
