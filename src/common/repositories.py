from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

import boto3
from boto3.dynamodb.conditions import Key

from common.config import settings


def _to_python(value: Any) -> Any:
    if isinstance(value, list):
        return [_to_python(item) for item in value]
    if isinstance(value, dict):
        return {key: _to_python(item) for key, item in value.items()}
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    return value


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class CareCallRepository:
    def __init__(self) -> None:
        dynamodb = boto3.resource("dynamodb")
        self.targets_table = dynamodb.Table(settings.targets_table)
        self.sessions_table = dynamodb.Table(settings.sessions_table)

    def get_target(self, user_id: str) -> Optional[Dict[str, Any]]:
        response = self.targets_table.get_item(Key={"user_id": user_id})
        item = response.get("Item")
        return _to_python(item) if item else None

    def list_enabled_targets(self) -> List[Dict[str, Any]]:
        response = self.targets_table.query(
            IndexName="AutoCallStatusIndex",
            KeyConditionExpression=Key("auto_call_status").eq("ENABLED"),
        )
        items = response.get("Items", [])

        while "LastEvaluatedKey" in response:
            response = self.targets_table.query(
                IndexName="AutoCallStatusIndex",
                KeyConditionExpression=Key("auto_call_status").eq("ENABLED"),
                ExclusiveStartKey=response["LastEvaluatedKey"],
            )
            items.extend(response.get("Items", []))

        return [_to_python(item) for item in items]

    def list_sessions_by_date(self, call_date: str) -> List[Dict[str, Any]]:
        response = self.sessions_table.query(
            IndexName="CallDateIndex",
            KeyConditionExpression=Key("call_date").eq(call_date),
        )
        items = response.get("Items", [])

        while "LastEvaluatedKey" in response:
            response = self.sessions_table.query(
                IndexName="CallDateIndex",
                KeyConditionExpression=Key("call_date").eq(call_date),
                ExclusiveStartKey=response["LastEvaluatedKey"],
            )
            items.extend(response.get("Items", []))

        return [_to_python(item) for item in items]

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        response = self.sessions_table.get_item(Key={"session_id": session_id})
        item = response.get("Item")
        return _to_python(item) if item else None

    def update_auto_call(self, user_id: str, enabled: bool) -> Dict[str, Any]:
        auto_call_status = "ENABLED" if enabled else "DISABLED"
        updated_at = _utc_now()
        response = self.targets_table.update_item(
            Key={"user_id": user_id},
            UpdateExpression="SET auto_call_enabled = :enabled, auto_call_status = :status, updated_at = :updated_at",
            ExpressionAttributeValues={
                ":enabled": enabled,
                ":status": auto_call_status,
                ":updated_at": updated_at,
            },
            ReturnValues="ALL_NEW",
        )
        return _to_python(response["Attributes"])

    def create_session(self, session: Dict[str, Any]) -> None:
        self.sessions_table.put_item(Item=session)

    def mark_session_contact_started(self, session_id: str, contact_id: str) -> None:
        self.sessions_table.update_item(
            Key={"session_id": session_id},
            UpdateExpression="SET contact_id = :contact_id, call_status = :status, updated_at = :updated_at",
            ExpressionAttributeValues={
                ":contact_id": contact_id,
                ":status": "INITIATED",
                ":updated_at": _utc_now(),
            },
        )

    def mark_session_failed(self, session_id: str, reason: str) -> None:
        self.sessions_table.update_item(
            Key={"session_id": session_id},
            UpdateExpression="SET call_status = :status, failure_reason = :reason, updated_at = :updated_at",
            ExpressionAttributeValues={
                ":status": "FAILED",
                ":reason": reason[:500],
                ":updated_at": _utc_now(),
            },
        )

