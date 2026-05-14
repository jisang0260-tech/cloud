import json
import os
from decimal import Decimal

import boto3


TARGETS_TABLE = os.environ["TARGETS_TABLE"]
SESSIONS_TABLE = os.environ["SESSIONS_TABLE"]
RECORDINGS_BUCKET = os.environ["RECORDINGS_BUCKET"]
ANALYSIS_BUCKET = os.environ["ANALYSIS_BUCKET"]
PRESIGNED_URL_EXPIRES = int(os.getenv("PRESIGNED_URL_EXPIRES", "3600"))
ADMIN_GROUP = os.getenv("ADMIN_GROUP", "admin")

dynamodb = boto3.resource("dynamodb")
s3 = boto3.client("s3")
targets_table = dynamodb.Table(TARGETS_TABLE)
sessions_table = dynamodb.Table(SESSIONS_TABLE)


def json_response(status_code, payload):
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(payload, ensure_ascii=False),
    }


def to_python(value):
    if isinstance(value, list):
        return [to_python(item) for item in value]
    if isinstance(value, dict):
        return {key: to_python(item) for key, item in value.items()}
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    return value


def get_claims(event):
    request_context = event.get("requestContext") or {}
    authorizer = request_context.get("authorizer") or {}

    rest_claims = authorizer.get("claims")
    if rest_claims:
        return rest_claims

    jwt = authorizer.get("jwt") or {}
    return jwt.get("claims") or {}


def extract_groups(claims):
    raw_groups = claims.get("cognito:groups") or claims.get("groups") or ""
    if isinstance(raw_groups, list):
        return {str(group).strip() for group in raw_groups if str(group).strip()}
    if isinstance(raw_groups, str):
        return {group.strip() for group in raw_groups.split(",") if group.strip()}
    return set()


def require_admin(event):
    claims = get_claims(event)
    if not claims:
        raise PermissionError("Missing authenticated user claims.")

    groups = extract_groups(claims)
    role = str(claims.get("custom:role") or claims.get("role") or "").strip().lower()

    if ADMIN_GROUP in groups or role == "admin":
        return claims

    raise PermissionError("Admin permission is required.")


def get_session(session_id):
    response = sessions_table.get_item(Key={"session_id": session_id})
    item = response.get("Item")
    return to_python(item) if item else None


def get_target(user_id):
    response = targets_table.get_item(Key={"user_id": user_id})
    item = response.get("Item")
    return to_python(item) if item else None


def generate_presigned_url(bucket, key):
    if not key:
        return None
    return s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=PRESIGNED_URL_EXPIRES,
    )


def lambda_handler(event, context):
    try:
        require_admin(event)

        path_parameters = event.get("pathParameters") or {}
        session_id = str(path_parameters.get("session_id") or "").strip()
        if not session_id:
            return json_response(400, {"status": "error", "message": "Missing required path parameter: session_id"})

        session = get_session(session_id)
        if not session:
            return json_response(404, {"status": "error", "message": "Session not found"})

        target = None
        if session.get("user_id"):
            target = get_target(str(session["user_id"]))

        original_audio_url = generate_presigned_url(RECORDINGS_BUCKET, session.get("audio_s3_key"))
        analysis_data_url = generate_presigned_url(ANALYSIS_BUCKET, session.get("analysis_s3_key"))

        return json_response(
            200,
            {
                "status": "success",
                "data": {
                    "session_id": session["session_id"],
                    "target_info": {
                        "user_id": session.get("user_id", ""),
                        "name": (target or {}).get("name") or session.get("target_name", ""),
                    },
                    "call_metadata": {
                        "start_time": session.get("start_time"),
                        "duration_sec": session.get("call_duration_sec", 0),
                        "call_status": session.get("call_status", "UNKNOWN"),
                    },
                    "analysis": {
                        "risk_level": session.get("risk_level", "UNKNOWN"),
                        "summary": session.get("analysis_summary", ""),
                    },
                    "transcript": session.get("transcript", []),
                    "s3_urls": {
                        "original_audio": original_audio_url,
                        "analysis_data": analysis_data_url,
                    },
                },
            },
        )
    except PermissionError as error:
        return json_response(403, {"status": "error", "message": str(error)})
    except Exception as error:
        return json_response(500, {"status": "error", "message": str(error)})

