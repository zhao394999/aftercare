import hashlib
import io
import json
import secrets
from datetime import datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool
from sqlalchemy import delete, func, or_, select, tuple_
from sqlalchemy.orm import Session

from . import domain as d
from .config import settings
from .database import set_tenant
from .feishu import router as feishu_router
from .importing import parse_csv
from .models import (
    Action,
    AgentRun,
    AuditEvent,
    AuthToken,
    Communication,
    Evidence,
    ImportJob,
    Job,
    Membership,
    ModelCall,
    Order,
    Policy,
    Proposal,
    RunEvent,
    SessionRecord,
    Tenant,
    Ticket,
    User,
    now,
    uid,
)
from .schemas import *
from .security import (
    COOKIE,
    ROLE_PERMISSIONS,
    Actor,
    create_session,
    check_password,
    current_actor,
    digest,
    get_db,
    hasher,
    password_policy,
    password_throttle,
    require,
)
from .storage import file_location, store_file, validate_file

router = APIRouter(prefix="/api/v1")
router.include_router(feishu_router)
DB = Depends(get_db)
AUTH = Depends(current_actor)
DUMMY_HASH = hasher.hash(secrets.token_urlsafe(32))


def profile(actor: Actor):
    return {
        "user": {
            "id": actor.user_id,
            "name": actor.user.name,
            "email": actor.user.email,
        },
        "tenant": {
            "id": actor.tenant_id,
            "name": actor.tenant.name,
            "is_demo": actor.tenant.is_demo,
            "config": actor.tenant.config,
        },
        "role": actor.membership.role,
        "permissions": sorted(ROLE_PERMISSIONS[actor.membership.role]),
        "csrf": actor.session.csrf,
        "model_enabled": settings().model_enabled,
    }


@router.get("/system")
def system_info():
    return {
        "name": "循单 Aftercare",
        "version": "0.1.0",
        "demo_mode": settings().demo_mode,
    }


@router.post("/auth/login")
def login(payload: LoginInput, request: Request, response: Response, db: Session = DB):
    require(
        request.headers.get("origin") == settings().public_origin,
        "origin_rejected",
        "请求来源无效",
        403,
    )
    user = db.scalar(select(User).where(User.email == payload.email.casefold()))
    check_password(
        db, payload.email, payload.password, user.password_hash if user else DUMMY_HASH
    )
    require(user and user.enabled, "invalid_credentials", "账户或密码不正确", 401)
    membership = db.scalar(
        select(Membership)
        .join(Tenant, Membership.tenant_id == Tenant.id)
        .where(
            Membership.user_id == user.id,
            Membership.enabled == True,
            *([Membership.tenant_id == payload.tenant_id] if payload.tenant_id else [Tenant.enabled == True]),
        )
        .order_by(Membership.created_at)
    )
    require(membership is not None, "no_workspace", "账户没有可用工作空间", 403)
    tenant = db.get(Tenant, membership.tenant_id)
    require(tenant and tenant.enabled, "workspace_disabled", "工作空间不可用", 403)
    record = create_session(db, response, user, tenant.id)
    set_tenant(db, tenant.id)
    actor = Actor(user, tenant, membership, record)
    d.audit(db, actor, "auth.login", user.id)
    return profile(actor)


@router.post("/auth/demo")
def demo_login(
    payload: DemoInput, request: Request, response: Response, db: Session = DB
):
    require(
        settings().demo_mode and settings().app_env != "production",
        "not_found",
        "入口不可用",
        404,
    )
    require(
        request.headers.get("origin") == settings().public_origin,
        "origin_rejected",
        "请求来源无效",
        403,
    )
    user = db.scalar(
        select(User).where(User.email == f"demo-{payload.role}@aftercare.local")
    )
    require(user is not None, "demo_not_initialized", "请先运行演示空间初始化命令", 503)
    member = db.scalar(select(Membership).where(Membership.user_id == user.id))
    tenant = db.get(Tenant, member.tenant_id)
    require(tenant.is_demo, "invalid_demo_workspace", "演示空间配置错误", 503)
    old = request.cookies.get(COOKIE)
    if old:
        db.execute(delete(SessionRecord).where(SessionRecord.token_hash == digest(old)))
    record = create_session(db, response, user, tenant.id)
    set_tenant(db, tenant.id)
    actor = Actor(user, tenant, member, record)
    d.audit(db, actor, "auth.demo_login", user.id)
    return profile(actor)


@router.post("/auth/register", status_code=201)
def register(payload: RegisterInput, request: Request, response: Response, db: Session = DB):
    require(request.headers.get("origin") == settings().public_origin,
            "origin_rejected", "请求来源无效", 403)
    throttle = password_throttle(db, "register:" + (request.client.host if request.client else "unknown"))
    throttle.attempts += 1
    db.commit()
    email = payload.email.casefold()
    require(db.scalar(select(User.id).where(User.email == email)) is None,
            "email_registered", "此邮箱已有账户，请登录或使用团队邀请加入", 409)
    password_policy(payload.password)
    tenant = Tenant(name=payload.workspace, config={"sla_hours": 24, "approval_hours": 24})
    user = User(email=email, name=payload.name, password_hash=hasher.hash(payload.password))
    db.add_all([tenant, user])
    db.flush()
    set_tenant(db, tenant.id)
    member = Membership(tenant_id=tenant.id, user_id=user.id, role="supervisor")
    db.add(member)
    record = create_session(db, response, user, tenant.id)
    actor = Actor(user, tenant, member, record)
    d.audit(db, actor, "workspace.registered", tenant.id)
    return profile(actor)


@router.get("/auth/me")
def me(actor: Actor = AUTH):
    return profile(actor)


@router.post("/auth/logout")
def logout(response: Response, actor: Actor = AUTH, db: Session = DB):
    db.delete(actor.session)
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/members")
def members(actor: Actor = AUTH, db: Session = DB):
    return [
        {
            "id": u.id,
            "name": u.name,
            "email": u.email if actor.can("members.manage") else None,
            "role": m.role,
            "enabled": m.enabled,
        }
        for m, u in db.execute(
            select(Membership, User)
            .join(User, Membership.user_id == User.id)
            .where(Membership.tenant_id == actor.tenant_id)
        )
    ]


@router.put("/members/{user_id}")
def update_member(
    user_id: str, payload: MemberUpdate, actor: Actor = AUTH, db: Session = DB
):
    actor.need("members.manage")
    db.scalar(select(Tenant).where(Tenant.id == actor.tenant_id).with_for_update())
    member = db.scalar(
        select(Membership)
        .where(Membership.user_id == user_id, Membership.tenant_id == actor.tenant_id)
        .with_for_update()
    )
    require(member is not None, "not_found", "成员不存在", 404)
    if (
        member.role in {"admin", "supervisor"}
        and member.enabled
        and (payload.role not in {"admin", "supervisor"} or not payload.enabled)
    ):
        admins = db.scalar(
            select(func.count(Membership.id)).where(
                Membership.tenant_id == actor.tenant_id,
                Membership.role.in_(["admin", "supervisor"]),
                Membership.enabled == True,
            )
        )
        require(admins > 1, "last_admin", "不能停用或降级最后一位主管或空间所有者")
    member.role = payload.role
    member.enabled = payload.enabled
    db.execute(
        delete(SessionRecord).where(
            SessionRecord.user_id == user_id, SessionRecord.tenant_id == actor.tenant_id
        )
    )
    d.audit(db, actor, "member.permissions_updated", user_id, payload.model_dump())
    return {"ok": True}


