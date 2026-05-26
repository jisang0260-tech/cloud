import json
import hashlib
import os
import re
from datetime import datetime, timezone
from urllib.parse import unquote_plus

import boto3
from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError


TRANSCRIBE_REGION = os.getenv("TRANSCRIBE_REGION", "ap-northeast-2")
LANGUAGE_CODE = os.getenv("LANGUAGE_CODE", "ko-KR")
OUTPUT_BUCKET = os.getenv("TRANSCRIBE_OUTPUT_BUCKET", "")
OUTPUT_PREFIX = os.getenv("TRANSCRIBE_OUTPUT_PREFIX", "transcripts/")
JOB_PREFIX = os.getenv("TRANSCRIBE_JOB_PREFIX", "carecall")
DATA_REGION = os.getenv("DATA_REGION", "us-east-1")
CALL_HISTORY_TABLE = os.getenv("CALL_HISTORY_TABLE", "")
CONTACTID_INDEX = os.getenv("CONTACTID_INDEX", "ContactIdIndex")
CONTACTID_ATTR = os.getenv("CONTACTID_ATTR", "contactId")
UPDATE_CALL_HISTORY = os.getenv("UPDATE_CALL_HISTORY", "true").lower() == "true"

SUPPORTED_EXTENSIONS = {
    ".amr": "amr",
    ".flac": "flac",
    ".m4a": "m4a",
    ".mp3": "mp3",
    ".mp4": "mp4",
    ".ogg": "ogg",
    ".wav": "wav",
    ".webm": "webm",
}

transcribe = boto3.client("transcribe", region_name=TRANSCRIBE_REGION)
dynamodb = boto3.resource("dynamodb", region_name=DATA_REGION)
call_history_table = dynamodb.Table(CALL_HISTORY_TABLE) if CALL_HISTORY_TABLE else None


def lambda_handler(event, context):
    print("s3 event:", json.dumps(event, ensure_ascii=False))

    if not OUTPUT_BUCKET:
        raise ValueError("Missing required env value: TRANSCRIBE_OUTPUT_BUCKET")

    results = []

    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        key = unquote_plus(record["s3"]["object"]["key"])

        result = start_transcription_for_s3_object(bucket, key)
        results.append(result)

    print("transcribe results:", json.dumps(results, ensure_ascii=False))
    return {"status": "ok", "results": results}


def start_transcription_for_s3_object(bucket, key):
    extension = os.path.splitext(key)[1].lower()
    media_format = SUPPORTED_EXTENSIONS.get(extension)

    if not media_format:
        return {
            "status": "skipped",
            "reason": f"Unsupported media extension: {extension}",
            "bucket": bucket,
            "key": key,
        }

    contactId = extract_contactId(key)
    job_name = make_job_name(contactId, key)
    output_key = make_output_key(contactId)

    request = {
        "TranscriptionJobName": job_name,
        "LanguageCode": LANGUAGE_CODE,
        "MediaFormat": media_format,
        "Media": {
            "MediaFileUri": f"s3://{bucket}/{key}",
        },
        "OutputBucketName": OUTPUT_BUCKET,
        "OutputKey": output_key,
    }

    try:
        transcribe.start_transcription_job(**request)
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code")
        if code == "ConflictException":
            update_call_history_transcript(
                contactId=contactId,
                recording_bucket=bucket,
                recording_key=key,
                transcript_bucket=OUTPUT_BUCKET,
                transcript_key=output_key,
            )
            return {
                "status": "exists",
                "message": "Transcription job already exists.",
                "job_name": job_name,
                "input_bucket": bucket,
                "input_key": key,
                "output_bucket": OUTPUT_BUCKET,
                "output_key": output_key,
            }
        raise

    update_call_history_transcript(
        contactId=contactId,
        recording_bucket=bucket,
        recording_key=key,
        transcript_bucket=OUTPUT_BUCKET,
        transcript_key=output_key,
    )

    return {
        "status": "started",
        "job_name": job_name,
        "contactId": contactId,
        "input_bucket": bucket,
        "input_key": key,
        "output_bucket": OUTPUT_BUCKET,
        "output_key": output_key,
    }


def extract_contactId(key):
    match = re.search(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        key,
        re.IGNORECASE,
    )
    if match:
        return match.group(0)

    filename = os.path.basename(key)
    return os.path.splitext(filename)[0]


def make_job_name(contactId, key):
    # Job names are account-region global for a while, so include a stable key hash.
    stable_suffix = hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]
    raw_name = f"{JOB_PREFIX}-{contactId}-{stable_suffix}"
    return sanitize_transcribe_name(raw_name)[:200]


def make_output_key(contactId):
    now = datetime.now(timezone.utc)
    prefix = OUTPUT_PREFIX.strip("/")
    return f"{prefix}/{now:%Y/%m/%d}/{contactId}.json"


def sanitize_transcribe_name(value):
    return re.sub(r"[^0-9A-Za-z._-]", "-", str(value)).strip(".-_")


def update_call_history_transcript(contactId, recording_bucket, recording_key, transcript_bucket, transcript_key):
    if not UPDATE_CALL_HISTORY or not call_history_table or not contactId:
        return

    try:
        items = find_call_history_items(contactId)
        if not items:
            print(f"No CallHistory item found for contactId={contactId}")
            return

        for item in items:
            session_id = item.get("session_id")
            if not session_id:
                continue

            call_history_table.update_item(
                Key={"session_id": session_id},
                UpdateExpression=(
                    "SET transcript_s3_bucket = :transcript_bucket, "
                    "transcript_s3_key = :transcript_key, "
                    "recording_s3_bucket = :recording_bucket, "
                    "recording_s3_key = :recording_key, "
                    "audio_s3_key = :recording_key"
                ),
                ExpressionAttributeValues={
                    ":transcript_bucket": transcript_bucket,
                    ":transcript_key": transcript_key,
                    ":recording_bucket": recording_bucket,
                    ":recording_key": recording_key,
                },
            )
    except Exception as error:
        print(f"CallHistory transcript update failed for contactId={contactId}: {error}")


def find_call_history_items(contactId):
    try:
        response = call_history_table.query(
            IndexName=CONTACTID_INDEX,
            KeyConditionExpression=Key(CONTACTID_ATTR).eq(contactId),
        )
        return response.get("Items", [])
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code")
        if code not in {"ResourceNotFoundException", "ValidationException"}:
            raise

    response = call_history_table.scan(
        FilterExpression=Attr(CONTACTID_ATTR).eq(contactId)
    )
    return response.get("Items", [])
