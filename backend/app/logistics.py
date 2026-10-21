"""Read-only Kuaidi100 queries; never approve refunds or verify evidence."""

import hashlib
import json
import re
import time
from datetime import timedelta

import httpx
from sqlalchemy import select

from . import domain as d
from .config import settings
from .database import tenant_session
from .models import Membership, Order, Tenant, Ticket, User, now
from .security import Actor, require

STATE_LABELS = {
    "0": "在途",
    "1": "揽收",
    "2": "疑难",
    "3": "签收",
    "4": "退签",
    "5": "派件",
    "6": "退回",
    "7": "转投",
    "8": "清关",
    "14": "拒签",
    "201": "超时未签收",
    "202": "超时未更新",
    "203": "拒收",
    "204": "派件异常",
    "205": "超时未取",
    "206": "无法联系",
    "207": "超区",
    "208": "滞留",
    "209": "破损",
    "301": "本人签收",
    "302": "异常后签收",
    "303": "代签",
    "304": "柜或驿站签收",
}


def failure(status, message):
    return {"source": "快递100", "status": status, "message": message}


def clean_text(value, phone=""):
    value = str(value)[:1000]
    if phone:
        value = value.replace(phone, "[校验号码已脱敏]")
    return re.sub(r"(?<!\d)1[3-9]\d{9}(?!\d)", "[手机号已脱敏]", value)


def request_tracking(carrier, number, phone):
    cfg = settings()
    param = json.dumps(
        {
            "com": carrier,
            "num": number,
            "phone": phone,
            "resultv2": "4",
            "show": "0",
            "order": "desc",
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    sign = (
        hashlib.md5((param + cfg.kuaidi100_key + cfg.kuaidi100_customer).encode())
        .hexdigest()
        .upper()
    )
    try:
        started = time.monotonic()
        with (
            httpx.Client(timeout=8, trust_env=False, follow_redirects=False) as client,
            client.stream(
                "POST",
                "https://poll.kuaidi100.com/poll/query.do",
                data={"customer": cfg.kuaidi100_customer, "sign": sign, "param": param},
            ) as response,
        ):
            response.raise_for_status()
            content = bytearray()
            for chunk in response.iter_bytes():
                content.extend(chunk)
                if len(content) > 256 * 1024 or time.monotonic() - started > 12:
                    return failure("failed", "物流响应超过时间或大小限制，请人工核查")
            raw = json.loads(content)
        if not isinstance(raw, dict):
            return failure("failed", "物流接口返回格式异常")
        if str(raw.get("status")) != "200":
            code = str(raw.get("returnCode", ""))
            return failure(
                "no_result" if code == "500" else "failed",
                "物流接口暂无查询结果，请核对单号并人工核查"
                if code == "500"
                else "物流接口拒绝查询，请维护人员核查服务授权、额度或参数",
            )
        if raw.get("com") != carrier or raw.get("nu") != number:
            return failure("failed", "物流返回的承运商或单号不匹配，已拒绝使用")
        items = raw.get("data")
        if not isinstance(items, list) or not items or len(items) > 200:
            return failure("failed", "物流轨迹为空或超出解析限制")
        if not all(
            isinstance(x, dict) and isinstance(x.get("context"), str) for x in items
        ):
            return failure("failed", "物流轨迹格式异常")
        events = [
            {
                "time": clean_text(x.get("ftime") or x.get("time", ""), phone),
                "description": clean_text(x["context"], phone),
                "state_code": str(x.get("statusCode", ""))[:20],
            }
            for x in items[:30]
        ]
        state = str(raw.get("state", ""))[:20]
        return {
            "source": "快递100",
            "status": "ok",
            "state": state,
            "delivered": state == "3"
            or events[0]["state_code"] in {"3", "301", "302", "303", "304"},
            "state_label": STATE_LABELS.get(
                events[0]["state_code"], STATE_LABELS.get(state, "状态待核查")
            ),
            "events": events,
            "message": "承运商轨迹记录；未明确说明的具体原因仍需核查",
            "reason_confirmed": False,
            "is_loss_proof": False,
        }
    except (httpx.HTTPError, ValueError, TypeError):
        # Provider errors may contain credentials; never log their text.
        return failure("failed", "物流查询失败或超时，请稍后重试或转人工核查")


def authorized_actor(db, tenant_id, user_id):
    tenant, user = db.get(Tenant, tenant_id), db.get(User, user_id)
    member = db.scalar(
        select(Membership).where(
            Membership.tenant_id == tenant_id, Membership.user_id == user_id
        )
    )
    require(
        tenant
        and tenant.enabled
        and user
        and user.enabled
        and member
        and member.enabled,
        "actor_revoked",
        "访问权限已撤销",
        403,
    )
    actor = Actor(user, tenant, member, None)
    actor.need("runs.start")
    return actor


def query_logistics(tenant_id, ticket_id, user_id):
    with tenant_session(tenant_id) as db:
        actor = authorized_actor(db, tenant_id, user_id)
        # Serialize claims in this workspace; HTTP happens after committing these locks.
        # shortcut: add account-wide claims before sharing credentials across tenants.
        db.scalar(select(Tenant).where(Tenant.id == tenant_id).with_for_update())
        ticket = d.scoped(db, Ticket, ticket_id, tenant_id)
        order = d.scoped(db, Order, ticket.order_id, tenant_id, lock=True)
        if not order.carrier_code or not order.tracking_number:
            return failure("missing_tracking", "请补充快递公司编码和运单号")
        if (
            order.carrier_code in {"shunfeng", "shunfengkuaiyun", "zhongtong"}
            and not order.tracking_phone
        ):
            return failure("phone_required", "该承运商需要查件校验号码，请补充物流资料")
        cfg = settings()
        if not cfg.kuaidi100_customer or not cfg.kuaidi100_key:
            return failure("unconfigured", "快递100服务尚未配置，不能查询真实物流")
        recent = db.scalar(
            select(Order)
            .where(
                Order.tenant_id == tenant_id,
                Order.carrier_code == order.carrier_code,
                Order.tracking_number == order.tracking_number,
                Order.logistics_queried_at > now() - timedelta(minutes=30),
            )
            .order_by(Order.logistics_queried_at.desc())
            .limit(1)
        )
        if recent:
            order.logistics_snapshot = recent.logistics_snapshot
            order.logistics_queried_at = recent.logistics_queried_at
            return {**recent.logistics_snapshot, "cached": True}
        carrier, number, phone = (
            order.carrier_code,
            order.tracking_number,
            order.tracking_phone,
        )
        order_id, version = order.id, order.version
        order.logistics_queried_at = now()
        checked_at = order.logistics_queried_at.isoformat() + "Z"
        order.logistics_snapshot = failure(
            "querying", "物流正在查询；半小时内复用本次查询"
        ) | {"queried_at": checked_at}
    result = request_tracking(carrier, number, phone) | {
        "queried_at": checked_at,
        "cached": False,
    }
    with tenant_session(tenant_id) as db:
        actor = authorized_actor(db, tenant_id, user_id)
        order = d.scoped(db, Order, order_id, tenant_id, lock=True)
        if (order.carrier_code, order.tracking_number, order.tracking_phone) != (
            carrier,
            number,
            phone,
        ):
            return failure("stale", "运单资料已变化，旧查询结果未使用")
        if order.version != version:
            result = failure("stale", "订单已变化，请人工核查物流结果") | {
                "queried_at": checked_at
            }
        order.logistics_snapshot = result
        d.audit(
            db,
            actor,
            "logistics.queried",
            ticket_id,
            {"status": result["status"], "order_id": order.id},
        )
    return result
