import json
import os
from decimal import Decimal

import boto3
from botocore.exceptions import ClientError


DATA_REGION = os.getenv("DATA_REGION", "us-east-1")
USERS_TABLE = os.getenv("USERS_TABLE") or os.getenv("RECIPIENTS_TABLE", "carecall-users-dev")

dynamodb = boto3.resource("dynamodb", region_name=DATA_REGION)
users_table = dynamodb.Table(USERS_TABLE)


def json_response(status_code, payload):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
            "Access-Control-Allow-Methods": "GET,OPTIONS",
        },
        "body": json.dumps(to_json_safe(payload), ensure_ascii=False),
    }


def to_json_safe(value):
    if isinstance(value, list):
        return [to_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {key: to_json_safe(item) for key, item in value.items()}
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    return value


def get_first(item, *keys, default=None):
    for key in keys:
        value = item.get(key)
        if value not in (None, ""):
            return value
    return default


def normalize_bool(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return False

    text = str(value).strip().lower()
    return text in {"true", "1", "yes", "y", "on", "enabled", "active"}


def normalize_risk_level(value):
    raw = str(value or "").strip()
    key = raw.lower()

    if raw in {"정상", "주의", "위험", "미응답"}:
        return raw
    if key in {"normal", "low", "ok"}:
        return "정상"
    if key in {"caution", "warning", "medium"}:
        return "주의"
    if key in {"danger", "high", "critical", "emergency"}:
        return "위험"
    if key in {"no_answer", "unanswered", "failed"}:
        return "미응답"

    return None


def format_phone_number(value):
    phone = str(value or "").strip()
    digits = "".join(ch for ch in phone if ch.isdigit())

    if phone.startswith("+82") and digits.startswith("82"):
        local = "0" + digits[2:]
    elif digits.startswith("82"):
        local = "0" + digits[2:]
    else:
        local = digits

    if len(local) == 11 and local.startswith("010"):
        return f"{local[:3]}-{local[3:7]}-{local[7:]}"

    return phone


def normalize_age(value):
    if value in (None, ""):
        return 0

    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def build_recipient(item):
    auto_call_status = get_first(item, "auto_call_status", "autoCallStatus")
    auto_call_enabled = get_first(item, "auto_call_enabled", "autoCallEnabled")

    if auto_call_enabled is None:
        auto_call_enabled = auto_call_status

    recipient_id = str(get_first(item, "recipientId", "recipient_id", "user_id", default=""))
    phone = get_first(item, "phoneNumber", "phone_number", "phone_e164", default="")

    return {
        "recipientId": recipient_id,
        "recipientName": str(get_first(item, "recipientName", "user_name", default="")),
        "phoneNumber": format_phone_number(phone),
        "age": normalize_age(get_first(item, "age", default=0)),
        "address": str(get_first(item, "address", "home_address", default="")),
        "assignedWorker": str(get_first(item, "assignedWorker", "assigned_worker", "worker_name", default="")),
        "lastRiskLevel": normalize_risk_level(get_first(item, "lastRiskLevel", "last_risk_level")),
        "autoCallEnabled": normalize_bool(auto_call_enabled),
        "autoCallTime": str(get_first(item, "autoCallTime", "preferred_time", "auto_call_time", default="09:00")),
        "memo": str(get_first(item, "memo", "notes", default="")),
        "photo": get_first(item, "photo", "photo_url", "profile_image_url", default=None),
    }


def scan_recipients():
    items = []
    exclusive_start_key = None

    while True:
        params = {}
        if exclusive_start_key:
            params["ExclusiveStartKey"] = exclusive_start_key

        response = users_table.scan(**params)
        items.extend(response.get("Items", []))
        exclusive_start_key = response.get("LastEvaluatedKey")

        if not exclusive_start_key:
            return items


def lambda_handler(event, context):
    try:
        event = event or {}
        method = event.get("httpMethod") or (event.get("requestContext", {}).get("http") or {}).get("method") or "GET"

        if method == "OPTIONS":
            return json_response(200, {})

        items = scan_recipients()
        recipients = [build_recipient(item) for item in items]
        recipients.sort(key=lambda item: item.get("name") or item.get("recipientId") or "")

        return json_response(200, {"recipients": recipients})
    except ClientError as error:
        print("fetchRecipients DynamoDB error:", json.dumps(error.response, ensure_ascii=False, default=str))
        return json_response(500, {"error": error.response.get("Error", {}).get("Message", str(error))})
    except Exception as error:
        print("fetchRecipients error:", str(error))
        return json_response(500, {"error": str(error)})