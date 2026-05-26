import json
import os
import re

import boto3
from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError


DATA_REGION = os.getenv("DATA_REGION", "us-east-1")
S3_REGION = os.getenv("S3_REGION", "ap-northeast-2")
CALL_HISTORY_TABLE = os.getenv("CALL_HISTORY_TABLE") or os.getenv("CALL_RECORDS_TABLE", "carecall-call-history-dev")
CONTACTID_INDEX = os.getenv("CONTACTID_INDEX", "ContactIdIndex")
CONTACTID_ATTR = os.getenv("CONTACTID_ATTR", "contactId")
RECORDINGS_BUCKET = os.getenv("RECORDINGS_BUCKET", "")
RECORDING_SEARCH_PREFIX = os.getenv("RECORDING_SEARCH_PREFIX", "connect/")
PRESIGNED_URL_EXPIRES = int(os.getenv("PRESIGNED_URL_EXPIRES", "300"))
UPDATE_FOUND_KEY = os.getenv("UPDATE_FOUND_KEY", "true").lower() == "true"

AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".ogg", ".webm", ".amr", ".flac", ".mp4"}

dynamodb = boto3.resource("dynamodb", region_name=DATA_REGION)
s3 = boto3.client("s3", region_name=S3_REGION)
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
        "body": json.dumps(payload, ensure_ascii=False),
    }


def get_first(record, *keys, default=None):
    for key in keys:
        value = (record or {}).get(key)
        if value not in (None, ""):
            return value
    return default


def to_bool(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def extract_request_values(event):
    path_parameters = event.get("pathParameters") or {}
    query = event.get("queryStringParameters") or {}

    session_id = str(
        path_parameters.get("session_id")
        or path_parameters.get("sessionId")
        or query.get("session_id")
        or query.get("sessionId")
        or ""
    ).strip()
    contact_id = str(
        path_parameters.get("contactId")
        or path_parameters.get("contact_id")
        or query.get("contactId")
        or query.get("contact_id")
        or ""
    ).strip()
    download = to_bool(query.get("download"))

    if not session_id:
        path = event.get("path") or event.get("rawPath") or ""
        match = re.search(r"/recordings/([^/?#]+)/url$", path)
        if match:
            session_id = match.group(1).strip()

    return session_id, contact_id, download


def get_call_history_by_session(session_id):
    response = call_history_table.get_item(Key={"session_id": session_id})
    return response.get("Item") or None


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
            get_first(
                record,
                "callTime",
                "started_at",
                "start_time",
                "updated_at",
                "created_at",
                "createdAt",
                default="",
            )
        )

    if not records:
        return None
    return sorted(records, key=sort_key, reverse=True)[0]


def resolve_recording_bucket(record):
    return str(
        get_first(
            record,
            "recording_s3_bucket",
            "recordingS3Bucket",
            "audio_s3_bucket",
            "audioS3Bucket",
            default=RECORDINGS_BUCKET,
        )
        or ""
    ).strip()


def resolve_recording_key(record):
    return str(
        get_first(
            record,
            "recording_s3_key",
            "recordingS3Key",
            "audio_s3_key",
            "audioS3Key",
            default="",
        )
        or ""
    ).strip()


def build_search_prefixes(record):
    prefixes = []
    base_prefix = RECORDING_SEARCH_PREFIX.strip("/")
    if base_prefix:
        prefixes.append(f"{base_prefix}/")

    timestamp = str(
        get_first(
            record,
            "callTime",
            "started_at",
            "start_time",
            "created_at",
            "createdAt",
            default="",
        )
        or ""
    ).strip()
    match = re.match(r"(\d{4})-(\d{2})-(\d{2})", timestamp)
    if match and base_prefix:
        year, month, day = match.groups()
        prefixes.insert(0, f"{base_prefix}/{year}/{month}/{day}/")

    if not prefixes:
        prefixes.append("")

    return list(dict.fromkeys(prefixes))


