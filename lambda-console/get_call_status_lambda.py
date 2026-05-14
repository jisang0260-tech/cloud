import json
import os
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key


TARGETS_TABLE = os.environ["TARGETS_TABLE"]
SESSIONS_TABLE = os.environ["SESSIONS_TABLE"]
CALL_DATE_INDEX = os.getenv("CALL_DATE_INDEX", "CallDateIndex")
AUTO_CALL_STATUS_INDEX = os.getenv("AUTO_CALL_STATUS_INDEX", "AutoCallStatusIndex")
ADMIN_GROUP = os.getenv("ADMIN_GROUP", "admin")

dynamodb = boto3.resource("dynamodb")
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


def list_enabled_targets():
    response = targets_table.query(
        IndexName=AUTO_CALL_STATUS_INDEX,
        KeyConditionExpression=Key("auto_call_status").eq("ENABLED"),
    )
    items = response.get("Items", [])

    while "LastEvaluatedKey" in response:
        response = targets_table.query(
            IndexName=AUTO_CALL_STATUS_INDEX,
            KeyConditionExpression=Key("auto_call_status").eq("ENABLED"),
            ExclusiveStartKey=response["LastEvaluatedKey"],
        )
        items.extend(response.get("Items", []))

    return [to_python(item) for item in items]


def list_sessions_by_date(call_date):
    response = sessions_table.query(
        IndexName=CALL_DATE_INDEX,
        KeyConditionExpression=Key("call_date").eq(call_date),
    )
    items = response.get("Items", [])

    while "LastEvaluatedKey" in response:
        response = sessions_table.query(
            IndexName=CALL_DATE_INDEX,
            KeyConditionExpression=Key("call_date").eq(call_date),
            ExclusiveStartKey=response["LastEvaluatedKey"],
        )
        items.extend(response.get("Items", []))

    return [to_python(item) for item in items]


def risk_rank(level):
    order = {
        "CRITICAL": 0,
        "HIGH": 1,
        "WARNING": 2,
        "NORMAL": 3,
        "LOW": 4,
        "UNKNOWN": 5,
    }
    return order.get(level, 6)


def build_detail(target, session):
    if not session:
        return {
            "user_id": target["user_id"],
            "name": target.get("name", ""),
            "call_status": "NOT_CALLED",
            "risk_level": "UNKNOWN",
            "analysis_summary": "No call history for the requested date.",
            "call_duration_sec": 0,
        }

    return {
        "user_id": target["user_id"],
        "name": target.get("name") or session.get("target_name", ""),
        "call_status": session.get("call_status", "UNKNOWN"),
        "risk_level": session.get("risk_level", "UNKNOWN"),
        "analysis_summary": session.get("analysis_summary", ""),
        "call_duration_sec": session.get("call_duration_sec", 0),
    }


def lambda_handler(event, context):
    try:
        require_admin(event)

        query = event.get("queryStringParameters") or {}
        call_date = str(query.get("date") or "").strip()
        if not call_date:
            return json_response(400, {"status": "error", "message": "Missing required query parameter: date"})

        enabled_targets = list_enabled_targets()
        sessions = list_sessions_by_date(call_date)

        latest_session_by_user = {}
        for session in sessions:
            user_id = str(session.get("user_id") or "").strip()
            if not user_id:
                continue
            previous = latest_session_by_user.get(user_id)
            if not previous or str(session.get("start_time", "")) > str(previous.get("start_time", "")):
                latest_session_by_user[user_id] = session

        details = []
        seen_user_ids = set()
        completed_calls = 0
        risk_detected = 0
        unanswered = 0

        for target in enabled_targets:
            detail = build_detail(target, latest_session_by_user.get(target["user_id"]))
            details.append(detail)
            seen_user_ids.add(target["user_id"])

            if detail["call_status"] == "COMPLETED":
                completed_calls += 1
            if detail["risk_level"] in {"WARNING", "HIGH", "CRITICAL"}:
                risk_detected += 1
            if detail["call_status"] in {"UNANSWERED", "NO_ANSWER", "FAILED"}:
                unanswered += 1

        for user_id, session in latest_session_by_user.items():
            if user_id in seen_user_ids:
                continue

            detail = {
                "user_id": user_id,
                "name": session.get("target_name", ""),
                "call_status": session.get("call_status", "UNKNOWN"),
                "risk_level": session.get("risk_level", "UNKNOWN"),
                "analysis_summary": session.get("analysis_summary", ""),
                "call_duration_sec": session.get("call_duration_sec", 0),
            }
            details.append(detail)

            if detail["call_status"] == "COMPLETED":
                completed_calls += 1
            if detail["risk_level"] in {"WARNING", "HIGH", "CRITICAL"}:
                risk_detected += 1
            if detail["call_status"] in {"UNANSWERED", "NO_ANSWER", "FAILED"}:
                unanswered += 1

        details.sort(key=lambda item: (risk_rank(item["risk_level"]), item["name"]))

        return json_response(
            200,
            {
                "status": "success",
                "data": {
                    "summary": {
                        "total_targets": len(details),
                        "completed_calls": completed_calls,
                        "risk_detected": risk_detected,
                        "unanswered": unanswered,
                    },
                    "details": details,
                },
            },
        )
    except PermissionError as error:
        return json_response(403, {"status": "error", "message": str(error)})
    except Exception as error:
        return json_response(500, {"status": "error", "message": str(error)})

