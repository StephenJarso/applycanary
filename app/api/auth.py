""""Session authentication endpoints consumed by the React frontend."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response, status
from pydantic import BaseModel, EmailStr, Field
from sqlmodel import Session, select

from app.auth import (
    SESSION_COOKIE_NAME,
    SESSION_MAX_AGE,
    authenticate,
    clear_email_change_token,
    clear_email_verification_token,
    clear_password_reset_token,
    consume_email_change_token,
    consume_email_verification_token,
    consume_password_reset_token,
    create_email_change_token,
    create_email_verification_token,
    create_password_reset_token,
    hash_password,
    verify_password,
)
from app.config import get_settings
from app.db import get_session
from app.deps import current_user, require_admin
from app.models import InviteCode, Profile, User, utcnow
from app.notify.email import (
    send_email_change_email,
    send_email_changed_email,
    send_password_changed_email,
    send_password_reset_email,
    send_verification_email,
)

router = APIRouter(prefix="/api/auth", tags=["authentication"])

# Returned by every endpoint that must not disclose whether an address is
# registered. See `forgot_password`.
_NO_CONTENT = status.HTTP_204_NO_CONTENT


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
    email_verified: bool = False
    # False only from /register when verification is enforced: the account
    # exists but deliberately holds no session yet. Lets the register form show
    # "check your inbox" instead of navigating into an app the user cannot load.
    session_started: bool = True


class InviteOut(BaseModel):
    code: str
    link: str


class SignupInfoOut(BaseModel):
    # Hackathon open-signup: prefilled into the register form so new users can
    # sign up without hunting for a single-use invite code.
    default_invite_code: str = ""
    require_email_verification: bool = False


class EmailRequest(BaseModel):
    email: EmailStr


class TokenRequest(BaseModel):
    token: str = Field(min_length=1, max_length=512)


class ResetPasswordRequest(TokenRequest):
    password: str = Field(min_length=10, max_length=1024)


class ChangeEmailRequest(BaseModel):
    new_email: EmailStr
    password: str = Field(min_length=1, max_length=1024)


def _set_session(response: Response, user: User) -> None:
    from app.auth import create_session_token

    response.set_cookie(
        SESSION_COOKIE_NAME, create_session_token(user), max_age=SESSION_MAX_AGE,
        httponly=True, samesite="lax", secure=get_settings().session_cookie_secure,
    )


def _out(user: User, *, session_started: bool = True) -> CurrentUser:
    return CurrentUser(
        id=user.id or 0, email=user.email, is_admin=user.is_admin,
        email_verified=user.email_verified, session_started=session_started,
    )


@router.post("/login", response_model=CurrentUser)
def login(payload: Credentials, response: Response, session: Session = Depends(get_session)) -> CurrentUser:
    user = authenticate(session, payload.email, payload.password)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")
    # 403 rather than 401: the credentials were correct, so retyping them will
    # not help. The frontend keys the "resend confirmation" prompt off this
    # status, which is unambiguous here because login has no other 403.
    if get_settings().require_email_verification and not user.email_verified:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Confirm your email address before signing in. Check your inbox for the link.",
        )
    user.last_login_at = utcnow()
    session.add(user)
    session.commit()
    _set_session(response, user)
    return _out(user)


@router.post("/register", response_model=CurrentUser, status_code=status.HTTP_201_CREATED)
def register(
    payload: Registration,
    response: Response,
    background: BackgroundTasks,
    session: Session = Depends(get_session),
) -> CurrentUser:
    email = payload.email.strip().lower()
    code = payload.invite_code.strip()
    settings = get_settings()
    # Hackathon open-signup: the shared default code is accepted without
    # consuming an InviteCode row, so every new user can register with the same
    # prefilled referral code.
    if settings.default_invite_code and code == settings.default_invite_code:
        invite = None
    else:
        invite = session.exec(select(InviteCode).where(InviteCode.code == code)).first()
        if invite is None or not invite.is_redeemable():
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "That invite code is not valid")
    if session.exec(select(User).where(User.email == email)).first() is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "That email is already registered")
    user = User(email=email, password_hash=hash_password(payload.password))
    token = create_email_verification_token(user)
    session.add(user)
    session.commit()
    session.refresh(user)
    if invite is not None:
        invite.used_by_id, invite.used_at = user.id, utcnow()
        session.add(invite)
    session.add(Profile(user_id=user.id, email=email))
    session.commit()

    # Runs after the response is sent, so a slow or unreachable mail provider
    # delays nobody's signup. FastAPI awaits the coroutine on the event loop.
    background.add_task(send_verification_email, user.email, token)

    # Signing the user straight in would make the verification gate pointless,
    # so when it is enforced the session waits until the address is confirmed.
    if settings.require_email_verification:
        return _out(user, session_started=False)
    _set_session(response, user)
    return _out(user)


@router.post("/verify-email", status_code=_NO_CONTENT)
def verify_email(payload: TokenRequest, session: Session = Depends(get_session)) -> Response:
    """Confirm an address from the link in the registration email.

    Unauthenticated by design: the link is often opened in whichever browser
    the mail client hands it to, which may hold no session.
    """
    user = consume_email_verification_token(session, payload.token)
    if user is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That confirmation link is invalid or has expired")
    user.email_verified = True
    clear_email_verification_token(user)
    session.add(user)
    session.commit()
    return Response(status_code=_NO_CONTENT)


@router.post("/resend-verification", status_code=_NO_CONTENT)
def resend_verification(
    payload: Credentials,
    background: BackgroundTasks,
    session: Session = Depends(get_session),
) -> Response:
    """Re-send the confirmation link. Always 204, to avoid probing for accounts."""
    user = authenticate(session, payload.email, payload.password)
    if user is None or user.email_verified:
        return Response(status_code=_NO_CONTENT)
    token = create_email_verification_token(user)
    session.add(user)
    session.commit()
    background.add_task(send_verification_email, user.email, token)
    return Response(status_code=_NO_CONTENT)


@router.post("/forgot-password", status_code=_NO_CONTENT)
def forgot_password(
    payload: EmailRequest,
    background: BackgroundTasks,
    session: Session = Depends(get_session),
) -> Response:
    """Start a password reset.

    Always 204, whether or not the address is registered: a 404 here would turn
    the endpoint into an account-enumeration oracle. Abuse is bounded by the
    10-req/min limit the middleware puts on /api/auth/*.
    """
    email = payload.email.strip().lower()
    user = session.exec(select(User).where(User.email == email)).first()
    if user is None or not user.is_active:
        return Response(status_code=_NO_CONTENT)
    token = create_password_reset_token(user)
    session.add(user)
    session.commit()
    background.add_task(send_password_reset_email, user.email, token)
    return Response(status_code=_NO_CONTENT)


@router.post("/reset-password", status_code=_NO_CONTENT)
def reset_password(
    payload: ResetPasswordRequest,
    background: BackgroundTasks,
    session: Session = Depends(get_session),
) -> Response:
    user = consume_password_reset_token(session, payload.token)
    if user is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That reset link is invalid or has expired")
    user.password_hash = hash_password(payload.password)
    # Whoever prompted the reset may already hold a stolen cookie; bumping the
    # version logs every existing session out.
    user.token_version += 1
    clear_password_reset_token(user)
    session.add(user)
    session.commit()
    background.add_task(send_password_changed_email, user.email)
    return Response(status_code=_NO_CONTENT)


@router.post("/change-email", status_code=_NO_CONTENT)
def change_email(
    payload: ChangeEmailRequest,
    background: BackgroundTasks,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> Response:
    """Stage an email change. The address only moves once the new one confirms."""
    if not verify_password(payload.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid password")

    new_email = payload.new_email.strip().lower()
    if new_email == user.email:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That is already your email address")
    if session.exec(select(User).where(User.email == new_email)).first() is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "That email is already registered")

    token = create_email_change_token(user, new_email)
    session.add(user)
    session.commit()
    background.add_task(send_email_change_email, new_email, token)
    return Response(status_code=_NO_CONTENT)


@router.post("/verify-email-change", status_code=_NO_CONTENT)
def verify_email_change(
    payload: TokenRequest,
    background: BackgroundTasks,
    session: Session = Depends(get_session),
) -> Response:
    """Complete a staged email change.

    Unauthenticated, like /verify-email: this link arrives at the *new* address,
    which the user may well open somewhere they have never signed in. The token
    is the proof, and it identifies the account on its own.
    """
    user = consume_email_change_token(session, payload.token)
    if user is None or not user.pending_email:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That confirmation link is invalid or has expired")

    # Re-check at confirmation time: the address may have been claimed during
    # the 24 hours the token was valid.
    if session.exec(select(User).where(User.email == user.pending_email)).first() is not None:
        clear_email_change_token(user)
        session.add(user)
        session.commit()
        raise HTTPException(status.HTTP_409_CONFLICT, "That email is already registered")

    old_email = user.email
    user.email = user.pending_email
    user.email_verified = True
    # The login identifier just changed, so old sessions should not survive it.
    user.token_version += 1
    clear_email_change_token(user)
    session.add(user)
    session.commit()

    # Warn the *old* address too — if this change was not the owner's doing,
    # that inbox is the only one they still control.
    background.add_task(send_email_changed_email, user.email, old_email)
    background.add_task(send_email_changed_email, old_email, old_email)
    return Response(status_code=_NO_CONTENT)


@router.get("/signup-info", response_model=SignupInfoOut)
def signup_info() -> SignupInfoOut:
    """Public signup defaults so the register form can prefill the invite code."""
    settings = get_settings()
    return SignupInfoOut(
        default_invite_code=settings.default_invite_code,
        require_email_verification=settings.require_email_verification,
    )


@router.post("/logout", status_code=_NO_CONTENT)
def logout(response: Response) -> Response:
    response.status_code = _NO_CONTENT
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
