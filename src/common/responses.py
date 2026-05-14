import json
from typing import Any, Dict, Optional

from common.errors import ApiError


def json_response(status_code: int, payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json; charset=utf-8",
        },
        "body": json.dumps(payload, ensure_ascii=False),
    }


def success_response(data: Optional[Dict[str, Any]] = None, message: Optional[str] = None) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"status": "success"}
    if message:
        payload["message"] = message
    if data is not None:
        payload["data"] = data
    return json_response(200, payload)


def error_response(error: Exception) -> Dict[str, Any]:
    if isinstance(error, ApiError):
        return json_response(error.status_code, {"status": "error", "message": error.message})

    return json_response(500, {"status": "error", "message": "내부 서버 오류가 발생했습니다."})

