import json
import os
import re
from datetime import datetime
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
CALL_HISTORY_TABLE = os.getenv("CALL_HISTORY_TABLE") or os.getenv("CALL_RECORDS_TABLE", "carecall-call-history-dev")
CONTACTID_INDEX = os.getenv("CONTACTID_INDEX", "ContactIdIndex")
CONTACTID_ATTR = os.getenv("CONTACTID_ATTR", "contactId")
APP_TIMEZONE = os.getenv("APP_TIMEZONE", "Asia/Seoul")
CORS_ALLOW_ORIGIN = os.getenv("CORS_ALLOW_ORIGIN", "https://carecall-phi.vercel.app")
CORS_ALLOW_HEADERS = os.getenv(
    "CORS_ALLOW_HEADERS",
    "Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token",
)
CORS_ALLOW_METHODS = os.getenv(
    "CORS_ALLOW_METHODS",
    "GET,POST,PATCH,PUT,DELETE,OPTIONS",
)

ALLOWED_RISK_LEVELS = {"정상", "주의", "위험"}

dynamodb = boto3.resource("dynamodb", region_name=DATA_REGION)
call_history_table = dynamodb.Table(CALL_HISTORY_TABLE)


def json_response(status_code, payload):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": CORS_ALLOW_ORIGIN,
            "Access-Control-Allow-Headers": CORS_ALLOW_HEADERS,
            "Access-Control-Allow-Methods": CORS_ALLOW_METHODS,
        },
        "body": json.dumps(payload, ensure_ascii=False),
    }


def parse_body(event):
    body = event.get("body")
    if body is None:
        return {}
    if isinstance(body, dict):
        return body
    return json.loads(body)


def extract_contact_id(event):
    path_parameters = event.get("pathParameters") or {}
    for key in ("contactId", "contact_id", "id"):
        value = str(path_parameters.get(key) or "").strip()
        if value:
            return value

    query = event.get("queryStringParameters") or {}
    for key in ("contactId", "contact_id", "id"):
        value = str(query.get(key) or "").strip()
        if value:
            return value

    path = str(event.get("path") or event.get("rawPath") or "")
    match = re.search(r"/calls/([^/?#]+)/correction$", path)
    if match:
        return str(match.group(1)).strip()

    return ""


def validate_risk_level(value, field_name):
    risk_level = str(value or "").strip()
    if not risk_level:
        raise ValueError(f"{field_name} is required.")
    if risk_level not in ALLOWED_RISK_LEVELS:
        raise ValueError(f"{field_name} must be one of 정상, 주의, 위험.")
    return risk_level


def query_call_history_by_contact(contact_id):
    items = []
    exclusive_start_key = None

    while True:
        params = {
            "IndexName": CONTACTID_INDEX,
            "KeyConditionExpression": Key(CONTACTID_ATTR).eq(contact_id),
        }
        if exclusive_start_key:
            params["ExclusiveStartKey"] = exclusive_start_key

        response = call_history_table.query(**params)
        items.extend(response.get("Items", []))
        exclusive_start_key = response.get("LastEvaluatedKey")

        if not exclusive_start_key:
            return items


def scan_call_history_by_contact(contact_id):
    items = []
    exclusive_start_key = None

    while True:
        params = {"FilterExpression": Attr(CONTACTID_ATTR).eq(contact_id)}
        if exclusive_start_key:
            params["ExclusiveStartKey"] = exclusive_start_key

        response = call_history_table.scan(**params)
        items.extend(response.get("Items", []))
        exclusive_start_key = response.get("LastEvaluatedKey")

        if not exclusive_start_key:
            return items


def list_call_history_by_contact(contact_id):
    try:
        return query_call_history_by_contact(contact_id)
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code")
        if code in {"ResourceNotFoundException", "ValidationException"}:
            print(f"ContactId query failed, falling back to scan: {code}")
            return scan_call_history_by_contact(contact_id)
        raise


def choose_latest_record(records):
    def sort_key(record):
        return str(
            record.get("callTime")
            or record.get("started_at")
            or record.get("start_time")
            or record.get("updated_at")
            or record.get("created_at")
            or record.get("createdAt")
            or ""
        )

    if not records:
        return None
    return sorted(records, key=sort_key, reverse=True)[0]


def build_correction_item(contact_id, body):
    if not contact_id:
        raise ValueError("contactId is required.")

    original_risk_level = validate_risk_level(body.get("originalRiskLevel"), "originalRiskLevel")
    corrected_risk_level = validate_risk_level(body.get("correctedRiskLevel"), "correctedRiskLevel")
    reason = str(body.get("reason") or "").strip()
    corrected_at = datetime.now(ZoneInfo(APP_TIMEZONE)).isoformat(timespec="seconds")
    corrected_date = corrected_at[:10]

    return {
        "contactId": contact_id,
        "originalRiskLevel": original_risk_level,
        "correctedRiskLevel": corrected_risk_level,
        "reason": reason,
        "correctedDate": corrected_date,
        "correctedAt": corrected_at,
    }


def apply_correction(item):
    records = list_call_history_by_contact(item["contactId"])
    record = choose_latest_record(records)
    if not record:
        return None

    session_id = str(record.get("session_id") or "").strip()
    if not session_id:
        raise ValueError("Call history record is missing session_id.")

    corrected_at = item["correctedAt"]
    dynamodb.meta.client.transact_write_items(
        TransactItems=[
            {
                "Put": {
                    "TableName": CALL_CORRECTIONS_TABLE,
                    "Item": {
                        "contactId": {"S": item["contactId"]},
                        "originalRiskLevel": {"S": item["originalRiskLevel"]},
                        "correctedRiskLevel": {"S": item["correctedRiskLevel"]},
                        "reason": {"S": item["reason"]},
                        "correctedDate": {"S": item["correctedDate"]},
                        "correctedAt": {"S": corrected_at},
                    },
                }
            },
            {
                "Update": {
                    "TableName": CALL_HISTORY_TABLE,
                    "Key": {"session_id": {"S": session_id}},
                    "UpdateExpression": "SET #riskLevel = :riskLevel, #riskReason = :riskReason, #updated_at = :updated_at",
                    "ExpressionAttributeNames": {
                        "#riskLevel": "riskLevel",
                        "#riskReason": "riskReason",
                        "#updated_at": "updated_at",
                    },
                    "ExpressionAttributeValues": {
                        ":riskLevel": {"S": item["correctedRiskLevel"]},
                        ":riskReason": {"S": item["reason"]},
                        ":updated_at": {"S": corrected_at},
                    },
                    "ConditionExpression": "attribute_exists(session_id)",
                }
            },
        ]
    )

    return session_id


def lambda_handler(event, context):
    try:
        event = event or {}
        method = event.get("httpMethod") or (event.get("requestContext", {}).get("http") or {}).get("method") or "POST"

        if method == "OPTIONS":
            return json_response(200, {})
        if method != "POST":
            return json_response(405, {"error": "Method not allowed"})

        contact_id = extract_contact_id(event)
        body = parse_body(event)
        item = build_correction_item(contact_id, body)
        session_id = apply_correction(item)
        if not session_id:
            return json_response(404, {"error": "Call history not found"})

        return json_response(200, item)
    except ClientError as error:
        print("postCallCorrection DynamoDB error:", json.dumps(error.response, ensure_ascii=False, default=str))
        message = error.response.get("Error", {}).get("Message", str(error))
        return json_response(500, {"error": message})
    except (json.JSONDecodeError, ValueError) as error:
        return json_response(400, {"error": str(error)})
    except Exception as error:
        print("postCallCorrection error:", str(error))
        return json_response(500, {"error": str(error)})