def find_recording_key_in_s3(bucket, contact_id, record):
    if not bucket or not contact_id:
        return ""

    candidates = []
    paginator = s3.get_paginator("list_objects_v2")

    for prefix in build_search_prefixes(record):
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for item in page.get("Contents", []):
                key = str(item.get("Key") or "")
                if not key:
                    continue
                filename = os.path.basename(key)
                extension = os.path.splitext(filename)[1].lower()
                if extension not in AUDIO_EXTENSIONS:
                    continue
                if contact_id not in filename and contact_id not in key:
                    continue
                candidates.append(key)

        if candidates:
            break

    candidates.sort(reverse=True)
    return candidates[0] if candidates else ""


def update_cached_recording_key(session_id, bucket, key):
    if not UPDATE_FOUND_KEY or not session_id or not bucket or not key:
        return

    try:
        call_history_table.update_item(
            Key={"session_id": session_id},
            UpdateExpression=(
                "SET recording_s3_bucket = :recording_bucket, "
                "recording_s3_key = :recording_key, "
                "audio_s3_key = :recording_key"
            ),
            ExpressionAttributeValues={
                ":recording_bucket": bucket,
                ":recording_key": key,
            },
        )
    except Exception as error:
        print(f"Recording key cache update failed for session_id={session_id}: {error}")


def build_download_filename(record, key):
    extension = os.path.splitext(str(key or ""))[1] or ".wav"
    recipient_name = re.sub(
        r"[^0-9A-Za-z가-힣_-]+",
        "_",
        str(get_first(record, "recipientName", "recipient_name", "name", default="carecall") or "carecall").strip(),
    ).strip("_")
    call_date = str(get_first(record, "createdAt", "created_at", default="") or "").strip()

    if recipient_name and call_date:
        return f"{recipient_name}-{call_date}{extension}"
    if recipient_name:
        return f"{recipient_name}{extension}"
    return f"carecall-recording{extension}"


def generate_presigned_url(bucket, key, download, record):
    params = {"Bucket": bucket, "Key": key}
    if download:
        params["ResponseContentDisposition"] = f'attachment; filename="{build_download_filename(record, key)}"'

    return s3.generate_presigned_url(
        ClientMethod="get_object",
        Params=params,
        ExpiresIn=PRESIGNED_URL_EXPIRES,
    )


def lambda_handler(event, context):
    try:
        event = event or {}
        method = event.get("httpMethod") or (event.get("requestContext", {}).get("http") or {}).get("method") or "GET"

        if method == "OPTIONS":
            return json_response(200, {})

        session_id, contact_id, download = extract_request_values(event)
        if not session_id and not contact_id:
            return json_response(400, {"error": "sessionId or contactId is required"})

        record = get_call_history_by_session(session_id) if session_id else None
        if not record and contact_id:
            record = choose_latest_record(list_call_history_by_contact(contact_id))

        if not record:
            return json_response(404, {"error": "Call history not found"})

        session_id = str(record.get("session_id") or session_id or "").strip()
        contact_id = str(get_first(record, "contactId", "contact_id", default=contact_id) or "").strip()

        bucket = resolve_recording_bucket(record)
        key = resolve_recording_key(record)

        if not key and bucket:
            key = find_recording_key_in_s3(bucket, contact_id, record)
            if key:
                update_cached_recording_key(session_id, bucket, key)

        if not bucket:
            return json_response(500, {"error": "Recordings bucket is not configured"})

        if not key:
            return json_response(404, {"error": "Recording file key not found"})

        url = generate_presigned_url(bucket, key, download, record)

        return json_response(
            200,
            {
                "sessionId": session_id,
                "contactId": contact_id,
                "bucket": bucket,
                "key": key,
                "expiresIn": PRESIGNED_URL_EXPIRES,
                "download": download,
                "url": url,
            },
        )
    except Exception as error:
        print("getRecordingPresignedUrl error:", str(error))
        return json_response(500, {"error": str(error)})