@router.post("/invitations")
def invite(payload: InvitationInput, actor: Actor = AUTH, db: Session = DB):
    actor.need("members.manage")
    require("@" in payload.email, "invalid_email", "邮箱格式不正确", 422)
    token = secrets.token_urlsafe(32)
    record = AuthToken(
        token_hash=digest(token),
        kind="invite",
        tenant_id=actor.tenant_id,
        email=payload.email.casefold(),
        role=payload.role,
        expires_at=now() + timedelta(days=7),
    )
    db.add(record)
    db.flush()
    d.audit(db, actor, "member.invited", record.id, {"role": payload.role})
    return {
        "url": settings().public_origin + "/invite/" + token,
        "expires_at": record.expires_at.isoformat() + "Z",
    }


@router.post("/invitations/accept")
def accept_invitation(
    payload: AcceptInvitation, request: Request, response: Response, db: Session = DB
):
    require(
        request.headers.get("origin") == settings().public_origin,
        "origin_rejected",
        "请求来源无效",
        403,
    )
    token = db.scalar(
        select(AuthToken)
        .where(
            AuthToken.token_hash == digest(payload.token), AuthToken.kind == "invite"
        )
        .with_for_update()
    )
    require(
        token and token.used_at is None and token.expires_at > now(),
        "invalid_invite",
        "邀请链接无效或已过期",
        422,
    )
    user = db.scalar(select(User).where(User.email == token.email))
    if user:
        require(user.enabled, "account_disabled", "账户不可用", 403)
        # Existing identity must authenticate; an invitation can never reset another workspace's password.
        check_password(db, token.email, payload.password, user.password_hash)
    else:
        password_policy(payload.password)
        user = User(
            email=token.email,
            name=payload.name,
            password_hash=hasher.hash(payload.password),
        )
        db.add(user)
        db.flush()
    existing = db.scalar(
        select(Membership).where(
            Membership.tenant_id == token.tenant_id, Membership.user_id == user.id
        )
    )
    require(existing is None, "already_member", "账户已加入此工作空间")
    tenant = db.get(Tenant, token.tenant_id)
    require(tenant and tenant.enabled, "workspace_disabled", "工作空间不可用", 403)
    member = Membership(tenant_id=tenant.id, user_id=user.id, role=token.role)
    db.add(member)
    token.used_at = now()
    record = create_session(db, response, user, tenant.id)
    set_tenant(db, tenant.id)
    actor = Actor(user, tenant, member, record)
    d.audit(db, actor, "member.joined", user.id)
    return profile(actor)


