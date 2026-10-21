import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from .config import settings
from .database import SessionLocal, set_tenant
from .models import LoginThrottle, Membership, SessionRecord, Tenant, User, now

hasher = PasswordHasher()
COOKIE = "aftercare_session"
ROLE_PERMISSIONS = {
    "staff": {
        "orders.write",
        "tickets.write",
        "evidence.write",
        "proposals.write",
        "runs.start",
        "actions.execute",
    },
    "supervisor": {
        "orders.write",
        "tickets.write",
        "evidence.write",
        "evidence.verify",
        "proposals.write",
        "approvals.decide",
        "actions.verify",
        "runs.start",
        "audit.read",
        "policies.manage",
        "members.manage",
    },
    "executor": {"evidence.write", "actions.execute"},
    "auditor": {"audit.read"},
    "admin": {
        "orders.write",
        "tickets.write",
        "evidence.write",
        "evidence.verify",
        "proposals.write",
        "approvals.decide",
        "actions.execute",
        "actions.verify",
        "policies.manage",
        "members.manage",
        "runs.start",
        "audit.read",
    },
}


class DomainError(Exception):
    def __init__(self, code: str, message: str, status: int = 409):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)


def require(condition: bool, code: str, message: str, status: int = 409):
    if not condition:
        raise DomainError(code, message, status)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def password_valid(password: str, stored: str) -> bool:
    try:
        return hasher.verify(stored, password)
    except (VerificationError, InvalidHashError):
        return False


def password_policy(password: str):
    require(
        12 <= len(password) <= 128, "password_policy", "密码长度需要为12至128位", 422
    )


def password_throttle(db: Session, email: str):
    key = digest(email.casefold())
    insert = pg_insert if db.bind.dialect.name == "postgresql" else sqlite_insert
    db.execute(
        insert(LoginThrottle)
        .values(key=key, attempts=0, window_at=now())
        .on_conflict_do_nothing(index_elements=["key"])
    )
    throttle = db.scalar(
        select(LoginThrottle).where(LoginThrottle.key == key).with_for_update()
    )
    if throttle.window_at < now() - timedelta(minutes=15):
        throttle.attempts = 0
        throttle.window_at = now()
    require(
        throttle.attempts < 8, "login_rate_limit", "尝试过于频繁，请15分钟后再试", 429
    )
    return throttle


def check_password(db: Session, email: str, password: str, stored: str):
    throttle = password_throttle(db, email)
    if not password_valid(password, stored):
        throttle.attempts += 1
        db.commit()
        require(False, "invalid_credentials", "账户或密码不正确", 401)
    throttle.attempts = 0


def get_db(request: Request):
    # Request middleware commits before the HTTP response is released.
    return request.state.db


@dataclass
class Actor:
    user: User
    tenant: Tenant
    membership: Membership
    session: SessionRecord

    @property
    def tenant_id(self):
        return self.tenant.id

    @property
    def user_id(self):
        return self.user.id

    def can(self, permission: str):
        return permission in ROLE_PERMISSIONS.get(self.membership.role, set())

    def need(self, permission: str):
        require(self.can(permission), "forbidden", "当前角色没有此操作权限", 403)


def current_actor(request: Request, db: Session = Depends(get_db)) -> Actor:
    token = request.cookies.get(COOKIE, "")
    session = db.scalar(
        select(SessionRecord).where(
            SessionRecord.token_hash == digest(token), SessionRecord.expires_at > now()
        )
    )
    require(session is not None, "unauthenticated", "请登录或重新验证会话", 401)
    user = db.get(User, session.user_id)
    tenant = db.get(Tenant, session.tenant_id)
    member = db.scalar(
        select(Membership).where(
            Membership.user_id == session.user_id,
            Membership.tenant_id == session.tenant_id,
        )
    )
    require(
        bool(
            user
            and user.enabled
            and tenant
            and tenant.enabled
            and member
            and member.enabled
        ),
        "account_disabled",
        "账户或工作空间不可用",
        403,
    )
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin", "")
        require(
            origin == settings().public_origin,
            "origin_rejected",
            "请求来源不符合工作台配置",
            403,
        )
        require(
            hmac.compare_digest(request.headers.get("x-csrf-token", ""), session.csrf),
            "csrf_rejected",
            "会话校验失败，请刷新后重试",
            403,
        )
    set_tenant(db, tenant.id)
    request.state.actor_id = user.id
    request.state.tenant_id = tenant.id
    return Actor(user, tenant, member, session)


def create_session(db: Session, response: Response, user: User, tenant_id: str):
    token = secrets.token_urlsafe(32)
    record = SessionRecord(
        token_hash=digest(token),
        csrf=secrets.token_urlsafe(24),
        user_id=user.id,
        tenant_id=tenant_id,
        expires_at=now() + timedelta(hours=settings().session_hours),
    )
    db.add(record)
    response.set_cookie(
        COOKIE,
        token,
        max_age=settings().session_hours * 3600,
        secure=settings().cookie_secure,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return record
