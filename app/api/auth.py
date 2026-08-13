""""Session authentication endpoints consumed by the React frontend."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.auth import SESSION_COOKIE_NAME, SESSION_MAX_AGE, authenticate, hash_password
from app.config import get_settings
from app.db import get_session
from app.deps import current_user, require_admin
from app.models import InviteCode, Profile, User, utcnow

router = APIRouter(prefix="/api/auth", tags=["authentication"])


class Credentials(BaseModel):
    email: str
    password: str = Field(min_length=1, max_length=1024)


class Registration(Credentials):
    password: str = Field(min_length=10, max_length=1024)
    invite_code: str = Field(min_length=1, max_length=256)


class CurrentUser(BaseModel):
    id: int
    email: str
    is_admin: bool


class InviteOut(BaseModel):
    code: str
    link: str


def _set_session(response: Response, user: User) -> None:
    from app.auth import create_session_token

    response.set_cookie(
        SESSION_COOKIE_NAME, create_session_token(user), max_age=SESSION_MAX_AGE,
        httponly=True, samesite="lax", secure=get_settings().session_cookie_secure,
    )


def _out(user: User) -> CurrentUser:
    return CurrentUser(id=user.id or 0, email=user.email, is_admin=user.is_admin)


@router.post("/login", response_model=CurrentUser)
def login(payload: Credentials, response: Response, session: Session = Depends(get_session)) -> CurrentUser:
    user = authenticate(session, payload.email, payload.password)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")
    user.last_login_at = utcnow()
    session.add(user)
    session.commit()
    _set_session(response, user)
    return _out(user)


@router.post("/register", response_model=CurrentUser, status_code=status.HTTP_201_CREATED)
def register(payload: Registration, response: Response, session: Session = Depends(get_session)) -> CurrentUser:
    email = payload.email.strip().lower()
    invite = session.exec(select(InviteCode).where(InviteCode.code == payload.invite_code.strip())).first()
    if invite is None or not invite.is_redeemable():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That invite code is not valid")
    if session.exec(select(User).where(User.email == email)).first() is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "That email is already registered")
    user = User(email=email, password_hash=hash_password(payload.password))
    session.add(user)
    session.commit()
    session.refresh(user)
    invite.used_by_id, invite.used_at = user.id, utcnow()
    session.add(invite)
    session.add(Profile(user_id=user.id, email=email))
    session.commit()
    _set_session(response, user)
    return _out(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response) -> Response:
    response.status_code = status.HTTP_204_NO_CONTENT
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response


@router.get("/me", response_model=CurrentUser)
def me(user: User = Depends(current_user)) -> CurrentUser:
    return _out(user)


@router.get("/invite", response_model=InviteOut)
def get_invite(user: User = Depends(require_admin), session: Session = Depends(get_session)) -> InviteOut:
    invite = session.exec(
        select(InviteCode)
        .where(InviteCode.used_at.is_(None))
        .order_by(InviteCode.created_at.desc())
    ).first()
    if invite is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No unused invite code found")
    return InviteOut(code=invite.code, link=f"/register?invite_code={invite.code}")