@router.get("/orders")
def orders(
    q: str = "",
    page: int = 1,
    page_size: int = 50,
    ids: str = "",
    import_id: str = "",
    actor: Actor = AUTH,
    db: Session = DB,
):
    require(
        1 <= page and 1 <= page_size <= 200 and len(q) <= 100,
        "pagination",
        "分页或搜索参数无效",
        422,
    )
    query = select(Order).where(Order.tenant_id == actor.tenant_id)
    if ids:
        selected = ids.split(",")
        require(
            len(ids) <= 1850 and len(selected) <= 50,
            "order_selection",
            "一次最多选择50笔订单",
            422,
        )
        query = query.where(Order.id.in_(selected))
    if import_id:
        actor.need("orders.write")
        job = d.scoped(db, ImportJob, import_id, actor.tenant_id)
        require(
            job.state == "completed",
            "import_not_finished",
            "导入尚未完成，请等待任务结束",
            409,
        )
        query = query.where(
            tuple_(Order.channel, Order.external_id).in_(
                [(row["channel"], row["external_id"]) for row in job.rows]
            )
        )
    if q:
        query = query.where(Order.external_id.contains(q) | Order.product.contains(q))
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.scalars(
        query.order_by(Order.created_at.desc(), Order.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    counts = dict(
        db.execute(
            select(Ticket.order_id, func.count())
            .where(
                Ticket.tenant_id == actor.tenant_id,
                Ticket.order_id.in_([o.id for o in rows]),
            )
            .group_by(Ticket.order_id)
        ).all()
    )
    return {
        "items": [d.data(o) | {"ticket_count": counts.get(o.id, 0)} for o in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.post("/orders", status_code=201)
def create_order(payload: OrderInput, actor: Actor = AUTH, db: Session = DB):
    actor.need("orders.write")
    exists = db.scalar(
        select(Order.id).where(
            Order.tenant_id == actor.tenant_id,
            Order.external_id == payload.external_id,
            Order.channel == payload.channel,
        )
    )
    require(not exists, "order_exists", "订单已存在，请核查已有记录")
    values = payload.model_dump()
    for key in ["placed_at", "shipped_at", "promised_at"]:
        if values[key]:
            values[key] = d.utc(values[key])
    order = Order(tenant_id=actor.tenant_id, **values)
    db.add(order)
    db.flush()
    d.audit(db, actor, "order.created", order.id)
    return d.data(order)


@router.put("/orders/{order_id}")
def update_order(
    order_id: str, payload: OrderUpdate, actor: Actor = AUTH, db: Session = DB
):
    actor.need("orders.write")
    order = d.scoped(db, Order, order_id, actor.tenant_id, lock=True)
    before = d.data(order)
    require(
        order.version == payload.version,
        "version_conflict",
        "订单版本已变化，请刷新后重试",
    )
    require(
        payload.refunded_cents >= order.refunded_cents,
        "refund_history_decrease",
        "累计已退金额不能直接减少；账目纠错需由维护人员核实处理",
    )
    if payload.refunded_cents != order.refunded_cents:
        actor.need("actions.verify")
        active = db.scalar(
            select(Action.id).where(
                Action.tenant_id == actor.tenant_id,
                Action.order_id == order.id,
                Action.state.not_in(["succeeded", "cancelled", "partially_succeeded"]),
            )
        )
        require(
            active is None,
            "order_action_active",
            "请先核验或对账未完成动作，再更新外部历史退款",
        )
    require(
        payload.paid_cents >= payload.refunded_cents + order.reserved_cents,
        "ledger_conflict",
        "新实付金额不能小于已退款和已占用金额之和",
    )
    values = payload.model_dump(exclude={"version"})
    if "tracking_phone" not in payload.model_fields_set:
        values.pop("tracking_phone", None)
    if any(values.get(k, getattr(order, k)) != getattr(order, k) for k in ["carrier_code", "tracking_number", "tracking_phone"]):
        order.logistics_snapshot = {}
        order.logistics_queried_at = None
    for key, value in values.items():
        setattr(order, key, d.utc(value) if isinstance(value, datetime) else value)
    order.version += 1
    order.verified_at = None
    order.verified_by = None
    d.audit(
        db,
        actor,
        "order.snapshot_updated",
        order.id,
        {"version": order.version, "before": before, "after": d.data(order)},
    )
    return d.data(order)


def intake_replay(db, actor, request_id, event, fingerprint):
    db.scalar(select(Tenant).where(Tenant.id == actor.tenant_id).with_for_update())
    record = db.scalar(
        select(AuditEvent).where(
            AuditEvent.tenant_id == actor.tenant_id,
            AuditEvent.event == event,
            AuditEvent.request_id == str(request_id),
        )
    )
    if record:
        require(
            record.details["payload_hash"] == fingerprint,
            "intake_conflict",
            "同一次提交的内容已变化，请重新打开表单",
            409,
        )
    return record


def update_intake_order(order, payload, version, actor, db):
    require(
        order.version == version,
        "version_conflict",
        "订单版本已变化，请刷新后重试",
        409,
    )
    values = payload.model_dump()
    if "tracking_phone" not in payload.model_fields_set:
        values.pop("tracking_phone", None)
    if any(
        (d.utc(v) if isinstance(v, datetime) else v) != getattr(order, k)
        for k, v in values.items()
    ):
        update_order(order.id, OrderUpdate(**values, version=version), actor, db)


@router.post("/orders/intake", status_code=201)
def intake_order(payload: OrderIntakeInput, actor: Actor = AUTH, db: Session = DB):
    actor.need("orders.write")
    if payload.ticket:
        actor.need("tickets.write")
    fingerprint = digest(payload.model_dump_json())
    prior = intake_replay(
        db, actor, payload.request_id, "order.intake_saved", fingerprint
    )
    if prior:
        ticket_id = prior.details.get("ticket_id")
        return {
            "order": d.data(d.scoped(db, Order, prior.resource_id, actor.tenant_id)),
            "ticket": d.data(d.scoped(db, Ticket, ticket_id, actor.tenant_id))
            if ticket_id
            else None,
        }
    if payload.existing_order_id:
        order = d.scoped(
            db, Order, payload.existing_order_id, actor.tenant_id, lock=True
        )
        update_intake_order(order, payload.order, payload.order_version, actor, db)
    else:
        saved = create_order(payload.order, actor, db)
        order = d.scoped(db, Order, saved["id"], actor.tenant_id)
    ticket = (
        d.create_ticket(
            db,
            actor,
            TicketInput(order_id=order.id, **payload.ticket.model_dump()),
            source_ref="intake:" + str(payload.request_id) + ":" + order.id,
        )
        if payload.ticket
        else None
    )
    d.audit(
        db,
        actor,
        "order.intake_saved",
        order.id,
        {"payload_hash": fingerprint, "ticket_id": ticket["id"] if ticket else None},
        request_id=str(payload.request_id),
    )
    return {"order": d.data(order), "ticket": ticket}


@router.post("/orders/{order_id}/verify")
def verify_order(
    order_id: str, payload: VersionInput, actor: Actor = AUTH, db: Session = DB
):
    actor.need("orders.write")
    order = d.scoped(db, Order, order_id, actor.tenant_id, lock=True)
    require(
        order.version == payload.version, "version_conflict", "订单版本变化，请刷新"
    )
    order.verified_at = now()
    order.verified_by = actor.user_id
    d.audit(db, actor, "order.snapshot_verified", order.id, {"version": order.version})
    return d.data(order)


@router.get("/imports/template")
def import_template(actor: Actor = AUTH):
    actor.need("orders.write")
    content = "订单号,商品名称,数量,实付金额,订单状态,下单时间,发货时间,承诺送达时间,客户,渠道,历史已退金额,快递公司编码,运单号,查件校验号码\n"
    return Response(
        "\ufeff" + content,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=orders-template.csv"},
    )


@router.post("/imports/preview")
def preview_import(
    file: UploadFile = File(...),
    mapping: str = Form("{}"),
    actor: Actor = AUTH,
    db: Session = DB,
):
    actor.need("orders.write")
    content = file.file.read(10 * 1024 * 1024 + 1)
    rows, errors = parse_csv(content, mapping)
    job = ImportJob(
        tenant_id=actor.tenant_id,
        filename=(file.filename or "orders.csv")[:200],
        content_hash=hashlib.sha256(content).hexdigest(),
        rows=rows,
        errors=errors,
        created_by=actor.user_id,
    )
    db.add(job)
    db.flush()
    d.audit(
        db,
        actor,
        "import.previewed",
        job.id,
        {"valid": len(rows), "errors": len(errors)},
    )
    return {
        "id": job.id,
        "valid_count": len(rows),
        "error_count": len(errors),
        "errors": errors[:200],
        "sample": [{k: v for k, v in row.items() if k != "tracking_phone"} for row in rows[:5]],
        "state": "preview",
    }


@router.post("/imports/{import_id}/confirm", status_code=202)
def confirm_import(import_id: str, actor: Actor = AUTH, db: Session = DB):
    actor.need("orders.write")
    job = d.scoped(db, ImportJob, import_id, actor.tenant_id, lock=True)
    require(
        job.state == "preview" and bool(job.rows),
        "import_state",
        "没有可导入记录，或任务已提交",
    )
    job.state = "queued"
    db.add(Job(tenant_id=actor.tenant_id, kind="import", resource_id=job.id))
    d.audit(db, actor, "import.confirmed", job.id)
    return {"id": job.id, "state": job.state}


@router.get("/imports")
def import_jobs(actor: Actor = AUTH, db: Session = DB):
    actor.need("orders.write")
    return [
        {k: v for k, v in d.data(job).items() if k not in {"rows", "errors"}}
        | {"valid_count": len(job.rows), "error_count": len(job.errors)}
        for job in db.scalars(
            select(ImportJob)
            .where(ImportJob.tenant_id == actor.tenant_id)
            .order_by(ImportJob.created_at.desc())
            .limit(50)
        )
    ]


def evidence_queue(actor):
    return select(Evidence.ticket_id).where(
        Evidence.tenant_id == actor.tenant_id, Evidence.verified_at.is_(None),
        or_(Evidence.expires_at.is_(None), Evidence.expires_at > now()),
    )


def work_scope(actor):
    """A work queue is a filter, not a replacement for tenant access control."""
    tenant = Ticket.tenant_id == actor.tenant_id
    if actor.membership.role == "staff":
        return tenant & (Ticket.owner_id == actor.user_id) & Ticket.state.not_in(["waiting_approval", "waiting_verification"])
    approvals = select(Proposal.ticket_id).where(
        Proposal.tenant_id == actor.tenant_id, Proposal.state == "pending",
        Proposal.created_by != actor.user_id,
    )
    verification = select(Action.ticket_id).where(
        Action.tenant_id == actor.tenant_id,
        Action.state.in_(["waiting_verification", "reconciling"]),
        or_(Action.executed_by.is_(None), Action.executed_by != actor.user_id),
    )
    if actor.membership.role == "supervisor":
        return tenant & (Ticket.state != "closed") & (Ticket.id.in_(approvals) | Ticket.id.in_(verification) | Ticket.id.in_(evidence_queue(actor)))
    if actor.membership.role == "executor":
        actions = select(Action.ticket_id).where(
            Action.tenant_id == actor.tenant_id,
            Action.state.in_(["approved", "claimed", "processing", "reconciling"]),
            or_(Action.claimed_by.is_(None), Action.claimed_by == actor.user_id),
        )
        return tenant & Ticket.id.in_(actions)
    if actor.membership.role == "auditor":
        return tenant & Ticket.state.in_(["reconciling", "closed", "resolved"])
    return tenant


@router.get("/tickets")
def tickets(
    q: str = "",
    state: str = "",
    page: int = 1,
    page_size: int = 25,
    scope: str = "team",
    order_id: str = "",
    intake_id: str = "",
    actor: Actor = AUTH,
    db: Session = DB,
):
    require(
        1 <= page and 1 <= page_size <= 100 and len(q) <= 100,
        "pagination",
        "分页或搜索参数无效",
        422,
    )
    query = (
        select(Ticket, Order)
        .join(Order, Ticket.order_id == Order.id)
        .where(Ticket.tenant_id == actor.tenant_id, Order.tenant_id == actor.tenant_id)
    )
    require(scope in {"team", "mine"}, "scope", "待办范围无效", 422)
    if scope == "mine":
        query = query.where(work_scope(actor))
    if order_id:
        d.scoped(db, Order, order_id, actor.tenant_id)
        query = query.where(Ticket.order_id == order_id)
    if intake_id:
        try:
            batch_id = str(UUID(intake_id))
        except ValueError:
            require(False, "intake_id", "建单批次无效", 422)
        query = query.where(Ticket.source_ref.startswith("intake:" + batch_id + ":"))
    if q:
        query = query.where(
            Ticket.title.contains(q)
            | Ticket.number.contains(q)
            | Order.external_id.contains(q)
        )
    if state == "open":
        query = query.where(Ticket.state != "closed")
    elif state == "evidence_review":
        actor.need("evidence.verify")
        query = query.where(Ticket.id.in_(evidence_queue(actor)), Ticket.state != "closed")
    elif state == "closed_today":
        today = d.shanghai_today()
        query = query.where(
            Ticket.state == "closed",
            Ticket.closed_at >= today,
            Ticket.closed_at < today + timedelta(days=1),
        )
    elif state:
        query = query.where(Ticket.state == state)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.execute(
        query.order_by(Ticket.created_at.desc(), Ticket.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return {
        "items": [d.data(t) | {"order": d.data(o)} for t, o in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.post("/tickets", status_code=201)
def create_ticket(payload: TicketInput, actor: Actor = AUTH, db: Session = DB):
    return d.create_ticket(db, actor, payload)


@router.post("/tickets/batch", status_code=201)
def batch_tickets(payload: BatchTicketInput, actor: Actor = AUTH, db: Session = DB):
    actor.need("tickets.write")
    fingerprint = digest(payload.model_dump_json())
    prior = intake_replay(
        db, actor, payload.request_id, "ticket.batch_created", fingerprint
    )
    if prior:
        return {
            "intake_id": str(payload.request_id),
            "items": [
                d.data(d.scoped(db, Ticket, key, actor.tenant_id))
                for key in prior.details["ticket_ids"]
            ],
        }
    selected = {row.order_id: row for row in payload.items}
    require(
        len(selected) == len(payload.items),
        "duplicate_order",
        "同一批次不能重复选择订单",
        422,
    )
    for key in sorted(selected):
        order = d.scoped(db, Order, key, actor.tenant_id, lock=True)
        require(
            order.version == selected[key].order_version,
            "version_conflict",
            f"订单 {order.external_id} 已变化，请刷新核对后重试",
            409,
        )
    items = []
    for row in payload.items:
        if row.order:
            actor.need("orders.write")
            order = d.scoped(db, Order, row.order_id, actor.tenant_id)
            update_intake_order(order, row.order, row.order_version, actor, db)
        items.append(
            d.create_ticket(
                db,
                actor,
                TicketInput(**row.model_dump(exclude={"order_version", "order"})),
                source_ref="intake:" + str(payload.request_id) + ":" + row.order_id,
            )
        )
    d.audit(
        db,
        actor,
        "ticket.batch_created",
        str(payload.request_id),
        {"payload_hash": fingerprint, "ticket_ids": [t["id"] for t in items]},
        request_id=str(payload.request_id),
    )
    return {"intake_id": str(payload.request_id), "items": items}


@router.get("/tickets/{ticket_id}")
def ticket_detail(ticket_id: str, actor: Actor = AUTH, db: Session = DB):
    return d.ticket_detail(db, actor, ticket_id)


@router.post("/tickets/{ticket_id}/logistics")
def query_ticket_logistics(ticket_id: str, actor: Actor = AUTH):
    from .logistics import query_logistics

    return query_logistics(actor.tenant_id, ticket_id, actor.user_id)


@router.post("/tickets/{ticket_id}/evidence", status_code=201)
def add_evidence(
    ticket_id: str,
    title: str = Form(...),
    kind: str = Form(...),
    source: str = Form(...),
    body: str = Form(""),
    observed_at: str = Form(...),
    expires_at: str = Form(""),
    file: UploadFile | None = File(None),
    actor: Actor = AUTH,
    db: Session = DB,
):
    actor.need("evidence.write")
    ticket = d.scoped(db, Ticket, ticket_id, actor.tenant_id)
    require(
        kind
        in {
            "carrier_confirmation",
            "logistics",
            "payment",
            "inventory",
            "customer_request",
            "execution_receipt",
            "reconciliation",
        },
        "evidence_kind",
        "证据类型不支持",
        422,
    )
    require(
        1 <= len(title) <= 160 and 1 <= len(source) <= 200 and len(body) <= 30000,
        "evidence_fields",
        "证据字段超出限制",
        422,
    )
    try:
        observed = d.utc(datetime.fromisoformat(observed_at.replace("Z", "+00:00")))
        expires = (
            d.utc(datetime.fromisoformat(expires_at.replace("Z", "+00:00")))
            if expires_at
            else None
        )
    except ValueError:
        require(False, "evidence_time", "证据时间格式不正确", 422)
    require(
        observed <= now() + timedelta(minutes=5)
        and (expires is None or expires > observed),
        "evidence_time",
        "证据时间范围无效",
        422,
    )
    key, mime, filename = None, None, None
    if file:
        content = file.file.read(20 * 1024 * 1024 + 1)
        mime, extracted, content_hash = validate_file(content, file.filename or "")
        key = store_file(actor.tenant_id, content, mime)
        db.info.setdefault("pending_files", []).append(key)
        filename = (file.filename or "evidence")[:200]
        body = (body + "\n" + extracted).strip()[:30000]
    else:
        require(len(body) >= 5, "evidence_content", "请填写证据摘要或上传文件", 422)
        content_hash = hashlib.sha256(body.encode()).hexdigest()
    ticket = d.scoped(db, Ticket, ticket_id, actor.tenant_id, lock=True)
    evidence = Evidence(
        tenant_id=actor.tenant_id,
        ticket_id=ticket.id,
        kind=kind,
        title=title,
        source=source,
        body=body,
        observed_at=observed,
        expires_at=expires,
        file_key=key,
        mime=mime,
        filename=filename,
        content_hash=content_hash,
        uploaded_by=actor.user_id,
    )
    db.add(evidence)
    ticket.evidence_version += 1
    ticket.version += 1
    db.flush()
    d.audit(
        db,
        actor,
        "evidence.added",
        ticket.id,
        {"evidence_id": evidence.id, "kind": kind},
    )
    return d.data(evidence)


@router.post("/evidence/{evidence_id}/verify")
def verify_evidence(evidence_id: str, actor: Actor = AUTH, db: Session = DB):
    actor.need("evidence.verify")
    evidence = d.scoped(db, Evidence, evidence_id, actor.tenant_id)
    ticket = d.scoped(db, Ticket, evidence.ticket_id, actor.tenant_id, lock=True)
    require(
        not evidence.expires_at or evidence.expires_at > now(),
        "evidence_expired",
        "证据已过期",
    )
    if not evidence.verified_at:
        evidence.verified_at = now()
        evidence.verified_by = actor.user_id
        ticket.evidence_version += 1
        ticket.version += 1
        d.audit(db, actor, "evidence.verified", ticket.id, {"evidence_id": evidence.id})
    return d.data(evidence)


@router.get("/evidence/{evidence_id}/file")
def get_file(evidence_id: str, actor: Actor = AUTH, db: Session = DB):
    evidence = d.scoped(db, Evidence, evidence_id, actor.tenant_id)
    require(evidence.file_key is not None, "no_file", "此证据没有附件", 404)
    target = file_location(evidence.file_key, evidence.filename)
    d.audit(db, actor, "evidence.downloaded", evidence.id)
    from pathlib import Path
    from urllib.parse import quote

    headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"}
    if isinstance(target, Path):
        return FileResponse(
            target,
            filename=evidence.filename,
            media_type=evidence.mime,
            headers=headers,
        )
    headers["Content-Disposition"] = "attachment; filename*=UTF-8''" + quote(
        evidence.filename or "evidence", safe=""
    )

    def chunks():
        try:
            while chunk := target.read(65536):
                yield chunk
        finally:
            target.close()

    return StreamingResponse(chunks(), media_type=evidence.mime, headers=headers)


@router.post("/tickets/{ticket_id}/proposals", status_code=201)
def submit_proposal(
    ticket_id: str, payload: ProposalInput, actor: Actor = AUTH, db: Session = DB
):
    return d.data(d.create_proposal(db, actor, ticket_id, payload))


@router.get("/approvals")
def approvals(page: int = 1, actor: Actor = AUTH, db: Session = DB):
    actor.need("approvals.decide")
    require(page >= 1, "pagination", "页码无效", 422)
    filters = [Proposal.tenant_id == actor.tenant_id, Proposal.state == "pending"]
    if actor.membership.role == "supervisor":
        filters.append(Proposal.created_by != actor.user_id)
    total = db.scalar(
        select(func.count(Proposal.id)).where(*filters)
    )
    items = [
        {**d.data(p), "ticket": d.data(t), "order": d.data(o)}
        for p, t, o in db.execute(
            select(Proposal, Ticket, Order)
            .join(Ticket, Proposal.ticket_id == Ticket.id)
            .join(Order, Ticket.order_id == Order.id)
            .where(
                Proposal.tenant_id == actor.tenant_id,
                Ticket.tenant_id == actor.tenant_id,
                Order.tenant_id == actor.tenant_id,
                Proposal.state == "pending",
                *filters,
            )
            .order_by(Proposal.created_at)
            .offset((page - 1) * 50)
            .limit(50)
        )
    ]
    return {"items": items, "total": total, "page": page, "page_size": 50}


@router.post("/approvals/{proposal_id}/decisions")
def decide_proposal(
    proposal_id: str, payload: DecisionInput, actor: Actor = AUTH, db: Session = DB
):
    action = d.decide(db, actor, proposal_id, payload)
    return {"ok": True, "action": d.data(action) if action else None}


@router.get("/executions")
def executions(
    page: int = 1, state: str = "open", actor: Actor = AUTH, db: Session = DB
):
    require(
        actor.can("actions.execute") or actor.can("actions.verify"),
        "forbidden",
        "当前角色没有执行或核验权限",
        403,
    )
    require(page >= 1 and state in {"open", "all"}, "pagination", "页码或筛选无效", 422)
    filters = [Action.tenant_id == actor.tenant_id]
    if actor.membership.role == "staff":
        owned = select(Ticket.id).where(Ticket.tenant_id == actor.tenant_id, Ticket.owner_id == actor.user_id)
        filters.append(Action.ticket_id.in_(owned) | (Action.claimed_by == actor.user_id))
    elif actor.membership.role == "supervisor":
        filters.extend([Action.state.in_(["waiting_verification", "reconciling"]),
                        or_(Action.executed_by.is_(None), Action.executed_by != actor.user_id)])
    elif actor.membership.role == "executor":
        filters.append(or_(Action.claimed_by.is_(None), Action.claimed_by == actor.user_id))
    if state == "open":
        filters.append(
            Action.state.not_in(["succeeded", "cancelled", "partially_succeeded"])
        )
    total = db.scalar(select(func.count(Action.id)).where(*filters))
    items = [
        {**d.data(a), "ticket": d.data(t), "order": d.data(o)}
        for a, t, o in db.execute(
            select(Action, Ticket, Order)
            .join(Ticket, Action.ticket_id == Ticket.id)
            .join(Order, Action.order_id == Order.id)
            .where(
                Action.tenant_id == actor.tenant_id,
                Ticket.tenant_id == actor.tenant_id,
                Order.tenant_id == actor.tenant_id,
                *filters,
            )
            .order_by(Action.created_at.desc())
            .offset((page - 1) * 50)
            .limit(50)
        )
    ]
    return {"items": items, "total": total, "page": page, "page_size": 50}


@router.post("/actions/{action_id}/claim")
def claim(action_id: str, payload: ClaimInput, actor: Actor = AUTH, db: Session = DB):
    return d.data(d.claim_action(db, actor, action_id, payload))


@router.post("/actions/{action_id}/cancel")
def cancel_unclaimed(
    action_id: str, payload: ReasonInput, actor: Actor = AUTH, db: Session = DB
):
    actor.need("approvals.decide")
    action = d.scoped(db, Action, action_id, actor.tenant_id)
    ticket, order = d.locked_ticket(db, action.ticket_id, actor.tenant_id)
    action = d.scoped(db, Action, action_id, actor.tenant_id, lock=True)
    require(
        action.state == "approved" and not action.claimed_by,
        "action_may_have_executed",
        "已领取或结果未知的动作必须对账，不能直接撤销",
    )
    require(
        order.reserved_cents >= action.amount_cents,
        "ledger_conflict",
        "金额占用账本冲突",
    )
    order.reserved_cents -= action.amount_cents
    action.state = "cancelled"
    action.note = payload.note
    ticket.state = "draft"
    ticket.version += 1
    proposal = d.scoped(db, Proposal, action.proposal_id, actor.tenant_id)
    proposal.state = "cancelled"
    d.audit(db, actor, "action.unclaimed_cancelled", action.id)
    return d.data(action)


@router.post("/actions/{action_id}/receipt")
def receipt(
    action_id: str, payload: ReceiptInput, actor: Actor = AUTH, db: Session = DB
):
    return d.data(d.record_receipt(db, actor, action_id, payload))


@router.post("/actions/{action_id}/verify")
def verify(
    action_id: str, payload: VerificationInput, actor: Actor = AUTH, db: Session = DB
):
    return d.data(d.verify_action(db, actor, action_id, payload))


@router.post("/actions/{action_id}/reconcile")
def reconcile(
    action_id: str, payload: ReconcileInput, actor: Actor = AUTH, db: Session = DB
):
    return d.data(d.reconcile_action(db, actor, action_id, payload))


@router.post("/actions/{action_id}/handoff")
def handoff(
    action_id: str, payload: HandoffInput, actor: Actor = AUTH, db: Session = DB
):
    return d.data(d.handoff_action(db, actor, action_id, payload))


@router.post("/tickets/{ticket_id}/communications")
def communicate(
    ticket_id: str, payload: CommunicationInput, actor: Actor = AUTH, db: Session = DB
):
    actor.need("tickets.write")
    ticket = d.scoped(db, Ticket, ticket_id, actor.tenant_id)
    item = Communication(
        tenant_id=actor.tenant_id,
        ticket_id=ticket.id,
        created_by=actor.user_id,
        **payload.model_dump(),
    )
    db.add(item)
    db.flush()
    d.audit(db, actor, "ticket.communication_recorded", ticket.id)
    return d.data(item)


@router.post("/tickets/{ticket_id}/close")
def close(ticket_id: str, actor: Actor = AUTH, db: Session = DB):
    return d.data(d.close_ticket(db, actor, ticket_id))


@router.get("/policies")
def policies(actor: Actor = AUTH, db: Session = DB):
    return [
        d.data(p)
        for p in db.scalars(
            select(Policy)
            .where(Policy.tenant_id == actor.tenant_id)
            .order_by(Policy.created_at.desc())
            .limit(200)
        )
    ]


@router.post("/knowledge/ask", status_code=202)
def ask_knowledge(payload: KnowledgeInput, actor: Actor = AUTH, db: Session = DB):
    from .agent import runtime_signature
    from .knowledge import retrieve

    actor.need("runs.start")
    require(settings().model_enabled, "model_unconfigured", "模型未配置，暂不能生成知识回答", 503)
    require(actor.tenant.config.get("agent_enabled", True), "agent_paused", "商家已暂停模型任务")
    db.scalar(select(Tenant).where(Tenant.id == actor.tenant_id).with_for_update())
    count = db.scalar(select(func.count(AgentRun.id)).where(
        AgentRun.tenant_id == actor.tenant_id, AgentRun.state.in_(["queued", "running"])))
    require(count < 10, "tenant_concurrency_limit", "模型任务已达上限，请稍后再试", 429)
    at = d.utc(payload.order_date) if payload.order_date else now()
    sources = retrieve(db, actor.tenant_id, payload.question, payload.channel, at)
    require(bool(sources), "knowledge_no_match", "没有找到适用的已发布政策，请补充知识或转人工核实", 422)
    run = AgentRun(
        tenant_id=actor.tenant_id, ticket_id=None, requester_id=actor.user_id,
        budget_microusd=settings().llm_budget_microusd, model=settings().llm_model,
        prompt_version="knowledge-v1",
        checkpoint={"phase": "plan", "done": False, "runtime": runtime_signature(),
                    "knowledge": {"question": payload.question, "channel": payload.channel,
                                  "at": at.isoformat(), "sources": sources}},
    )
    db.add(run)
    db.flush()
    db.add(Job(tenant_id=actor.tenant_id, kind="agent", resource_id=run.id))
    d.event(db, run, "knowledge.queued", {"source_count": len(sources)})
    d.audit(db, actor, "knowledge.asked", run.id)
    return d.public_run(run, actor)


@router.post("/policies", status_code=201)
def create_policy(payload: PolicyInput, actor: Actor = AUTH, db: Session = DB):
    actor.need("policies.manage")
    values = payload.model_dump()
    values["effective_from"] = d.utc(values["effective_from"])
    if values["effective_until"]:
        values["effective_until"] = d.utc(values["effective_until"])
    require(
        values["effective_until"] is None
        or values["effective_until"] > values["effective_from"],
        "policy_time",
        "政策时间范围无效",
        422,
    )
    latest = (
        db.scalar(
            select(func.max(Policy.version)).where(
                Policy.tenant_id == actor.tenant_id, Policy.title == payload.title
            )
        )
        or 0
    )
    policy = Policy(
        tenant_id=actor.tenant_id,
        **values,
        version=latest + 1,
        content_hash=hashlib.sha256(
            json.dumps(payload.model_dump(mode="json"), sort_keys=True).encode()
        ).hexdigest(),
    )
    db.add(policy)
    db.flush()
    d.audit(db, actor, "policy.created", policy.id)
    return d.data(policy)


@router.post("/policies/{policy_id}/publish")
def publish_policy(policy_id: str, actor: Actor = AUTH, db: Session = DB):
    actor.need("policies.manage")
    policy = d.scoped(db, Policy, policy_id, actor.tenant_id, lock=True)
    require(policy.state == "draft", "policy_state", "仅草稿可以发布")
    policy.state = "published"
    policy.published_by = actor.user_id
    policy.published_at = now()
    d.audit(db, actor, "policy.published", policy.id)
    return d.data(policy)


@router.post("/policies/{policy_id}/retire")
def retire_policy(policy_id: str, actor: Actor = AUTH, db: Session = DB):
    actor.need("policies.manage")
    policy = d.scoped(db, Policy, policy_id, actor.tenant_id, lock=True)
    require(policy.state == "published", "policy_state", "仅已发布政策可以停用")
    policy.state = "retired"
    d.audit(db, actor, "policy.retired", policy.id)
    return d.data(policy)


@router.get("/audit")
def audit_events(page: int = 1, actor: Actor = AUTH, db: Session = DB):
    actor.need("audit.read")
    require(page >= 1, "pagination", "页码无效", 422)
    base = select(AuditEvent).where(AuditEvent.tenant_id == actor.tenant_id)
    return {
        "items": [
            d.data(e)
            for e in db.scalars(
                base.order_by(AuditEvent.created_at.desc())
                .offset((page - 1) * 50)
                .limit(50)
            )
        ],
        "total": db.scalar(select(func.count()).select_from(base.subquery())),
        "page": page,
    }


@router.get("/overview")
def overview(scope: str = "team", actor: Actor = AUTH, db: Session = DB):
    require(scope in {"team", "mine"}, "scope", "待办范围无效", 422)
    queue = work_scope(actor) if scope == "mine" else Ticket.tenant_id == actor.tenant_id
    states = dict(
        db.execute(
            select(Ticket.state, func.count())
            .where(queue)
            .group_by(Ticket.state)
        ).all()
    )
    today = d.shanghai_today()
    closed = db.scalar(
        select(func.count(Ticket.id)).where(
            Ticket.tenant_id == actor.tenant_id,
            queue,
            Ticket.state == "closed",
            Ticket.closed_at >= today,
            Ticket.closed_at < today + timedelta(days=1),
        )
    )
    recent = [
        d.data(t) | {"order": d.data(o)}
        for t, o in db.execute(
            select(Ticket, Order)
            .join(Order, Ticket.order_id == Order.id)
            .where(
                Ticket.tenant_id == actor.tenant_id,
                Order.tenant_id == actor.tenant_id,
                Ticket.state != "closed",
                queue,
            )
            .order_by(Ticket.due_at)
            .limit(8)
        )
    ]
    trend = []
    for day in range(6, -1, -1):
        start = today - timedelta(days=day)
        count = db.scalar(
            select(func.count(Ticket.id)).where(
                Ticket.tenant_id == actor.tenant_id,
                queue,
                Ticket.closed_at >= start,
                Ticket.closed_at < start + timedelta(days=1),
            )
        )
        trend.append(
            {"date": (start + timedelta(hours=8)).strftime("%m/%d"), "closed": count}
        )
    running = db.scalar(
        select(func.count(AgentRun.id)).where(
            AgentRun.tenant_id == actor.tenant_id,
            AgentRun.state.in_(["queued", "running"]),
        )
    )
    cost = (
        db.scalar(
            select(func.sum(ModelCall.cost_microusd)).where(
                ModelCall.tenant_id == actor.tenant_id
            )
        )
        or 0
    )
    return {
        "open": sum(v for k, v in states.items() if k != "closed"),
        "evidence_review": db.scalar(select(func.count(Ticket.id)).where(
            queue, Ticket.id.in_(evidence_queue(actor)), Ticket.state != "closed")) if actor.can("evidence.verify") else 0,
        "waiting_approval": states.get("waiting_approval", 0),
        "closed_today": closed,
        "states": states,
        "recent": recent,
        "trend": trend,
        "running": running,
        "cost_microusd": cost if actor.can("members.manage") else None,
        "updated_at": now().isoformat() + "Z",
        "model_enabled": settings().model_enabled,
    }


@router.get("/settings")
def tenant_settings(actor: Actor = AUTH):
    actor.need("members.manage")
    return {
        "name": actor.tenant.name,
        "config": actor.tenant.config,
        "model_enabled": settings().model_enabled,
        "model": settings().llm_model or None,
        "platform_connected": False,
        "environment": settings().app_env,
    }


@router.get("/operations")
def operations(actor: Actor = AUTH, db: Session = DB):
    actor.need("audit.read")
    from .models import WorkerHeartbeat
    from .telemetry import WORKER_HEARTBEAT_SECONDS

    pending = db.scalar(
        select(func.count(Job.id)).where(
            Job.tenant_id == actor.tenant_id, Job.state == "pending"
        )
    )
    failed = db.scalar(
        select(func.count(Job.id)).where(
            Job.tenant_id == actor.tenant_id, Job.state == "failed"
        )
    )
    expired = db.scalar(
        select(func.count(Job.id)).where(
            Job.tenant_id == actor.tenant_id,
            Job.state == "running",
            Job.lease_until < now(),
        )
    )
    oldest = db.scalar(
        select(func.min(Job.created_at)).where(
            Job.tenant_id == actor.tenant_id, Job.state == "pending"
        )
    )
    alive = db.scalar(
        select(func.count())
        .select_from(WorkerHeartbeat)
        .where(
            WorkerHeartbeat.seen_at
            > now() - timedelta(seconds=WORKER_HEARTBEAT_SECONDS)
        )
    )
    return {
        "worker_available": bool(alive),
        "pending_jobs": pending,
        "failed_jobs": failed,
        "expired_leases": expired,
        "oldest_pending_seconds": max(0, (now() - oldest).total_seconds())
        if oldest
        else 0,
        "reserved_cents": db.scalar(
            select(func.coalesce(func.sum(Order.reserved_cents), 0)).where(
                Order.tenant_id == actor.tenant_id
            )
        ),
        "reconciling_actions": db.scalar(
            select(func.count(Action.id)).where(
                Action.tenant_id == actor.tenant_id, Action.state == "reconciling"
            )
        ),
    }


@router.put("/settings")
def update_settings(payload: TenantConfigInput, actor: Actor = AUTH, db: Session = DB):
    actor.need("members.manage")
    actor.tenant.name = payload.name
    actor.tenant.config = payload.model_dump(exclude={"name"})
    d.audit(db, actor, "tenant.settings_updated", actor.tenant_id)
    return {"ok": True}


@router.get("/runs")
def runs(actor: Actor = AUTH, db: Session = DB):
    rows = db.scalars(
        select(AgentRun)
        .where(AgentRun.tenant_id == actor.tenant_id, AgentRun.ticket_id.is_not(None))
        .order_by(AgentRun.created_at.desc())
        .limit(100)
    )
    return [d.public_run(run, actor) for run in rows]


@router.post("/tickets/{ticket_id}/runs", status_code=202)
def start_run(ticket_id: str, actor: Actor = AUTH, db: Session = DB):
    return d.public_run(d.queue_investigation(db, actor, ticket_id), actor)


@router.get("/runs/{run_id}")
def run_detail(run_id: str, actor: Actor = AUTH, db: Session = DB):
    run = d.scoped(db, AgentRun, run_id, actor.tenant_id)
    result = d.public_run(run, actor)
    result["events"] = [
        d.data(e)
        for e in db.scalars(
            select(RunEvent)
            .where(RunEvent.tenant_id == actor.tenant_id, RunEvent.run_id == run.id)
            .order_by(RunEvent.seq)
            .limit(200)
        )
    ]
    if actor.can("members.manage"):
        result["calls"] = [
            d.data(c)
            for c in db.scalars(
                select(ModelCall).where(
                    ModelCall.tenant_id == actor.tenant_id, ModelCall.run_id == run.id
                )
            )
        ]
    return result


@router.post("/runs/{run_id}/resume", status_code=202)
def resume_run(run_id: str, actor: Actor = AUTH, db: Session = DB):
    actor.need("runs.start")
    require(settings().model_enabled, "model_unconfigured", "模型尚未配置", 503)
    tenant = db.scalar(
        select(Tenant)
        .where(Tenant.id == actor.tenant_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    require(
        tenant.config.get("agent_enabled", True), "agent_paused", "商家已暂停智能调查"
    )
    run = d.scoped(db, AgentRun, run_id, actor.tenant_id, lock=True)
    require(
        run.state == "waiting_input", "run_not_resumable", "当前运行不在等待补证阶段"
    )
    count = db.scalar(
        select(func.count(AgentRun.id)).where(
            AgentRun.tenant_id == actor.tenant_id,
            AgentRun.state.in_(["queued", "running"]),
        )
    )
    require(
        count < 10,
        "tenant_concurrency_limit",
        "工作空间同时调查任务已达上限，请稍后再试",
        429,
    )
    ticket = d.scoped(db, Ticket, run.ticket_id, actor.tenant_id)
    require(
        ticket.state in {"new", "investigating", "waiting_evidence", "draft"},
        "run_stage",
        "工单已由人工推进，不能恢复调查",
    )
    run.state = "queued"
    run.checkpoint = {
        **run.checkpoint,
        "phase": "plan",
        "observations": [],
        "done": False,
        "validation_errors": 0,
    }
    db.add(Job(tenant_id=actor.tenant_id, kind="agent", resource_id=run.id))
    d.event(db, run, "run.resumed", {"actor_id": actor.user_id})
    d.audit(db, actor, "agent.resumed", run.ticket_id)
    return d.public_run(run, actor)


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str, actor: Actor = AUTH, db: Session = DB):
    actor.need("runs.start")
    run = d.scoped(db, AgentRun, run_id, actor.tenant_id, lock=True)
    require(
        run.state in {"queued", "running", "waiting_input"},
        "run_not_cancellable",
        "运行已结束",
    )
    run.state = "cancelled"
    run.active_key = None
    run.completed_at = now()
    d.event(db, run, "run.cancelled", {"actor_id": actor.user_id})
    d.audit(db, actor, "agent.cancelled", run.ticket_id or run.id)
    return {"ok": True}


@router.get("/runs/{run_id}/events")
def run_events(
    run_id: str, request: Request, after: int = 0, actor: Actor = AUTH, db: Session = DB
):
    import asyncio
    from .database import SessionLocal

    d.scoped(db, AgentRun, run_id, actor.tenant_id)
    try:
        cursor = max(after, int(request.headers.get("last-event-id", "0")))
    except ValueError:
        require(False, "event_cursor", "事件序号无效", 422)
    require(cursor >= 0, "event_cursor", "事件序号无效", 422)

    def fetch_frames(current_cursor):
        with SessionLocal() as current:
            active = current.scalar(
                select(SessionRecord.id).where(
                    SessionRecord.id == actor.session.id,
                    SessionRecord.expires_at > now(),
                )
            )
            member = current.scalar(
                select(Membership).where(
                    Membership.tenant_id == actor.tenant_id,
                    Membership.user_id == actor.user_id,
                )
            )
            user = current.get(User, actor.user_id)
            tenant = current.get(Tenant, actor.tenant_id)
            if (
                not active
                or not member
                or not member.enabled
                or not user
                or not user.enabled
                or not tenant
                or not tenant.enabled
            ):
                return current_cursor, [], False
            set_tenant(current, actor.tenant_id)
            events = current.scalars(
                select(RunEvent)
                .where(
                    RunEvent.tenant_id == actor.tenant_id,
                    RunEvent.run_id == run_id,
                    RunEvent.seq > current_cursor,
                )
                .order_by(RunEvent.seq)
                .limit(100)
            ).all()
            frames = []
            for item in events:
                current_cursor = item.seq
                frames.append(
                    f"id: {item.seq}\nevent: update\ndata: {json.dumps(d.data(item), ensure_ascii=False)}\n\n"
                )
            return current_cursor, frames, True

    async def stream():
        nonlocal cursor
        for _ in range(60):
            if await request.is_disconnected():
                break
            cursor, frames, allowed = await run_in_threadpool(fetch_frames, cursor)
            if not allowed:
                yield "event: access_revoked\ndata: {}\n\n"
                return
            for frame in frames:
                yield frame
            yield ": heartbeat\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/invitations/{token_value}")
def invitation_info(token_value: str, db: Session = DB):
    require(
        20 <= len(token_value) <= 100, "invalid_invite", "邀请链接无效或已过期", 422
    )
    token = db.scalar(
        select(AuthToken).where(
            AuthToken.token_hash == digest(token_value), AuthToken.kind == "invite"
        )
    )
    require(
        token and token.used_at is None and token.expires_at > now(),
        "invalid_invite",
        "邀请链接无效或已过期",
        422,
    )
    tenant = db.get(Tenant, token.tenant_id)
    require(tenant and tenant.enabled, "invalid_invite", "邀请链接无效或已过期", 422)
    return {
        "tenant": tenant.name,
        "email": token.email,
        "role": token.role,
        "expires_at": token.expires_at.isoformat() + "Z",
    }


@router.get("/runs/{run_id}/review")
def run_review(run_id: str, actor: Actor = AUTH, db: Session = DB):
    d.scoped(db, AgentRun, run_id, actor.tenant_id)
    event = current_reviews(db, actor.tenant_id, [run_id]).get(run_id)
    return {"review": d.data(event) if event else None}


def current_reviews(db, tenant_id, run_ids):
    if not run_ids:
        return {}
    reviews = {}
    for event in db.scalars(
        select(RunEvent)
        .where(
            RunEvent.tenant_id == tenant_id,
            RunEvent.run_id.in_(run_ids),
            RunEvent.kind.in_(["evaluation.reviewed", "run.resumed"]),
        )
        .order_by(RunEvent.seq)
    ):
        if event.kind == "run.resumed":
            reviews.pop(event.run_id, None)
        else:
            reviews[event.run_id] = event
    return reviews


@router.post("/runs/{run_id}/review")
def review_run(
    run_id: str, payload: RunReviewInput, actor: Actor = AUTH, db: Session = DB
):
    actor.need("audit.read")
    run = d.scoped(db, AgentRun, run_id, actor.tenant_id, lock=True)
    require(run.ticket_id is not None, "review_type", "此复核入口仅用于工单调查", 422)
    require(
        run.state in {"completed", "human_needed", "waiting_input", "cancelled"},
        "run_not_finished",
        "请等待运行结束或暂停后复核",
    )
    require(
        run.requester_id != actor.user_id,
        "self_review",
        "调查发起人不能独立复核自身结果",
        403,
    )
    d.event(
        db,
        run,
        "evaluation.reviewed",
        payload.model_dump() | {"reviewer_id": actor.user_id},
    )
    d.audit(
        db,
        actor,
        "agent.quality_reviewed",
        run.id,
        payload.model_dump(exclude={"note"}),
    )
    return {"ok": True}


@router.get("/evaluations")
def evaluations(actor: Actor = AUTH, db: Session = DB):
    since = now() - timedelta(days=30)
    runs = db.scalars(
        select(AgentRun).where(
            AgentRun.tenant_id == actor.tenant_id, AgentRun.created_at >= since,
            AgentRun.ticket_id.is_not(None),
        )
    ).all()
    ids = [run.id for run in runs]
    reviews = {
        run_id: event.data
        for run_id, event in current_reviews(db, actor.tenant_id, ids).items()
    }
    count = len(reviews)
    terminal = [run for run in runs if run.completed_at]
    errors = sum(bool(run.error) for run in terminal)
    return {
        "period_days": 30,
        "runs": len(runs),
        "reviewed": count,
        **{
            name: sum(bool(v.get(key)) for v in reviews.values()) / count
            if count
            else None
            for name, key in [
                ("correct", "business_correct"),
                ("evidence_supported", "evidence_supported"),
                ("handoff_appropriate", "handoff_appropriate"),
            ]
        },
        "review_coverage": count / len(runs) if runs else None,
        "runtime_error_rate": errors / len(terminal) if terminal else None,
        "terminal_runs": len(terminal),
        "avg_duration_ms": sum(
            (run.completed_at - run.created_at).total_seconds() * 1000
            for run in terminal
        )
        / len(terminal)
        if terminal
        else None,
    }


@router.post("/actions/{action_id}/settle")
def settle_partial(
    action_id: str,
    payload: ActualSettlementInput,
    actor: Actor = AUTH,
    db: Session = DB,
):
    return d.data(d.settle_actual(db, actor, action_id, payload))


@router.post("/tickets/{ticket_id}/resolve-without-payment")
def resolve_without_payment(
    ticket_id: str,
    payload: NonfinancialResolutionInput,
    actor: Actor = AUTH,
    db: Session = DB,
):
    return d.data(d.resolve_without_payment(db, actor, ticket_id, payload))
