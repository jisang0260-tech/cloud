import json
import os
import re
from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

import boto3
from botocore.exceptions import ClientError


DATA_REGION = os.getenv("DATA_REGION", "us-east-1")
CALL_CORRECTIONS_TABLE = (
    os.getenv("CALL_CORRECTIONS_TABLE")
    or os.getenv("CORRECTIONS_TABLE")
    or "carecall-correction-dev"
)
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


def build_correction_item(contact_id, body):
    if not contact_id:
        raise ValueError("contactId is required.")

    original_risk_level = validate_risk_level(body.get("originalRiskLevel"), "originalRiskLevel")
    corrected_risk_level = validate_risk_level(body.get("correctedRiskLevel"), "correctedRiskLevel")
    reason = str(body.get("reason") or "").strip()
    corrected_at = datetime.now(ZoneInfo(APP_TIMEZONE)).isoformat(timespec="seconds")

    return {
        "correctionId": str(uuid4()),
        "contactId": contact_id,
        "originalRiskLevel": original_risk_level,
        "correctedRiskLevel": corrected_risk_level,
        "reason": reason,
        "correctedAt": corrected_at,
    }


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

        corrections_table.put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(correctionId)",
        )

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
