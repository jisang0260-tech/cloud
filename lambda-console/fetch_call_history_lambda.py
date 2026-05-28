import json
import math
import os
import re
from decimal import Decimal
from urllib.parse import unquote

import boto3
from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError


DATA_REGION = os.getenv("DATA_REGION", "us-east-1")
S3_REGION = os.getenv("S3_REGION", "ap-northeast-2")
CALL_HISTORY_TABLE = os.getenv("CALL_HISTORY_TABLE") or os.getenv("CALL_RECORDS_TABLE", "carecall-call-history-dev")
RECIPIENT_NAME_INDEX = os.getenv("RECIPIENT_NAME_INDEX", "RecipientNameIndex")
RECIPIENT_NAME_ATTR = os.getenv("RECIPIENT_NAME_ATTR", "recipientName")
USE_SCAN_ONLY = os.getenv("USE_SCAN_ONLY", "false").lower() == "true"
MAX_HISTORY_ITEMS = int(os.getenv("MAX_HISTORY_ITEMS", "100"))
TRANSCRIPT_BUCKET = os.getenv("TRANSCRIPT_BUCKET", "")
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
s3 = boto3.client("s3", region_name=S3_REGION)
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


def get_first(record, *keys, default=None):
    for key in keys:
        value = record.get(key)
        if value not in (None, ""):
            return value
    return default


def decode_request_value(value):
    return unquote(str(value or "").strip())


def extract_recipient_name(event):
    path_parameters = event.get("pathParameters") or {}
    for key in ("recipientName", "recipient_name", "name", "recipientId", "recipient_id", "user_id", "id"):
        value = path_parameters.get(key)
        if value:
            return decode_request_value(value)

    query = event.get("queryStringParameters") or {}
    for key in ("recipientName", "recipient_name", "name", "recipientId", "recipient_id", "user_id", "id"):
        value = query.get(key)
        if value:
            return decode_request_value(value)

    path = event.get("path") or event.get("rawPath") or ""
    match = re.search(r"/calls/history/([^/?#]+)$", path)
    if match:
        return decode_request_value(match.group(1))

    return ""


def query_history_by_recipient_name(recipient_name):
    items = []
    exclusive_start_key = None

    while True:
        params = {
            "IndexName": RECIPIENT_NAME_INDEX,
            "KeyConditionExpression": Key(RECIPIENT_NAME_ATTR).eq(recipient_name),
            "ScanIndexForward": False,
        }
        if exclusive_start_key:
            params["ExclusiveStartKey"] = exclusive_start_key

        response = call_history_table.query(**params)
        items.extend(response.get("Items", []))
        exclusive_start_key = response.get("LastEvaluatedKey")

        if not exclusive_start_key:
            return items


def scan_history_by_recipient_name(recipient_name):
    items = []
    exclusive_start_key = None
    attrs = []
    for attr in (RECIPIENT_NAME_ATTR, "recipientName", "recipient_name", "name", "target_name", "user_name_snapshot"):
        if attr not in attrs:
            attrs.append(attr)

    filter_expression = None
    for attr in attrs:
        condition = Attr(attr).eq(recipient_name)
        filter_expression = condition if filter_expression is None else filter_expression | condition

    while True:
        params = {"FilterExpression": filter_expression}
        if exclusive_start_key:
            params["ExclusiveStartKey"] = exclusive_start_key

        response = call_history_table.scan(**params)
        items.extend(response.get("Items", []))
        exclusive_start_key = response.get("LastEvaluatedKey")

        if not exclusive_start_key:
            return items


def list_call_history(recipient_name):
    if USE_SCAN_ONLY:
        return scan_history_by_recipient_name(recipient_name)

    try:
        return query_history_by_recipient_name(recipient_name)
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code")
        if code in {"ResourceNotFoundException", "ValidationException"}:
            print(f"History query failed, falling back to scan: {code}")
            return scan_history_by_recipient_name(recipient_name)
        raise


def normalize_call_status(value):
    status = str(value or "").strip()
    upper = status.upper()

    if status in {"응답", "미응답"}:
        return status
    if upper in {"COMPLETED", "ANSWERED", "SUCCESS", "IN_PROGRESS"}:
        return "응답"
    if upper in {"NO_ANSWER", "UNANSWERED", "FAILED", "CANCELED", "CANCELLED", "BUSY", "MISSED"}:
        return "미응답"
    return "미응답" if not status else status


def normalize_risk_level(record):
    status = normalize_call_status(record.get("call_status") or record.get("status"))
    raw = str(record.get("riskLevel") or record.get("risk_level") or "").strip()
    key = raw.lower()

    if status == "미응답":
        return "미응답"
    if raw in {"정상", "주의", "위험", "미응답"}:
        return raw
    if key in {"normal", "low", "ok", "safe"}:
        return "정상"
    if key in {"caution", "warning", "medium"}:
        return "주의"
    if key in {"danger", "high", "critical", "emergency"}:
        return "위험"
    return "정상"


def normalize_sentiment(value):
    raw = str(value or "").strip()
    key = raw.lower()

    if raw in {"POSITIVE", "NEGATIVE", "NEUTRAL"}:
        return raw
    if key in {"positive", "pos", "긍정"}:
        return "POSITIVE"
    if key in {"negative", "neg", "부정"}:
        return "NEGATIVE"
    if key in {"neutral", "neu", "중립"}:
        return "NEUTRAL"
    return None


