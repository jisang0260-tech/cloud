from typing import Any, Dict

from common.auth import require_admin
from common.errors import ApiError
from common.repositories import CareCallRepository
from common.responses import error_response, success_response
from common.services import StorageService
from common.config import settings


repository = CareCallRepository()
storage_service = StorageService()


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    try:
        require_admin(event)

        path_parameters = event.get("pathParameters") or {}
        session_id = str(path_parameters.get("session_id") or "").strip()
        if not session_id:
            raise ApiError(400, "path parameter 'session_id' 가 필요합니다.")

        session = repository.get_session(session_id)
        if not session:
            raise ApiError(404, "해당 세션을 찾을 수 없습니다.")

        target = repository.get_target(str(session.get("user_id", ""))) if session.get("user_id") else None

        original_audio_url = storage_service.generate_presigned_url(
            settings.recordings_bucket, session.get("audio_s3_key")
        )
        analysis_data_url = storage_service.generate_presigned_url(
            settings.analysis_bucket, session.get("analysis_s3_key")
        )

        return success_response(
            {
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
            }
        )
    except Exception as error:
        return error_response(error)

