import json
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import boto3
from botocore.exceptions import ClientError


DATA_REGION = os.getenv("DATA_REGION", "us-east-1")
USERS_TABLE = os.getenv("USERS_TABLE") or os.getenv("RECIPIENTS_TABLE", "carecall-users-dev")
APP_TIMEZONE = os.getenv("APP_TIMEZONE", "Asia/Seoul")
RECIPIENT_ID_ATTR = os.getenv("RECIPIENT_ID_ATTR", "recipientId")
CORS_ALLOW_ORIGIN = os.getenv("CORS_ALLOW_ORIGIN", "https://carecall-phi.vercel.app")
CORS_ALLOW_HEADERS = os.getenv(
    "CORS_ALLOW_HEADERS",
    "Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token",
)
CORS_ALLOW_METHODS = os.getenv(
    "CORS_ALLOW_METHODS",
    "GET,POST,PATCH,PUT,DELETE,OPTIONS",
)


dynamodb = boto3.resource("dynamodb", region_name=DATA_REGION)
users_table = dynamodb.Table(USERS_TABLE)


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


def extract_recipient_id(event):
    path_parameters = event.get("pathParameters") or {}
    for key in ("recipientId", "recipient_id", "user_id", "id"):
        value = str(path_parameters.get(key) or "").strip()
        if value:
            return value
    return ""


def validate_auto_call_enabled(value):
    if not isinstance(value, bool):
        raise ValueError("autoCallEnabled must be a boolean true or false.")
    return value


def validate_auto_call_time(value):
    text = str(value or "").strip()
    if not re.fullmatch(r"\d{2}:\d{2}", text):
        raise ValueError("autoCallTime must use HH:MM format.")

    hour, minute = [int(part) for part in text.split(":")]
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("autoCallTime must be a valid 24-hour time.")

    return text


def validate_age(value):
    try:
        age = int(value)
    except (TypeError, ValueError):
        raise ValueError("age must be an integer.")

    if age < 0:
        raise ValueError("age must be 0 or greater.")

    return age


def get_recipient(recipient_id):
    response = users_table.get_item(Key={RECIPIENT_ID_ATTR: recipient_id})
    return response.get("Item")


def build_update_parts(body):
    expression_names = {}
    expression_values = {}
    set_parts = []

    if "recipientName" in body:
        recipient_name = str(body.get("recipientName") or "").strip()
        if not recipient_name:
            raise ValueError("recipientName must not be empty.")
        expression_names["#recipientName"] = "recipientName"
        expression_values[":recipientName"] = recipient_name
        set_parts.append("#recipientName = :recipientName")

    if "age" in body:
        expression_names["#age"] = "age"
        expression_values[":age"] = validate_age(body.get("age"))
        set_parts.append("#age = :age")

    if "phoneNumber" in body:
        phone_number = str(body.get("phoneNumber") or "").strip()
        if not phone_number:
            raise ValueError("phoneNumber must not be empty.")
        expression_names["#phoneNumber"] = "phoneNumber"
        expression_values[":phoneNumber"] = phone_number
        set_parts.append("#phoneNumber = :phoneNumber")

    if "address" in body:
        expression_names["#address"] = "address"
        expression_values[":address"] = str(body.get("address") or "").strip()
        set_parts.append("#address = :address")

    if "memo" in body:
        expression_names["#memo"] = "memo"
        expression_values[":memo"] = str(body.get("memo") or "").strip()
        set_parts.append("#memo = :memo")

    if "autoCallTime" in body:
        expression_names["#autoCallTime"] = "autoCallTime"
        expression_values[":autoCallTime"] = validate_auto_call_time(body.get("autoCallTime"))
        set_parts.append("#autoCallTime = :autoCallTime")

    if "autoCallEnabled" in body:
        enabled = validate_auto_call_enabled(body.get("autoCallEnabled"))
        status = "ENABLED" if enabled else "DISABLED"

        expression_names["#autoCallEnabled"] = "autoCallEnabled"
        expression_values[":autoCallEnabled"] = enabled
        set_parts.append("#autoCallEnabled = :autoCallEnabled")

        expression_names["#auto_call_status"] = "auto_call_status"
        expression_values[":auto_call_status"] = status
        set_parts.append("#auto_call_status = :auto_call_status")

    if not set_parts:
        raise ValueError("At least one updatable field is required.")

    now = datetime.now(ZoneInfo(APP_TIMEZONE)).isoformat(timespec="seconds")
    expression_names["#updated_at"] = "updated_at"
    expression_values[":updated_at"] = now
    set_parts.append("#updated_at = :updated_at")

    return expression_names, expression_values, set_parts


def lambda_handler(event, context):
    try:
        event = event or {}
        method = event.get("httpMethod") or (event.get("requestContext", {}).get("http") or {}).get("method") or "PUT"

        if method == "OPTIONS":
            return json_response(200, {})
        if method != "PUT":
            return json_response(405, {"error": "Method not allowed"})

        recipient_id = extract_recipient_id(event)
        if not recipient_id:
            return json_response(400, {"error": "recipientId is required"})

        if not get_recipient(recipient_id):
            return json_response(404, {"error": "Recipient not found"})

        body = parse_body(event)
        expression_names, expression_values, set_parts = build_update_parts(body)

        users_table.update_item(
            Key={RECIPIENT_ID_ATTR: recipient_id},
            UpdateExpression="SET " + ", ".join(set_parts),
            ExpressionAttributeNames=expression_names,
            ExpressionAttributeValues=expression_values,
            ConditionExpression=f"attribute_exists({RECIPIENT_ID_ATTR})",
        )

        return json_response(200, {"status": "success"})
    except ClientError as error:
        print("updateRecipient DynamoDB error:", json.dumps(error.response, ensure_ascii=False, default=str))
        message = error.response.get("Error", {}).get("Message", str(error))
        if error.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return json_response(404, {"error": "Recipient not found"})
        return json_response(500, {"error": message})
    except (json.JSONDecodeError, ValueError) as error:
        return json_response(400, {"error": str(error)})
    except Exception as error:
        print("updateRecipient error:", str(error))
        return json_response(500, {"error": str(error)})
