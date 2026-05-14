import json
import os
from datetime import datetime, timezone
from decimal import Decimal

import boto3


TARGETS_TABLE = os.environ["TARGETS_TABLE"]
ADMIN_GROUP = os.getenv("ADMIN_GROUP", "admin")

dynamodb = boto3.resource("dynamodb")
targets_table = dynamodb.Table(TARGETS_TABLE)


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


def get_target(user_id):
    response = targets_table.get_item(Key={"user_id": user_id})
    item = response.get("Item")
    return to_python(item) if item else None


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def coerce_boolean(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "on", "enabled"}:
            return True
        if normalized in {"false", "0", "off", "disabled"}:
            return False
    raise ValueError("auto_call_enabled must be a boolean value.")


def lambda_handler(event, context):
    try:
        require_admin(event)

        path_parameters = event.get("pathParameters") or {}
        user_id = str(path_parameters.get("user_id") or "").strip()
        if not user_id:
            return json_response(400, {"status": "error", "message": "Missing required path parameter: user_id"})

        target = get_target(user_id)
        if not target:
            return json_response(404, {"status": "error", "message": "Target user not found"})

        body = json.loads(event.get("body") or "{}")
        if "auto_call_enabled" not in body:
            return json_response(400, {"status": "error", "message": "Missing required body field: auto_call_enabled"})

        enabled = coerce_boolean(body["auto_call_enabled"])
        status_value = "ENABLED" if enabled else "DISABLED"
        updated_at = utc_now()

        response = targets_table.update_item(
            Key={"user_id": user_id},
            UpdateExpression="SET auto_call_enabled = :enabled, auto_call_status = :status, updated_at = :updated_at",
            ExpressionAttributeValues={
                ":enabled": enabled,
                ":status": status_value,
                ":updated_at": updated_at,
            },
            ReturnValues="ALL_NEW",
        )
        updated = to_python(response["Attributes"])

        status_label = "enabled" if enabled else "disabled"
        return json_response(
            200,
            {
                "status": "success",
                "message": "Auto call setting has been {}.".format(status_label),
                "data": {
                    "user_id": updated["user_id"],
                    "auto_call_enabled": updated["auto_call_enabled"],
                    "updated_at": updated["updated_at"],
                },
            },
        )
    except PermissionError as error:
        return json_response(403, {"status": "error", "message": str(error)})
    except ValueError as error:
        return json_response(400, {"status": "error", "message": str(error)})
    except Exception as error:
        return json_response(500, {"status": "error", "message": str(error)})

