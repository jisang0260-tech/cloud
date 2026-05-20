import json
import math
import os
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import boto3
from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError


DATA_REGION = os.getenv("DATA_REGION", "us-east-1")
CALL_HISTORY_TABLE = os.getenv("CALL_HISTORY_TABLE", "carecall-call-history-dev")
CALL_DATE_INDEX = os.getenv("CALL_DATE_INDEX", "ByDateIndex")
CALL_DATE_ATTR = os.getenv("CALL_DATE_ATTR", "createdAt")
APP_TIMEZONE = os.getenv("APP_TIMEZONE", "Asia/Seoul")

dynamodb = boto3.resource("dynamodb", region_name=DATA_REGION)
call_history_table = dynamodb.Table(CALL_HISTORY_TABLE)


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


def get_kst_today():
    return datetime.now(ZoneInfo(APP_TIMEZONE)).strftime("%Y-%m-%d")


def get_query_date(event):
    query = event.get("queryStringParameters") or {}
    return str(query.get("date") or get_kst_today()).strip()


def query_call_records(callTime):
    items = []
    exclusive_start_key = None

    while True:
        params = {
            "IndexName": CALL_DATE_INDEX,
            "KeyConditionExpression": Key(CALL_DATE_ATTR).eq(callTime),
        }
        if exclusive_start_key:
            params["ExclusiveStartKey"] = exclusive_start_key

        response = call_history_table.query(**params)
        items.extend(response.get("Items", []))
        exclusive_start_key = response.get("LastEvaluatedKey")

        if not exclusive_start_key:
            return items


def scan_call_records_by_date(callTime):
    # Fallback for prototype tables where the date GSI has not been created yet.
    items = []
    exclusive_start_key = None

    while True:
        params = {
            "FilterExpression": Attr(CALL_DATE_ATTR).eq(callTime),
        }
        if exclusive_start_key:
            params["ExclusiveStartKey"] = exclusive_start_key

        response = call_history_table.scan(**params)
        items.extend(response.get("Items", []))
        exclusive_start_key = response.get("LastEvaluatedKey")

        if not exclusive_start_key:
            return items


def list_today_records(callTime):
    try:
        return query_call_records(callTime)
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code")
        if code in {"ResourceNotFoundException", "ValidationException"}:
            print(f"Query failed, falling back to scan: {code}")
            return scan_call_records_by_date(callTime)
        raise


def normalize_call_status(value):
    status = str(value or "").strip()
    upper = status.upper()

    if status in {"응답", "미응답"}:
        return status
    if upper in {"COMPLETED", "ANSWERED", "SUCCESS"}:
        return "응답"
    if upper in {"NO_ANSWER", "UNANSWERED", "FAILED", "CANCELED", "CANCELLED", "BUSY"}:
        return "미응답"

    # The current frontend only has display config for 응답/미응답.
    return "미응답" if status else "미응답"


def normalize_risk_level(record):
    status = normalize_call_status(record.get("call_status") or record.get("status"))
    raw = str(record.get("risk_level") or record.get("riskLevel") or "").strip()
    key = raw.lower()

    if status == "미응답":
        return "미응답"
    if raw in {"정상", "주의", "위험", "미응답"}:
        return raw
    if key in {"normal", "low", "ok"}:
        return "정상"
    if key in {"caution", "warning", "medium"}:
        return "주의"
    if key in {"danger", "high", "critical", "emergency"}:
        return "위험"

    return "정상"


def get_first(record, *keys, default=None):
    for key in keys:
        value = record.get(key)
        if value not in (None, ""):
            return value
    return default


def duration_to_minutes(record):
    value = get_first(record, "duration", "duration_sec", "call_duration_sec")
    if value in (None, ""):
        return None

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None

    if "duration_sec" in record or "call_duration_sec" in record:
        return max(1, int(math.ceil(number / 60)))
    return int(number)


def to_frontend_record(record):
    call_status = normalize_call_status(record.get("call_status") or record.get("status"))
    risk_level = normalize_risk_level(record)
    call_time = get_first(record, "callTime", "started_at", "created_at", "updated_at")

    return {
        "contactId": str(get_first(record, "contactId", "contact_id", default=record.get("session_id") or "")),
        "recipientId": str(get_first(record, "recipientId", "user_id", default="")),
        "recipientName": str(get_first(record, "recipientName", "user_name_snapshot", "name", "target_name", default="")),
        "status": call_status,
        "duration": duration_to_minutes(record),
        "callTime": call_time,
        "sentiment": get_first(record, "sentiment", default=None),
        "sentimentScore": get_first(record, "sentimentScore", "risk_score", default=None),
        "riskLevel": risk_level,
        "riskReason": get_first(record, "riskReason", "risk_reason", default=""),
        "summary": get_first(record, "summary", "analysis_summary", default=""),
        "conversation": record.get("conversation") or record.get("transcript") or [],
        "createdAt": get_first(record, "createdAt", "created_at", "started_at", "updated_at", default=call_time),
    }


def risk_sort_key(record):
    order = {"위험": 0, "주의": 1, "미응답": 2, "정상": 3}
    return (
        order.get(record.get("riskLevel"), 9),
        str(record.get("callTime") or ""),
        str(record.get("recipientName") or ""),
    )


def build_today_status(callTime, records):
    frontend_records = [to_frontend_record(record) for record in records]
    frontend_records.sort(key=risk_sort_key)

    risk_counts = {"정상": 0, "주의": 0, "위험": 0, "미응답": 0}
    for record in frontend_records:
        level = record.get("riskLevel") or "미응답"
        if level not in risk_counts:
            level = "미응답"
        risk_counts[level] += 1

    return {
        "date": callTime,
        "total": len(frontend_records),
        "riskCounts": risk_counts,
        "records": frontend_records,
    }


def lambda_handler(event, context):
    try:
        event = event or {}
        method = event.get("httpMethod") or (event.get("requestContext", {}).get("http") or {}).get("method") or "GET"

        if method == "OPTIONS":
            return json_response(200, {})

        callTime = get_query_date(event)
        sts = boto3.client("sts")
        print("DEBUG caller:", json.dumps(sts.get_caller_identity(), ensure_ascii=False))
        print("DEBUG region:", DATA_REGION)
        print("DEBUG table:", CALL_HISTORY_TABLE)
        print("DEBUG date attr:", CALL_DATE_ATTR)
        print("DEBUG query date:", callTime)

        debug_scan = call_history_table.scan(Limit=50)
        print("DEBUG scan count:", debug_scan.get("Count"))
        print("DEBUG scan items:", json.dumps(to_json_safe(debug_scan.get("Items", [])), ensure_ascii=False))

        records = list_today_records(callTime)
        return json_response(200, build_today_status(callTime, records))
    except Exception as error:
        print("fetchTodayCallStatus error:", str(error))
        return json_response(500, {"error": str(error)})