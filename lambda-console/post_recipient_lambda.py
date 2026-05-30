import json
import os
import re
from datetime import datetime
from uuid import uuid4
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
    if value in (None, ""):
        raise ValueError("age is required.")

    try:
        age = int(value)
    except (TypeError, ValueError):
        raise ValueError("age must be an integer.")

    if age < 0:
        raise ValueError("age must be 0 or greater.")

    return age




def build_recipient_item(body):
    recipient_name = str(body.get("recipientName") or "").strip()
    age = validate_age(body.get("age"))
    phone_number = str(body.get("phoneNumber") or "").strip()
    address = str(body.get("address") or "").strip()
    memo = str(body.get("memo") or "").strip()
    auto_call_time = validate_auto_call_time(body.get("autoCallTime"))
    auto_call_enabled = validate_auto_call_enabled(body.get("autoCallEnabled"))

    if not recipient_name:
        raise ValueError("recipientName is required.")
    if not phone_number:
        raise ValueError("phoneNumber is required.")

    now = datetime.now(ZoneInfo(APP_TIMEZONE)).isoformat(timespec="seconds")
    recipient_id = f"r-{uuid4().hex[:12]}"
    auto_call_status = "ENABLED" if auto_call_enabled else "DISABLED"

    return {
        RECIPIENT_ID_ATTR: recipient_id,
        "recipientId": recipient_id,
        "recipientName": recipient_name,
        "age": age,
        "phoneNumber": phone_number,
        "address": address,
        "memo": memo,
        "autoCallTime": auto_call_time,
        "autoCallEnabled": auto_call_enabled,
    
    }


def lambda_handler(event, context):
    try:
        event = event or {}
        method = event.get("httpMethod") or (event.get("requestContext", {}).get("http") or {}).get("method") or "POST"

        if method == "OPTIONS":
            return json_response(200, {})
        if method != "POST":
            return json_response(405, {"status": "error", "message": "Method not allowed."})

        body = parse_body(event)
        item = build_recipient_item(body)

        users_table.put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(#recipient_id)",
            ExpressionAttributeNames={"#recipient_id": RECIPIENT_ID_ATTR},
        )

        return json_response(201, {"status": "success"})
    except ClientError as error:
        print("postRecipient DynamoDB error:", json.dumps(error.response, ensure_ascii=False, default=str))
        message = error.response.get("Error", {}).get("Message", str(error))
        return json_response(500, {"status": "error", "message": message})
    except (json.JSONDecodeError, ValueError) as error:
        return json_response(400, {"status": "error", "message": str(error)})
    except Exception as error:
        print("postRecipient error:", str(error))
        return json_response(500, {"status": "error", "message": str(error)})
