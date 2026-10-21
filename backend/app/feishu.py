"""Feishu Base automation: authenticated record intake and live status lookup."""

import hmac
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Path, Request
from pydantic import Field, field_validator
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from . import domain as d
from .config import settings
from .database import set_tenant
from .models import Membership, Order, Tenant, Ticket, User, now, uid
from .schemas import Input
from .security import Actor, current_actor, digest, get_db, require

router = APIRouter(prefix="/integrations/feishu", tags=["Feishu Base"])
IDENTIFIER = r"^[A-Za-z0-9_-]+$"
ISSUES = {
    "物流长期未更新": "logistics_delay",
    "超过承诺送达时效": "delivery_overdue",
    "承运商已确认丢件": "confirmed_loss",
}
STATES = {
    "new": "待调查",
    "investigating": "调查中",
    "draft": "待修改",
    "waiting_evidence": "待补证",
    "waiting_approval": "待审批",
    "waiting_execution": "待人工执行",
    "waiting_verification": "待核验",
    "reconciling": "对账中",
    "resolved": "已解决",
    "closed": "已关闭",
}


class FeishuRecord(Input):
    auto_investigate: bool = True
    base_id: str = Field(min_length=1, max_length=100, pattern=IDENTIFIER)
    table_id: str = Field(min_length=1, max_length=100, pattern=IDENTIFIER)
    record_id: str = Field(min_length=1, max_length=100, pattern=IDENTIFIER)
    channel: str = Field(min_length=1, max_length=50)
    external_id: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=2, max_length=200)
    request: str = Field(min_length=2, max_length=4000)
    issue: Literal["logistics_delay", "delivery_overdue", "confirmed_loss"]

    @field_validator("issue", mode="before")
    @classmethod
    def normalize_issue(cls, value):
        return ISSUES.get(value, value) if isinstance(value, str) else value

    @property
    def source_ref(self):
        return f"feishu:{self.base_id}:{self.table_id}:{self.record_id}"


def integration_actor(request: Request, db: Session = Depends(get_db)):
    cfg = settings()
    require(
        len(cfg.feishu_intake_token) >= 32
        and all(
            [
                cfg.feishu_tenant_id,
                cfg.feishu_owner_id,
                cfg.feishu_base_id,
                cfg.feishu_table_id,
            ]
        ),
        "feishu_not_configured",
        "飞书表格入口尚未配置",
        503,
    )
    require(
        hmac.compare_digest(
            digest(request.headers.get("authorization", "")),
            digest("Bearer " + cfg.feishu_intake_token),
        ),
        "invalid_integration_token",
        "表格接入凭证无效",
        401,
    )
    tenant = db.get(Tenant, cfg.feishu_tenant_id)
    user = db.get(User, cfg.feishu_owner_id)
    member = db.scalar(
        select(Membership).where(
            Membership.tenant_id == cfg.feishu_tenant_id,
            Membership.user_id == cfg.feishu_owner_id,
        )
    )
    require(
        tenant
        and tenant.enabled
        and user
        and user.enabled
        and member
        and member.enabled,
        "integration_account_disabled",
        "表格所属空间或负责人不可用",
        403,
    )
    actor = Actor(user, tenant, member, None)
    actor.need("tickets.write")
    set_tenant(db, tenant.id)
    request.state.tenant_id = tenant.id
    request.state.actor_id = user.id
    return actor


def result(ticket, created=False):
    return {
        "created": created,
        "ticket_id": ticket.id,
        "number": ticket.number,
        "state": ticket.state,
        "state_label": STATES.get(ticket.state, ticket.state),
        "url": settings().public_origin.rstrip("/") + "/tickets/" + ticket.id,
        "due_at": d.data(ticket)["due_at"],
        "updated_at": d.data(ticket)["updated_at"],
    }


def intake(db, actor, payload):
    actor.need("tickets.write")
    order = db.scalar(
        select(Order).where(
            Order.tenant_id == actor.tenant_id,
            Order.channel == payload.channel,
            Order.external_id == payload.external_id,
        )
    )
    require(
        order is not None,
        "order_not_found",
        "未找到该渠道下的订单，请先导入订单或检查渠道和订单号",
        404,
    )
    insert = pg_insert if db.bind.dialect.name == "postgresql" else sqlite_insert
    created_id = db.scalar(
        insert(Ticket)
        .values(
            tenant_id=actor.tenant_id,
            source_ref=payload.source_ref,
            order_id=order.id,
            title=payload.title,
            request=payload.request,
            issue=payload.issue,
            number="TK-" + now().strftime("%Y%m%d") + "-" + uid()[:6].upper(),
            owner_id=actor.user_id,
            due_at=now() + timedelta(hours=actor.tenant.config.get("sla_hours", 24)),
        )
        .on_conflict_do_nothing(index_elements=["tenant_id", "source_ref"])
        .returning(Ticket.id)
    )
    ticket = db.scalar(
        select(Ticket).where(
            Ticket.tenant_id == actor.tenant_id,
            Ticket.source_ref == payload.source_ref,
        )
    )
    require(
        ticket.order_id == order.id
        and ticket.title == payload.title
        and ticket.request == payload.request
        and ticket.issue == payload.issue,
        "source_record_changed",
        "该表格记录已建单，内容发生变化，请在原工单中补充材料；新的事件请新增一行",
        409,
    )
    if created_id:
        d.audit(
            db,
            actor,
            "ticket.feishu_created",
            ticket.id,
            {"source_ref": payload.source_ref, "order_id": order.id},
        )
    investigation = d.auto_investigate(db, actor, ticket) if created_id and payload.auto_investigate else None
    return result(ticket, bool(created_id)) | {"investigation": investigation}


@router.post("/records")
def receive_record(
    payload: FeishuRecord,
    actor: Actor = Depends(integration_actor),
    db: Session = Depends(get_db),
):
    cfg = settings()
    require(
        payload.base_id == cfg.feishu_base_id
        and payload.table_id == cfg.feishu_table_id,
        "integration_table_mismatch",
        "该表格未获准接入此空间",
        403,
    )
    return intake(db, actor, payload)


@router.get("/records/{record_id}")
def record_status(
    record_id: str = Path(min_length=1, max_length=100, pattern=IDENTIFIER),
    actor: Actor = Depends(integration_actor),
    db: Session = Depends(get_db),
):
    cfg = settings()
    ticket = db.scalar(
        select(Ticket).where(
            Ticket.tenant_id == actor.tenant_id,
            Ticket.source_ref
            == f"feishu:{cfg.feishu_base_id}:{cfg.feishu_table_id}:{record_id}",
        )
    )
    require(ticket is not None, "not_found", "该表格记录尚未生成工单", 404)
    return result(ticket)


@router.post("/simulate")
def simulate(
    payload: FeishuRecord,
    actor: Actor = Depends(current_actor),
    db: Session = Depends(get_db),
):
    require(
        settings().demo_mode
        and settings().app_env != "production"
        and actor.tenant.is_demo,
        "not_found",
        "模拟入口仅在演示空间可用",
        404,
    )
    require(
        payload.base_id == "demo" and payload.table_id == "demo",
        "simulation_source",
        "模拟记录须使用演示表格",
        422,
    )
    return intake(db, actor, payload)
