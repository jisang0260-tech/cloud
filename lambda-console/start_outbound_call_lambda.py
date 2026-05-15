import json
import os
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError


CONNECT_REGION = os.getenv("CONNECT_REGION", "ap-northeast-2")
CONNECT_INSTANCE_ID = os.getenv("CONNECT_INSTANCE_ID", "")
CONTACT_FLOW_ID = os.getenv("CONTACT_FLOW_ID", "")
SOURCE_PHONE_NUMBER = os.getenv("SOURCE_PHONE_NUMBER", "")
DEFAULT_DESTINATION_PHONE_NUMBER = os.getenv("DEFAULT_DESTINATION_PHONE_NUMBER", "")

DATA_REGION = os.getenv("DATA_REGION", "us-east-1")
USERS_TABLE = os.getenv("USERS_TABLE") or os.getenv("TARGETS_TABLE", "")
CALL_HISTORY_TABLE = os.getenv("CALL_HISTORY_TABLE") or os.getenv("SESSIONS_TABLE", "")
SCHEDULE_INDEX_NAME = os.getenv("SCHEDULE_INDEX_NAME") or os.getenv("AUTO_CALL_STATUS_INDEX", "AutoCallStatusIndex")
AUTO_CALL_STATUS_ATTR = os.getenv("AUTO_CALL_STATUS_ATTR", "auto_call_status")
AUTO_CALL_STATUS_VALUE = os.getenv("AUTO_CALL_STATUS_VALUE", "ENABLED")
PREFERRED_TIME_ATTR = os.getenv("PREFERRED_TIME_ATTR", "preferred_time")
APP_TIMEZONE = os.getenv("APP_TIMEZONE", "Asia/Seoul")
MAX_TARGETS = int(os.getenv("MAX_TARGETS", "50"))
SCHEDULE_TIME_OFFSET_MINUTES = int(os.getenv("SCHEDULE_TIME_OFFSET_MINUTES", "0"))
USE_SCAN_ONLY = str(os.getenv("USE_SCAN_ONLY", "false")).lower() == "true"
DEFAULT_OPENING_QUESTION = os.getenv(
    "DEFAULT_OPENING_QUESTION",
    os.getenv(
        "OPENING_PROMPT",
        "안녕하세요 경희대 복지센터입니다. 오늘 식사는 하셨나요?",
    ),
)
NEXT_OPENING_QUESTION_ATTR = os.getenv(
    "NEXT_OPENING_QUESTION_ATTR",
    "next_opening_question",
)

LEGACY_NEXT_OPENING_PROMPT_ATTR = os.getenv(
    "NEXT_OPENING_PROMPT_ATTR",
    "next_opening_prompt",
)

connect_client = boto3.client("connect", region_name=CONNECT_REGION)
dynamodb = boto3.resource("dynamodb", region_name=DATA_REGION)

KST = ZoneInfo("Asia/Seoul")



def json_response(status_code, payload):
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(payload, ensure_ascii=False),
    }


def normalize_phone_number(value):
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("Phone number is required.")

    if raw.startswith("+"):
        digits = re.sub(r"\D", "", raw)
        if not digits:
            raise ValueError(f"Invalid phone number: {value}")
        return f"+{digits}"

    digits = re.sub(r"\D", "", raw)
    if not digits:
        raise ValueError(f"Invalid phone number: {value}")

    if digits.startswith("82"):
        return f"+{digits}"

    if digits.startswith("0"):
        return f"+82{digits[1:]}"

    return f"+{digits}"


def stringify_dict(data):
    return {str(key): str(value) for key, value in (data or {}).items()}


def now_in_app_timezone():
    return datetime.now(ZoneInfo(APP_TIMEZONE))


