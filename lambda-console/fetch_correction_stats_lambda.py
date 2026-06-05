import json
import os
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import boto3
from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError


DATA_REGION = os.getenv("DATA_REGION", "us-east-1")
CALL_CORRECTIONS_TABLE = (
    os.getenv("CALL_CORRECTIONS_TABLE")
    or os.getenv("CORRECTIONS_TABLE")
    or "carecall-correction-dev"
)
CORRECTIONS_BY_DATE_INDEX = os.getenv("CORRECTIONS_BY_DATE_INDEX", "CorrectionsByDateIndex")
CORRECTED_DATE_ATTR = os.getenv("CORRECTED_DATE_ATTR", "correctedDate")
CORRECTED_AT_ATTR = os.getenv("CORRECTED_AT_ATTR", "correctedAt")
APP_TIMEZONE = os.getenv("APP_TIMEZONE", "Asia/Seoul")
CORS_ALLOW_ORIGIN = os.getenv("CORS_ALLOW_ORIGIN", "https://d29gc62aprgiim.cloudfront.net")
CORS_ALLOW_HEADERS = os.getenv(
    "CORS_ALLOW_HEADERS",
    "Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token",
)
CORS_ALLOW_METHODS = os.getenv(
    "CORS_ALLOW_METHODS",
    "GET,POST,PATCH,PUT,DELETE,OPTIONS",
)

RISK_LEVEL_ORDER = ("위험", "주의", "정상")

dynamodb = boto3.resource("dynamodb", region_name=DATA_REGION)
corrections_table = dynamodb.Table(CALL_CORRECTIONS_TABLE)


def json_response(status_code, payload):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": CORS_ALLOW_ORIGIN,
            "Access-Control-Allow-Headers": CORS_ALLOW_HEADERS,
            "Access-Control-Allow-Methods": CORS_ALLOW_METHODS,
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


def get_query_day(event):
    query = event.get("queryStringParameters") or {}
    day = str(query.get("day") or get_kst_today()).strip()
    datetime.strptime(day, "%Y-%m-%d")
    return day


def query_corrections_by_day(day):
    items = []
    exclusive_start_key = None

    while True:
        params = {
            "IndexName": CORRECTIONS_BY_DATE_INDEX,
            "KeyConditionExpression": Key(CORRECTED_DATE_ATTR).eq(day),
        }
        if exclusive_start_key:
            params["ExclusiveStartKey"] = exclusive_start_key

        response = corrections_table.query(**params)
        items.extend(response.get("Items", []))
        exclusive_start_key = response.get("LastEvaluatedKey")

        if not exclusive_start_key:
            return items


def scan_corrections_by_day(day):
    items = []
    exclusive_start_key = None

    while True:
        params = {
            "FilterExpression": Attr(CORRECTED_DATE_ATTR).eq(day) | Attr(CORRECTED_AT_ATTR).begins_with(day),
        }
        if exclusive_start_key:
            params["ExclusiveStartKey"] = exclusive_start_key

        response = corrections_table.scan(**params)
        items.extend(response.get("Items", []))
        exclusive_start_key = response.get("LastEvaluatedKey")

        if not exclusive_start_key:
            return items


def list_corrections_by_day(day):
    try:
        return query_corrections_by_day(day)
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code")
        if code in {"ResourceNotFoundException", "ValidationException"}:
            print(f"Corrections date query failed, falling back to scan: {code}")
            return scan_corrections_by_day(day)
        raise


def normalize_risk_level(value):
    risk_level = str(value or "").strip()
    if risk_level in RISK_LEVEL_ORDER:
        return risk_level

    lowered = risk_level.lower()
    if lowered in {"danger", "high", "critical", "emergency"}:
        return "위험"
    if lowered in {"caution", "warning", "medium"}:
        return "주의"
    if lowered in {"normal", "low", "ok"}:
        return "정상"
    return ""


def build_correction_stats(records):
    counts = {level: 0 for level in RISK_LEVEL_ORDER}

    for record in records:
        level = normalize_risk_level(record.get("correctedRiskLevel") or record.get("riskLevel"))
        if level:
            counts[level] += 1

    return {
        "totalCorrections": len(records),
        "correctionByLevel": counts,
    }


def lambda_handler(event, context):
    try:
        event = event or {}
        method = event.get("httpMethod") or (event.get("requestContext", {}).get("http") or {}).get("method") or "GET"

        if method == "OPTIONS":
            return json_response(200, {})
        if method != "GET":
            return json_response(405, {"error": "Method not allowed"})

        day = get_query_day(event)
        records = list_corrections_by_day(day)
        return json_response(200, build_correction_stats(records))
    except ValueError as error:
        return json_response(400, {"error": str(error)})
    except ClientError as error:
        print("fetchCorrectionStats DynamoDB error:", json.dumps(error.response, ensure_ascii=False, default=str))
        message = error.response.get("Error", {}).get("Message", str(error))
        return json_response(500, {"error": message})
    except Exception as error:
        print("fetchCorrectionStats error:", str(error))
        return json_response(500, {"error": str(error)})