def duration_to_minutes(record):
    value = get_first(record, "duration", "duration_min", "duration_sec", "call_duration_sec")
    if value in (None, ""):
        return None

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None

    if any(key in record for key in ("duration_sec", "call_duration_sec")):
        return max(1, int(math.ceil(number / 60)))
    return int(number)


def normalize_speaker(value):
    raw = str(value or "").strip()
    key = raw.lower()

    if raw == "AI" or key in {"ai", "assistant", "bot", "agent", "system"}:
        return "AI"
    if raw == "대상자" or key in {"user", "customer", "target", "caller", "participant"}:
        return "대상자"
    return raw or "대상자"


def normalize_conversation(items):
    conversation = []
    if not isinstance(items, list):
        return conversation

    for item in items:
        if isinstance(item, str):
            text = item.strip()
            if text:
                conversation.append({"speaker": "대상자", "text": text})
            continue

        if not isinstance(item, dict):
            continue

        text = str(get_first(item, "text", "content", "transcript", "utterance", default="")).strip()
        if not text:
            continue

        conversation.append(
            {
                "speaker": normalize_speaker(get_first(item, "speaker", "role", "channel", default="대상자")),
                "text": text,
            }
        )

    return conversation


def conversation_from_answers(record):
    answer_items = []

    for key, value in record.items():
        if not re.fullmatch(r"answer_\d+", str(key)):
            continue
        text = str(value or "").strip()
        if text:
            answer_items.append((int(str(key).split("_")[1]), text))

    answer_items.sort(key=lambda item: item[0])
    return [{"speaker": "대상자", "text": text} for _, text in answer_items]


def parse_transcribe_json(payload):
    if isinstance(payload, dict):
        for key in ("conversation", "transcript", "messages", "utterances"):
            conversation = normalize_conversation(payload.get(key))
            if conversation:
                return conversation

        transcript = None
        results = payload.get("results")
        if isinstance(results, dict):
            transcripts = results.get("transcripts") or []
            if transcripts:
                transcript = transcripts[0].get("transcript")
        if transcript:
            return [{"speaker": "대상자", "text": str(transcript).strip()}]

    return normalize_conversation(payload)


def load_transcript_conversation(record):
    existing = normalize_conversation(record.get("conversation"))
    if existing:
        return existing

    existing = normalize_conversation(record.get("transcript"))
    if existing:
        return existing

    bucket = get_first(record, "transcript_s3_bucket", "transcriptS3Bucket", default=TRANSCRIPT_BUCKET)
    key = get_first(record, "transcript_s3_key", "transcriptS3Key")
    if bucket and key:
        try:
            response = s3.get_object(Bucket=str(bucket), Key=str(key))
            payload = json.loads(response["Body"].read().decode("utf-8"))
            conversation = parse_transcribe_json(payload)
            if conversation:
                return conversation
        except Exception as error:
            print(f"Transcript load failed for s3://{bucket}/{key}: {error}")

    return conversation_from_answers(record)


def to_frontend_record(record):
    call_status = normalize_call_status(record.get("call_status") or record.get("status"))
    risk_level = normalize_risk_level(record)
    call_time = get_first(record, "callTime", "started_at", "start_time", "created_at", "createdAt", "updated_at")
    created_at = get_first(record, "createdAt", "created_at", "started_at", "start_time", "updated_at", default=call_time)

    return {
        "contactId": str(get_first(record, "contactId", "contact_id", default=record.get("session_id") or "")),
        "recipientId": str(get_first(record, "recipientId", "recipient_id", "user_id", default="")),
        "recipientName": str(get_first(record, "recipientName", "recipient_name", "user_name_snapshot", "name", "target_name", default="")),
        "status": call_status,
        "duration": duration_to_minutes(record),
        "callTime": call_time or created_at,
        "sentiment": normalize_sentiment(get_first(record, "sentiment", "sentiment_label")),
        "sentimentScore": get_first(record, "sentimentScore", "sentiment_score", "risk_score", default=None),
        "riskLevel": risk_level,
        "riskReason": str(get_first(record, "riskReason", "risk_reason", default="")),
        "summary": str(get_first(record, "summary", "analysis_summary", default="")),
        "conversation": [] if risk_level == "미응답" else load_transcript_conversation(record),
        "createdAt": created_at or call_time,
    }


def sort_history(records):
    def key(record):
        return str(record.get("createdAt") or record.get("callTime") or "")

    return sorted(records, key=key, reverse=True)


def lambda_handler(event, context):
    try:
        event = event or {}
        method = event.get("httpMethod") or (event.get("requestContext", {}).get("http") or {}).get("method") or "GET"

        if method == "OPTIONS":
            return json_response(200, {})

        recipient_name = extract_recipient_name(event)
        if not recipient_name:
            return json_response(400, {"error": "recipientName is required"})

        records = list_call_history(recipient_name)
        history = [to_frontend_record(record) for record in records]
        history = sort_history(history)[:MAX_HISTORY_ITEMS]

        return json_response(200, {"recipientName": recipient_name, "history": history})
    except Exception as error:
        print("fetchCallHistory error:", str(error))
        return json_response(500, {"error": str(error)})
