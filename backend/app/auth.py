"""Sessions: issue, verify, revoke.

A session is a signed token in an HttpOnly cookie. Authorisation is never read from
the token — role and department come from the database on every request — so the
token carries only who you are and which *generation* of sessions it belongs to.

That generation, `token_version`, is what makes revocation possible. A signed token
cannot be withdrawn once issued; it can only be made to stop matching. Incrementing
the account's version invalidates every token issued before, on the next request.
Until it existed, changing a password left a stolen session valid for eight hours —
and an agent changes their password precisely when they think it has been stolen.
"""

from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Cookie, Depends, HTTPException, Request, Response, status
from jwt.exceptions import InvalidTokenError
from pwdlib import PasswordHash
from sqlalchemy.orm import Session

from .config import get_settings
from .database import User, get_db


password_hash = PasswordHash.recommended()
TOKEN_LIFETIME_HOURS = 8
SESSION_COOKIE = "ansi_session"

# The only paths an account may reach while an administrator-chosen password is
# still in force. Everything else answers 403 until the agent picks their own.
PASSWORD_CHANGE_ALLOWED_PATHS = frozenset({"/auth/me", "/auth/password", "/auth/logout"})
PASSWORD_CHANGE_REQUIRED = "Vous devez choisir un nouveau mot de passe avant de continuer."


def verify_password(password: str, stored_hash: str) -> bool:
    return password_hash.verify(password, stored_hash)


def create_access_token(user: User) -> str:
    """No role claim: a claim nothing reads is a trap for whoever reads it next."""
    expires_at = datetime.now(timezone.utc) + timedelta(hours=TOKEN_LIFETIME_HOURS)
    return jwt.encode(
        {"sub": str(user.id), "ver": user.token_version or 0, "exp": expires_at},
        get_settings().jwt_secret,
        algorithm="HS256",
    )


def set_session_cookie(response: Response, user: User) -> None:
    response.set_cookie(
        key=SESSION_COOKIE,
        value=create_access_token(user),
        httponly=True,
        secure=get_settings().cookie_secure,
        samesite="lax",
        max_age=TOKEN_LIFETIME_HOURS * 60 * 60,
        path="/",
    )


def revoke_sessions(user: User) -> None:
    """Every session issued before this call stops working on its next request.

    The caller commits. To keep the *current* session alive afterwards, re-issue its
    cookie with `set_session_cookie` once the new version is committed.
    """
    user.token_version = (user.token_version or 0) + 1


def get_current_user(
    request: Request,
    ansi_session: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
) -> User:
    if not ansi_session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Connexion requise.")
    try:
        payload = jwt.decode(ansi_session, get_settings().jwt_secret, algorithms=["HS256"])
        user_id = int(payload["sub"])
        version = int(payload.get("ver", 0))
    except (InvalidTokenError, KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session invalide.") from exc

    user = db.get(User, user_id)
    if not user or not user.is_active or user.status != "active":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Compte inactif.")
    if version != (user.token_version or 0):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Cette session a été fermée. Reconnectez-vous.",
        )
    # Checked here, once, rather than on each endpoint: a gate that has to be
    # remembered route by route is a gate that will be forgotten on one of them.
    if user.must_change_password and request.url.path not in PASSWORD_CHANGE_ALLOWED_PATHS:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=PASSWORD_CHANGE_REQUIRED)
    return user


def require_roles(*allowed_roles: str):
    def authorize(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed_roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Droits insuffisants.")
        return user

    return authorize
