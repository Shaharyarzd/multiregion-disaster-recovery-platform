from __future__ import annotations

import sys
from datetime import UTC, datetime
from types import SimpleNamespace

from dr_platform.aws_adapters import DynamoRecoveryAdapter, S3RecoveryAdapter


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def restore_table_to_point_in_time(self, **kwargs):
        self.calls.append(("restore", kwargs))
        return {"TableDescription": {"TableArn": "arn:isolated"}}

    def describe_table(self, **kwargs):
        self.calls.append(("describe", kwargs))
        return {"Table": {"TableName": kwargs["TableName"]}}

    def list_object_versions(self, **kwargs):
        self.calls.append(("versions", kwargs))
        return {"Versions": [{"VersionId": "v1"}]}

    def copy_object(self, **kwargs):
        self.calls.append(("copy", kwargs))


def install_fake_boto3(monkeypatch):
    clients: dict[str, FakeClient] = {}

    def client(service: str, **_kwargs):
        clients[service] = FakeClient()
        return clients[service]

    monkeypatch.setitem(sys.modules, "boto3", SimpleNamespace(client=client))
    return clients


def test_dynamo_adapter_restores_isolated_target(monkeypatch) -> None:
    clients = install_fake_boto3(monkeypatch)
    adapter = DynamoRecoveryAdapter("us-east-1")
    point = datetime(2026, 1, 1, tzinfo=UTC)
    assert adapter.restore_to_isolated_table("arn:source", "isolated", point) == "arn:isolated"
    assert adapter.describe("isolated")["TableName"] == "isolated"
    assert clients["dynamodb"].calls[0][1]["UseLatestRestorableTime"] is False


def test_s3_adapter_recovers_to_quarantine(monkeypatch) -> None:
    clients = install_fake_boto3(monkeypatch)
    adapter = S3RecoveryAdapter("us-west-2")
    assert adapter.versions("source", "supporting-data/")[0]["VersionId"] == "v1"
    adapter.recover_version("source", "key", "v1", "quarantine")
    copy = clients["s3"].calls[-1][1]
    assert copy["Bucket"] == "quarantine"
    assert copy["CopySource"]["VersionId"] == "v1"
