import json
from typing import Any, Dict

from common.auth import require_admin
from common.errors import ApiError
from common.repositories import CareCallRepository
from common.responses import error_response, success_response


repository = CareCallRepository()


def _coerce_boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "on", "enabled"}:
            return True
        if normalized in {"false", "0", "off", "disabled"}:
            return False
    raise ApiError(400, "'auto_call_enabled' 는 boolean 이어야 합니다.")


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    try:
        require_admin(event)

        path_parameters = event.get("pathParameters") or {}
        user_id = str(path_parameters.get("user_id") or "").strip()
        if not user_id:
            raise ApiError(400, "path parameter 'user_id' 가 필요합니다.")

        target = repository.get_target(user_id)
        if not target:
            raise ApiError(404, "대상자를 찾을 수 없습니다.")

        body = json.loads(event.get("body") or "{}")
        if "auto_call_enabled" not in body:
            raise ApiError(400, "body 에 'auto_call_enabled' 값이 필요합니다.")

        enabled = _coerce_boolean(body["auto_call_enabled"])
        updated = repository.update_auto_call(user_id, enabled)

        status_label = "활성화(ON)" if enabled else "비활성화(OFF)"
        return success_response(
            {
                "user_id": updated["user_id"],
                "auto_call_enabled": updated["auto_call_enabled"],
                "updated_at": updated["updated_at"],
            },
            message=f"대상자의 자동 발신 설정이 {status_label} 되었습니다.",
        )
    except Exception as error:
        return error_response(error)

