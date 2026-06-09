import json
import os
import re

import boto3


S3_REGION = os.getenv("S3_REGION", "ap-northeast-2")
RECORDINGS_BUCKET = os.getenv("RECORDINGS_BUCKET", "")
# Buckets the caller is allowed to read from. RECORDINGS_BUCKET is always
# included; set ALLOWED_BUCKETS (comma-separated) to permit extra buckets.
ALLOWED_BUCKETS = {b.strip() for b in os.getenv("ALLOWED_BUCKETS", "").split(",") if b.strip()}
# Optional key prefix the recording key must start with (e.g. "recordings/").
RECORDING_KEY_PREFIX = os.getenv("RECORDING_KEY_PREFIX", "").strip()
PRESIGNED_URL_EXPIRES = int(os.getenv("PRESIGNED_URL_EXPIRES", "300"))
INTERNAL_ERROR_MESSAGE = "내부 서버 오류가 발생했습니다."
CORS_ALLOW_ORIGIN = os.getenv("CORS_ALLOW_ORIGIN", "https://d29gc62aprgiim.cloudfront.net")
CORS_ALLOW_HEADERS = os.getenv(
    "CORS_ALLOW_HEADERS",
    "Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token",
)
CORS_ALLOW_METHODS = os.getenv(
    "CORS_ALLOW_METHODS",
    "GET,POST,PATCH,PUT,DELETE,OPTIONS",
)
s3 = boto3.client("s3", region_name=S3_REGION)


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


def get_first(record, *keys, default=None):
    for key in keys:
        value = (record or {}).get(key)
        if value not in (None, ""):
            return value
    return default


def to_bool(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def allowed_buckets():
    buckets = set(ALLOWED_BUCKETS)
    if RECORDINGS_BUCKET:
        buckets.add(RECORDINGS_BUCKET)
    return buckets


def is_valid_recording_key(key):
    if not key:
        return False
    # Block path traversal and absolute keys so the caller cannot escape
    # the intended recording layout.
    if key.startswith("/") or ".." in key:
        return False
    if RECORDING_KEY_PREFIX and not key.startswith(RECORDING_KEY_PREFIX):
        return False
    return True


def extract_request_values(event):
    path_parameters = event.get("pathParameters") or {}
    query = event.get("queryStringParameters") or {}

    recording_key = str(
        query.get("key")
        or query.get("recordingKey")
        or query.get("recording_s3_key")
        or path_parameters.get("key")
        or path_parameters.get("recordingKey")
        or path_parameters.get("recording_s3_key")
        or ""
    ).strip()
    recording_bucket = str(
        query.get("bucket")
        or query.get("recordingBucket")
        or query.get("recording_s3_bucket")
        or path_parameters.get("bucket")
        or path_parameters.get("recordingBucket")
        or path_parameters.get("recording_s3_bucket")
        or ""
    ).strip()
    download = to_bool(query.get("download"))

    # Optional metadata the caller can pass to get a human-friendly download
    # filename (e.g. "홍길동-2026-06-09.wav"). Falls back to the generic name.
    record = {
        "recipientName": str(query.get("recipientName") or query.get("recipient_name") or "").strip(),
        "createdAt": str(query.get("date") or query.get("createdAt") or query.get("created_at") or "").strip(),
    }

    return recording_key, recording_bucket, download, record


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

        recording_key, recording_bucket, download, record = extract_request_values(event)
        if not recording_key:
            return json_response(400, {"error": "key is required"})

        bucket = recording_bucket or RECORDINGS_BUCKET
        if not bucket:
            return json_response(500, {"error": "Recordings bucket is not configured"})

        # Only hand out presigned URLs for allow-listed buckets and well-formed
        # keys; otherwise a caller could read arbitrary objects the Lambda role
        # can reach by supplying their own bucket/key.
        if bucket not in allowed_buckets():
            return json_response(403, {"error": "Bucket is not allowed"})
        if not is_valid_recording_key(recording_key):
            return json_response(400, {"error": "Invalid recording key"})

        url = generate_presigned_url(bucket, recording_key, download, record)

        return json_response(
            200,
            {
                "bucket": bucket,
                "key": recording_key,
                "expiresIn": PRESIGNED_URL_EXPIRES,
                "download": download,
                "url": url,
            },
        )
    except Exception as error:
        print("getRecordingPresignedUrl error:", str(error))
        return json_response(500, {"error": INTERNAL_ERROR_MESSAGE})
