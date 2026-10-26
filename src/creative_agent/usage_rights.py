"""Current own-footage permission shared by search and script preparation."""
from datetime import UTC, datetime


def require_own_reuse(metadata):
    rights = metadata.get('rights')
    if metadata.get('reuse_allowed') is False:
        raise ValueError('此素材没有当前制作用途权限')
    if rights is not None:
        if not isinstance(rights, dict) or rights.get('derivative_create') is not True:
            raise ValueError('此素材没有明确的当前制作用途授权')
        try:
            expires = datetime.fromisoformat(str(rights.get('expires_at', '')).replace('Z', '+00:00'))
        except ValueError:
            raise ValueError('此素材授权有效期未知，请核实制作用途权限') from None
        if expires.tzinfo is None or expires <= datetime.now(UTC):
            raise ValueError('此素材授权已过期或有效期不明确，请核实制作用途权限')
        if not isinstance(rights.get('evidence'), str) or not rights['evidence'].strip():
            raise ValueError('此素材缺少当前制作用途授权依据')
    elif metadata.get('reuse_allowed') is not True:
        raise ValueError('此素材复用权限未知，请先确认当前制作用途')
