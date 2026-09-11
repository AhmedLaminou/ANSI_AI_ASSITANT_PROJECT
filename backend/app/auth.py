from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Cookie, Depends, HTTPException, status
from jwt.exceptions import InvalidTokenError
from pwdlib import PasswordHash
from sqlalchemy.orm import Session

from .config import get_settings
from .database import User, get_db


password_hash = PasswordHash.recommended()
TOKEN_LIFETIME_HOURS = 8


def verify_password(password: str, stored_hash: str) -> bool:
    return password_hash.verify(password, stored_hash)


def create_access_token(user: User) -> str:
    expires_at = datetime.now(timezone.utc) + timedelta(hours=TOKEN_LIFETIME_HOURS)
    return jwt.encode({"sub": str(user.id), "role": user.role, "exp": expires_at}, get_settings().jwt_secret, algorithm="HS256")


def get_current_user(ansi_session: str | None = Cookie(default=None), db: Session = Depends(get_db)) -> User:
    if not ansi_session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    try:
        payload = jwt.decode(ansi_session, get_settings().jwt_secret, algorithms=["HS256"])
        user_id = int(payload["sub"])
    except (InvalidTokenError, KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session") from exc
    user = db.get(User, user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Inactive account")
    return user


def require_roles(*allowed_roles: str):
    def authorize(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed_roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permission")
        return user

    return authorize
