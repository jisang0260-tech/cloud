from typing import Any, Dict, List

from common.auth import require_admin
from common.errors import ApiError
from common.repositories import CareCallRepository
from common.responses import error_response, success_response


repository = CareCallRepository()


def _risk_rank(level: str) -> int:
    order = {
        "CRITICAL": 0,
        "HIGH": 1,
        "WARNING": 2,
        "NORMAL": 3,
        "LOW": 4,
        "UNKNOWN": 5,
    }
    return order.get(level, 6)


def _build_detail(target: Dict[str, Any], session: Dict[str, Any] | None) -> Dict[str, Any]:
    if not session:
        return {
            "user_id": target["user_id"],
            "name": target.get("name", ""),
            "call_status": "NOT_CALLED",
            "risk_level": "UNKNOWN",
            "analysis_summary": "당일 통화 이력이 없습니다.",
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


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    try:
        require_admin(event)

        query = event.get("queryStringParameters") or {}
        call_date = str(query.get("date") or "").strip()
        if not call_date:
            raise ApiError(400, "query parameter 'date' 가 필요합니다.")

        enabled_targets = repository.list_enabled_targets()
        sessions = repository.list_sessions_by_date(call_date)

        latest_session_by_user: Dict[str, Dict[str, Any]] = {}
        for session in sessions:
            user_id = str(session.get("user_id") or "").strip()
            if not user_id:
                continue
            previous = latest_session_by_user.get(user_id)
            if not previous or str(session.get("start_time", "")) > str(previous.get("start_time", "")):
                latest_session_by_user[user_id] = session

        details: List[Dict[str, Any]] = []
        seen_user_ids = set()
        completed_calls = 0
        risk_detected = 0
        unanswered = 0

        for target in enabled_targets:
            detail = _build_detail(target, latest_session_by_user.get(target["user_id"]))
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

        details.sort(key=lambda item: (_risk_rank(item["risk_level"]), item["name"]))

        return success_response(
            {
                "summary": {
                    "total_targets": len(details),
                    "completed_calls": completed_calls,
                    "risk_detected": risk_detected,
                    "unanswered": unanswered,
                },
                "details": details,
            }
        )
    except Exception as error:
        return error_response(error)
