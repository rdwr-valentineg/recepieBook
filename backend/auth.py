"""Shared-password authentication via signed cookie."""
import hmac
from itsdangerous import TimestampSigner, BadSignature, SignatureExpired
from fastapi import Cookie, HTTPException, Response

from config import settings

COOKIE_NAME = "recipe_session"
MAX_AGE_SECONDS = settings.session_max_age_days * 24 * 3600

_signer = TimestampSigner(settings.session_secret)


def issue_session(response: Response) -> None:
    token = _signer.sign(b"ok").decode("utf-8")
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=settings.secure_cookies,
        path="/",
    )


def clear_session(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


def verify_password(password: str) -> bool:
    return hmac.compare_digest(
        password.encode("utf-8"),
        settings.app_password.encode("utf-8"),
    )


def require_auth(recipe_session: str | None = Cookie(default=None)) -> bool:
    if not recipe_session:
        raise HTTPException(status_code=401, detail="לא מחובר")
    try:
        _signer.unsign(recipe_session, max_age=MAX_AGE_SECONDS)
        return True
    except SignatureExpired:
        raise HTTPException(status_code=401, detail="הסשן פג")
    except BadSignature:
        raise HTTPException(status_code=401, detail="סשן לא תקף")


def is_authenticated(recipe_session: str | None) -> bool:
    """Non-raising check, used in the SPA fallback."""
    if not recipe_session:
        return False
    try:
        _signer.unsign(recipe_session, max_age=MAX_AGE_SECONDS)
        return True
    except (BadSignature, SignatureExpired):
        return False