def iso_utc(dt):
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def to_json_safe(value):
    if isinstance(value, list):
        return [to_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {key: to_json_safe(item) for key, item in value.items()}
    if isinstance(value, Decimal):
       return int(value) if value % 1 == 0 else float(value)
    return value


def safe_session_part(value):
    return re.sub(r"[^A-Za-z0-9_-]", "_", str(value or "unknown"))


def make_session_id(user_id, current_time):
    return f"sess_{current_time:%Y%m%d}_{safe_session_part(user_id)}_{current_time:%H%M}"


def get_phone_from_user(user):
    return (
        user.get("phone_e164")
        or user.get("phoneNumber")
        or user.get("phone_number")
        or user.get("destination_phone_number")
    )


def get_user_id(user):
    return str(user.get("recipientId") or user.get("recipient_id") or "").strip()


def get_user_name(user):
    return str(user.get("recipientName") or "").strip()


def get_preferred_time(user):
    return str(
        user.get(PREFERRED_TIME_ATTR)
        or user.get("preferred_time")
        or user.get("preferred_call_time")
        or user.get("autoCallTime")
        or ""
    ).strip()


def get_next_opening_question(user):
    question = str(
        user.get(NEXT_OPENING_QUESTION_ATTR)
        or user.get("next_opening_question")
        or user.get(LEGACY_NEXT_OPENING_PROMPT_ATTR)
        or user.get("next_opening_prompt")
        or DEFAULT_OPENING_QUESTION
    ).strip()
    return personalize_opening_question(question, get_user_name(user))


def personalize_opening_question(question, recipient_name):
    question = str(question or "").strip()
    recipient_name = str(recipient_name or "").strip()

    if not question or not recipient_name:
        return question
    if recipient_name in question or f"{recipient_name}님" in question:
        return question

    marker = "경희대 복지센터입니다."
    if marker in question:
        return question.replace(marker, f"{marker} {recipient_name}님", 1)

    return f"{recipient_name}님 {question}"


def is_auto_call_enabled(user):
    value = (
        user.get(AUTO_CALL_STATUS_ATTR)
        or user.get("auto_call_status")
        or user.get("autoCallStatus")
        or user.get("auto_call_enabled")
        or user.get("autoCallEnabled")
    )

    if isinstance(value, bool):
        return value

    return str(value or "").strip().lower() in {"enabled", "true", "yes", "y", "on", "1", "active"}


def matches_schedule(user, preferred_time):
    return is_auto_call_enabled(user) and get_preferred_time(user) == preferred_time


def get_users_table():
    if not USERS_TABLE:
        raise ValueError("Missing required env value: USERS_TABLE")
    return dynamodb.Table(USERS_TABLE)


def get_call_history_table():
    if not CALL_HISTORY_TABLE:
        return None
    return dynamodb.Table(CALL_HISTORY_TABLE)


def get_schedule_time(event, current_time):
    if event.get("preferred_time"):
        return str(event["preferred_time"]).strip()
    if event.get("preferred_call_time"):
        return str(event["preferred_call_time"]).strip()

    target_time = current_time + timedelta(minutes=SCHEDULE_TIME_OFFSET_MINUTES)
    return target_time.strftime("%H:%M")


def load_targets(event, current_time):
    destination = event.get("destination_phone_number")
    if not destination and str(event.get("use_default_destination") or "").lower() == "true":
        destination = DEFAULT_DESTINATION_PHONE_NUMBER

    if destination:
        phone_e164 = normalize_phone_number(destination)
        return [
            {
                "user_id": str(event.get("user_id") or "manual-test-user"),
                "name": str(event.get("name") or "manual-test"),
                "phone_e164": phone_e164,
                PREFERRED_TIME_ATTR: event.get("preferred_time") or event.get("preferred_call_time") or current_time.strftime("%H:%M"),
            }
        ]

    if event.get("user_id"):
        response = get_users_table().get_item(Key={"user_id": str(event["user_id"])})
        user = response.get("Item")
        if not user:
            raise ValueError(f"User not found: {event['user_id']}")
        return [user]

    preferred_time = get_schedule_time(event, current_time)
    table = get_users_table()

    if USE_SCAN_ONLY:
        return scan_targets_by_schedule(table, preferred_time)

    try:
        response = table.query(
            IndexName=SCHEDULE_INDEX_NAME,
            KeyConditionExpression=(
                Key(AUTO_CALL_STATUS_ATTR).eq(AUTO_CALL_STATUS_VALUE)
                & Key(PREFERRED_TIME_ATTR).eq(preferred_time)
            ),
            Limit=MAX_TARGETS,
        )
        return response.get("Items", [])
    except ClientError as error:
        error_code = error.response.get("Error", {}).get("Code")
        if error_code in {"ValidationException", "ResourceNotFoundException"}:
            print(f"Schedule index unavailable; falling back to scan: {error}")
            return scan_targets_by_schedule(table, preferred_time)
        raise


def scan_targets_by_schedule(table, preferred_time):
    items = []
    exclusive_start_key = None
    scanned_count = 0

    while len(items) < MAX_TARGETS:
        params = {"Limit": max(MAX_TARGETS, 100)}
        if exclusive_start_key:
            params["ExclusiveStartKey"] = exclusive_start_key

        response = table.scan(**params)
        scanned_count += response.get("ScannedCount", 0)
        items.extend(item for item in response.get("Items", []) if matches_schedule(item, preferred_time))
        exclusive_start_key = response.get("LastEvaluatedKey")

        if not exclusive_start_key:
            break

    print(
        "scan schedule result:",
        json.dumps(
            {
                "preferred_time": preferred_time,
                "scanned_count": scanned_count,
                "matched_count": len(items),
            },
            ensure_ascii=False,
        ),
    )

    return items[:MAX_TARGETS]


def put_pending_call_history(table, user, session_id, phone_e164, current_time):
    if table is None:
        return

    now = datetime.now(KST).isoformat(timespec="seconds")


    table.put_item(
        Item={
            "session_id": session_id,
            "recipientId": get_user_id(user),
            "recipientName": get_user_name(user),
            "phone_e164": phone_e164,
            "callTime":  current_time.strftime("%Y-%m-%d"),
            "status": "통화중",
            "duration": None,
            "sentiment": None,
            "sentimentScore": None,
            "riskLevel": "",
            "riskReason": "",
            "summary": "",
            "conversation": [],
            "preferred_time": get_preferred_time(user) or current_time.strftime("%H:%M"),
            "createdAt": current_time,
        },
        ConditionExpression="attribute_not_exists(session_id)",
    )


def update_call_history_with_contact_id(table, session_id, contact_id, current_time):
    if table is None:
        return
    
    now = datetime.now(KST).isoformat(timespec="seconds")


    table.update_item(
        Key={"session_id": session_id},
        UpdateExpression=(
            "SET contactId = :contact_id, "
            "#status = :status"
        ),
        ExpressionAttributeNames={
            "#status": "status",
        },
        ExpressionAttributeValues={
            ":contact_id": contact_id,
            ":status": "통화중",
        },
    )


def build_connect_attributes(event, user, session_id, phone_e164):
    attributes = stringify_dict(event.get("attributes"))
    attributes.update(
        stringify_dict(
            {
                "session_id": session_id,
                "recipientId": get_user_id(user),
                "recipientName": get_user_name(user),
                "phone_e164": phone_e164,
                "preferred_time": get_preferred_time(user),
                NEXT_OPENING_QUESTION_ATTR: get_next_opening_question(user),
            }
        )
    )
    return attributes


def start_call(event, user, current_time, call_history_table):
    instance_id = str(event.get("instance_id") or CONNECT_INSTANCE_ID).strip()
    contact_flow_id = str(event.get("contact_flow_id") or CONTACT_FLOW_ID).strip()
    source_phone_number = str(event.get("source_phone_number") or SOURCE_PHONE_NUMBER).strip()

    if not instance_id:
        raise ValueError("Missing required value: instance_id or CONNECT_INSTANCE_ID")
    if not contact_flow_id:
        raise ValueError("Missing required value: contact_flow_id or CONTACT_FLOW_ID")
    user_id = get_user_id(user)
    if not user_id:
        raise ValueError("User item is missing required value: user_id")

    phone_e164 = normalize_phone_number(get_phone_from_user(user))
    session_id = str(event.get("session_id") or make_session_id(user_id, current_time))
    attributes = build_connect_attributes(event, user, session_id, phone_e164)

    try:
        put_pending_call_history(call_history_table, user, session_id, phone_e164, current_time)
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return {
                "status": "skipped",
                "reason": "CallHistory session_id already exists.",
                "session_id": session_id,
                "user_id": user_id,
                "destination_phone_number": phone_e164,
            }
        raise

    request = {
        "InstanceId": instance_id,
        "ContactFlowId": contact_flow_id,
        "DestinationPhoneNumber": phone_e164,
        "ClientToken": str(event.get("client_token") or session_id),
        "Attributes": attributes,
    }

    if source_phone_number:
        request["SourcePhoneNumber"] = normalize_phone_number(source_phone_number)

    if event.get("name"):
        request["Name"] = str(event["name"])

    if event.get("description"):
        request["Description"] = str(event["description"])

    response = connect_client.start_outbound_voice_contact(**request)
    contact_id = response.get("ContactId")
    update_call_history_with_contact_id(call_history_table, session_id, contact_id, current_time)

    return {
        "status": "success",
        "contact_id": contact_id,
        "session_id": session_id,
        "user_id": user_id,
        "destination_phone_number": phone_e164,
        "attributes": attributes,
    }


def lambda_handler(event, context):
    try:
        event = event or {}
        print("scheduler event:", json.dumps(event, ensure_ascii=False))

        current_time = now_in_app_timezone()
        schedule_time = get_schedule_time(event, current_time)
        targets = load_targets(event, current_time)
        call_history_table = get_call_history_table()
        results = []

        for user in targets[:MAX_TARGETS]:
            try:
                results.append(start_call(event, user, current_time, call_history_table))
            except Exception as error:
                results.append(
                    {
                        "status": "error",
                        "recipientId": get_user_id(user),
                        "message": str(error),
                    }
                )

        return json_response(
            200,
            {
                "status": "success",
                "message": "Scheduler completed.",
                "schedule_time": schedule_time,
                "target_count": len(targets),
                "called_count": sum(1 for item in results if item.get("status") == "success"),
                "results": to_json_safe(results),
            },
        )
    except Exception as error:
        return json_response(500, {"status": "error", "message": str(error)})