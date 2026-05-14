from typing import Any, Dict, Set

from common.config import settings
from common.errors import ApiError


def get_jwt_claims(event: Dict[str, Any]) -> Dict[str, Any]:
    request_context = event.get("requestContext") or {}
    authorizer = request_context.get("authorizer") or {}
    jwt = authorizer.get("jwt") or {}
    return jwt.get("claims") or {}


def _extract_groups(claims: Dict[str, Any]) -> Set[str]:
    raw_groups = claims.get("cognito:groups") or claims.get("groups") or ""
    if isinstance(raw_groups, list):
        return {str(group).strip() for group in raw_groups if str(group).strip()}
    if isinstance(raw_groups, str):
        return {group.strip() for group in raw_groups.split(",") if group.strip()}
    return set()


def require_admin(event: Dict[str, Any]) -> Dict[str, Any]:
    claims = get_jwt_claims(event)
    if not claims:
        raise ApiError(401, "유효한 JWT 가 필요합니다.")

    groups = _extract_groups(claims)
    role = str(claims.get("custom:role") or claims.get("role") or "").strip().lower()

    if settings.admin_group in groups or role == "admin":
        return claims

    raise ApiError(403, "관리자 권한이 필요합니다.")

