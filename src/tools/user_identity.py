"""Helpers for binding profile operations to the Coze runtime identity."""

from __future__ import annotations

from typing import Any


MISSING_USER_ID_MESSAGE = "当前请求缺少扣子用户身份，无法使用用户画像功能。"


class MissingUserIdentityError(ValueError):
    """Raised when Coze did not provide a user identity for this request."""


def require_context_user_id(ctx: Any) -> str:
    """Return a normalized Coze user ID or reject an anonymous context."""
    user_id = str(getattr(ctx, "user_id", "") or "").strip()
    if not user_id:
        raise MissingUserIdentityError(MISSING_USER_ID_MESSAGE)
    return user_id
