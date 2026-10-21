import hashlib
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from .models import (
    Action,
    AgentRun,
    AuditEvent,
    Communication,
    Evidence,
    Job,
    Membership,
    Order,
    Policy,
    Proposal,
    RunEvent,
    Ticket,
    User,
    now,
    uid,
)
from .schemas import ProposalInput, TicketInput
from .security import Actor, ROLE_PERMISSIONS, require


def utc(value: datetime) -> datetime:
    return (
        value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value
    )


def shanghai_today() -> datetime:
    return utc(
        now()
        .replace(tzinfo=timezone.utc)
        .astimezone(ZoneInfo("Asia/Shanghai"))
        .replace(hour=0, minute=0, second=0, microsecond=0)
    )


def data(row):
    result = {}
    for attr in inspect(row).mapper.column_attrs:
        value = getattr(row, attr.key)
        result[attr.key] = (
            value.isoformat() + "Z" if isinstance(value, datetime) else value
        )
    if isinstance(row, Order):
        result["tracking_phone_configured"] = bool(result.pop("tracking_phone", ""))
    return result


def scoped(db: Session, model, resource_id: str, tenant_id: str, lock=False):
    query = select(model).where(model.id == resource_id, model.tenant_id == tenant_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    row = db.scalar(query)
    require(row is not None, "not_found", "记录不存在或无权访问", 404)
    return row


def audit(db, actor: Actor, event: str, resource_id: str, details=None, request_id=""):
    db.add(
        AuditEvent(
            tenant_id=actor.tenant_id,
            actor_id=actor.user_id,
            event=event,
            resource_id=resource_id,
            details=details or {},
            request_id=request_id or db.info.get("request_id", ""),
        )
    )


def event(db, run: AgentRun, kind: str, payload: dict):
    db.add(RunEvent(tenant_id=run.tenant_id, run_id=run.id, kind=kind, data=payload))


def locked_ticket(db, ticket_id: str, tenant_id: str):
    ticket = scoped(db, Ticket, ticket_id, tenant_id)
    order = scoped(db, Order, ticket.order_id, tenant_id, lock=True)
    ticket = scoped(db, Ticket, ticket_id, tenant_id, lock=True)
    return ticket, order


def create_ticket(db, actor: Actor, payload: TicketInput, source_ref=None):
    actor.need("tickets.write")
    scoped(db, Order, payload.order_id, actor.tenant_id)
    ticket = Ticket(
        tenant_id=actor.tenant_id,
        **payload.model_dump(exclude={"auto_investigate"}),
        source_ref=source_ref,
        number="TK-" + now().strftime("%Y%m%d") + "-" + uid()[:6].upper(),
        owner_id=actor.user_id,
        due_at=now() + timedelta(hours=actor.tenant.config.get("sla_hours", 24)),
    )
    db.add(ticket)
    db.flush()
    audit(db, actor, "ticket.created", ticket.id)
    investigation = (
        auto_investigate(db, actor, ticket) if payload.auto_investigate else None
    )
    return data(ticket) | {"investigation": investigation}


def policy_valid(policy: Policy, order: Order):
    require(policy.state == "published", "policy_unavailable", "政策未发布或已停用")
    require(
        policy.effective_from <= order.placed_at
        and (
            policy.effective_until is None or order.placed_at < policy.effective_until
        ),
        "policy_time_mismatch",
        "政策不适用于订单下单时间",
    )
    require(
        policy.channel in {"*", order.channel},
        "policy_channel_mismatch",
        "政策不适用于该订单来源",
    )
    require(policy.allow_refund_on_loss, "policy_blocks_refund", "该政策未授权丢件退款")


def current_loss_evidence(db, ticket: Ticket, ids: list[str]):
    records = [scoped(db, Evidence, item, ticket.tenant_id) for item in set(ids)]
    require(
        all(e.ticket_id == ticket.id for e in records),
        "evidence_scope",
        "证据不属于当前工单",
    )
    valid = [
        e
        for e in records
        if e.verified_at and (not e.expires_at or e.expires_at > now())
    ]
    require(
        any(e.kind == "carrier_confirmation" for e in valid),
        "missing_loss_evidence",
        "需要已人工核验且有效的承运商丢件证明",
    )
    require(
        any(e.kind == "customer_request" for e in valid),
        "missing_customer_choice",
        "需要已核验的客户选择退款记录",
    )
    require(
        len(valid) == len(records),
        "unverified_evidence",
        "提案引用的证据尚未核验或已过期",
    )
    return records


def proposal_valid(db, proposal: Proposal, ticket: Ticket, order: Order):
    require(
        proposal.expires_at > now(), "approval_expired", "提案或审批已过期，请重新核查"
    )
    require(
        proposal.order_version == order.version,
        "order_changed",
        "订单信息已变化，需要重新提案",
    )
    require(
        proposal.evidence_version == ticket.evidence_version,
        "evidence_changed",
        "证据已变化，需要重新提案",
    )
    policy = scoped(db, Policy, proposal.policy_id, ticket.tenant_id, lock=True)
    require(
        policy.content_hash == proposal.policy_hash,
        "policy_changed",
        "政策已变化，需要重新提案",
    )
    policy_valid(policy, order)
    current_loss_evidence(db, ticket, proposal.evidence_ids)
    require(
        order.status in {"paid", "shipped"},
        "order_not_refundable",
        "当前订单状态需要人工重新核查",
    )
    require(
        order.verified_at is not None
        and order.verified_at > now() - timedelta(hours=24),
        "order_not_verified",
        "请人工核查当前外部订单与历史退款，并确认订单快照",
    )
    return policy


def create_proposal(db, actor: Actor, ticket_id: str, payload: ProposalInput):
    actor.need("proposals.write")
    ticket, order = locked_ticket(db, ticket_id, actor.tenant_id)
    require(
        ticket.state
        in {"new", "investigating", "waiting_evidence", "draft", "waiting_approval"},
        "state_conflict",
        "当前阶段不能修改处置提案",
    )
    active = db.scalar(
        select(Action).where(
            Action.tenant_id == actor.tenant_id,
            Action.order_id == order.id,
            Action.state.not_in(["succeeded", "cancelled", "partially_succeeded"]),
        )
    )
    require(
        active is None, "order_action_active", "同一订单已有未完成动作，请先核验或对账"
    )
    policy = scoped(db, Policy, payload.policy_id, actor.tenant_id, lock=True)
    policy_valid(policy, order)
    require(
        payload.amount_cents
        <= min(
            order.paid_cents - order.refunded_cents - order.reserved_cents,
            policy.max_refund_cents,
        ),
        "refund_limit",
        "金额超过剩余可退金额或政策上限",
    )
    current_loss_evidence(db, ticket, payload.evidence_ids)
    old = db.scalars(
        select(Proposal).where(
            Proposal.tenant_id == actor.tenant_id,
            Proposal.ticket_id == ticket.id,
            Proposal.state == "pending",
        )
    ).all()
    for item in old:
        item.state = "superseded"
    snapshot = {
        **payload.model_dump(),
        "order_version": order.version,
        "evidence_version": ticket.evidence_version,
        "policy_hash": policy.content_hash,
    }
    proposal = Proposal(
        tenant_id=actor.tenant_id,
        ticket_id=ticket.id,
        created_by=actor.user_id,
        **payload.model_dump(),
        evidence_version=ticket.evidence_version,
        order_version=order.version,
        policy_hash=policy.content_hash,
        payload_hash=hashlib.sha256(
            json.dumps(snapshot, sort_keys=True).encode()
        ).hexdigest(),
        expires_at=now()
        + timedelta(hours=actor.tenant.config.get("approval_hours", 24)),
    )
    db.add(proposal)
    db.flush()
    proposal_valid(db, proposal, ticket, order)
    ticket.state = "waiting_approval"
    ticket.version += 1
    audit(
        db,
        actor,
        "proposal.submitted",
        proposal.id,
        {
            "ticket_id": ticket.id,
            "amount_cents": proposal.amount_cents,
            "order_snapshot": data(order),
            "proposal_snapshot": snapshot,
            "payload_hash": proposal.payload_hash,
        },
    )
    return proposal


def decide(db, actor: Actor, proposal_id: str, payload):
    actor.need("approvals.decide")
    proposal = scoped(db, Proposal, proposal_id, actor.tenant_id)
    ticket, order = locked_ticket(db, proposal.ticket_id, actor.tenant_id)
    proposal = scoped(db, Proposal, proposal_id, actor.tenant_id, lock=True)
    require(
        proposal.payload_hash == payload.payload_hash,
        "proposal_changed",
        "提案版本不匹配，请刷新",
    )
    # Repeated approval of the same frozen proposal returns its existing action.
    if payload.decision == "approve" and proposal.state == "approved":
        return db.scalar(
            select(Action).where(
                Action.proposal_id == proposal.id, Action.tenant_id == actor.tenant_id
            )
        )
    require(
        proposal.state == "pending" and ticket.state == "waiting_approval",
        "already_decided",
        "提案已处理或不在审批阶段",
    )
    require(
        proposal.created_by != actor.user_id,
        "self_approval",
        "资金提案不能由提交人自行审批",
        403,
    )
    if payload.decision != "approve":
        require(
            len(payload.note) >= 5,
            "decision_reason_required",
            "请填写至少5字的处理意见",
            422,
        )
        proposal.state = (
            "rejected" if payload.decision == "reject" else "needs_evidence"
        )
        ticket.state = "draft" if payload.decision == "reject" else "waiting_evidence"
    else:
        proposal_valid(db, proposal, ticket, order)
        active = db.scalar(
            select(Action).where(
                Action.tenant_id == actor.tenant_id,
                Action.order_id == order.id,
                Action.state.not_in(["succeeded", "cancelled", "partially_succeeded"]),
            )
        )
        require(active is None, "order_action_active", "订单已有执行中或结果未知的动作")
        require(
            proposal.amount_cents
            <= order.paid_cents - order.refunded_cents - order.reserved_cents,
            "refund_limit",
            "剩余金额已被其他动作占用",
        )
        order.reserved_cents += proposal.amount_cents
        proposal.state = "approved"
        ticket.state = "waiting_execution"
        action = Action(
            tenant_id=actor.tenant_id,
            proposal_id=proposal.id,
            ticket_id=ticket.id,
            order_id=order.id,
            amount_cents=proposal.amount_cents,
        )
        db.add(action)
    proposal.decided_by = actor.user_id
    proposal.decided_at = now()
    proposal.decision_note = payload.note
    ticket.version += 1
    audit(
        db, actor, "proposal." + payload.decision, proposal.id, {"ticket_id": ticket.id}
    )
    db.flush()
    return action if payload.decision == "approve" else None


def claim_action(db, actor: Actor, action_id: str, payload):
    actor.need("actions.execute")
    action = scoped(db, Action, action_id, actor.tenant_id)
    ticket, order = locked_ticket(db, action.ticket_id, actor.tenant_id)
    action = scoped(db, Action, action_id, actor.tenant_id, lock=True)
    if action.state == "claimed" and action.claimed_by == actor.user_id:
        return action
    require(
        action.state == "approved",
        "action_not_claimable",
        "执行任务已领取或已进入后续阶段",
    )
    proposal = scoped(db, Proposal, action.proposal_id, actor.tenant_id)
    require(proposal.state == "approved", "approval_invalid", "没有有效批准记录")
    proposal_valid(db, proposal, ticket, order)
    require(
        payload.order_version == order.version and payload.current_state_confirmed,
        "fresh_order_check_required",
        "执行前必须核对当前外部订单、历史退款及可退金额",
    )
    checked = utc(payload.checked_at)
    require(
        now() - timedelta(hours=1) <= checked <= now() + timedelta(minutes=1),
        "fresh_order_check_required",
        "外部状态核查时间须在最近一小时内",
    )
    action.state = "claimed"
    action.claimed_by = actor.user_id
    action.claimed_at = now()
    action.checked_at = checked
    audit(db, actor, "action.claimed", action.id)
    return action


def record_receipt(db, actor: Actor, action_id: str, payload):
    actor.need("actions.execute")
    action = scoped(db, Action, action_id, actor.tenant_id)
    ticket, order = locked_ticket(db, action.ticket_id, actor.tenant_id)
    action = scoped(db, Action, action_id, actor.tenant_id, lock=True)
    before = data(action)
    require(
        action.claimed_by == actor.user_id,
        "executor_mismatch",
        "仅领取任务的经办人可以登记执行结果",
        403,
    )
    require(
        action.state in {"claimed", "processing", "reconciling"},
        "receipt_state_conflict",
        "当前阶段不能重新登记结果",
    )
    receipt = scoped(db, Evidence, payload.evidence_id, actor.tenant_id)
    require(
        receipt.ticket_id == ticket.id and receipt.kind == "execution_receipt",
        "receipt_required",
        "请选择当前工单的执行凭证",
    )
    require(
        action.external_ref is None or action.external_ref == payload.external_ref,
        "external_ref_changed",
        "原外部流水不能覆盖，流水争议须保留原记录并对账",
    )
    duplicate = db.scalar(
        select(Action).where(
            Action.tenant_id == actor.tenant_id,
            Action.external_ref == payload.external_ref,
            Action.id != action.id,
        )
    )
    require(duplicate is None, "duplicate_external_ref", "该外部流水已登记在其他动作中")
    require(
        not (action.external_state == "succeeded" or (action.actual_cents or 0) > 0)
        or (
            payload.external_state == "succeeded"
            and payload.actual_cents >= (action.actual_cents or 0)
        ),
        "contradictory_receipt",
        "已登记发生退款，不能改为未执行或减少金额，请保留记录并对账",
    )
    action.external_ref = payload.external_ref
    action.external_state = payload.external_state
    action.actual_cents = payload.actual_cents
    action.receipt_evidence_id = receipt.id
    action.executed_by = actor.user_id
    action.executed_at = now()
    action.note = payload.note
    if payload.actual_cents != action.amount_cents or payload.external_state in {
        "unknown",
        "failed",
    }:
        action.state = "reconciling"
        ticket.state = "reconciling"
    elif payload.external_state == "processing":
        action.state = "processing"
        ticket.state = "waiting_execution"
    else:
        action.state = "waiting_verification"
        ticket.state = "waiting_verification"
    ticket.version += 1
    audit(
        db,
        actor,
        "action.receipt_recorded",
        action.id,
        {
            "external_state": payload.external_state,
            "actual_cents": payload.actual_cents,
            "before": before,
            "after": data(action),
            "evidence_id": receipt.id,
        },
    )
    return action


def verify_action(db, actor: Actor, action_id: str, payload):
    actor.need("actions.verify")
    action = scoped(db, Action, action_id, actor.tenant_id)
    ticket, order = locked_ticket(db, action.ticket_id, actor.tenant_id)
    action = scoped(db, Action, action_id, actor.tenant_id, lock=True)
    require(
        action.executed_by != actor.user_id,
        "self_verification",
        "资金动作的核验人与执行人必须不同",
        403,
    )
    if action.state == "succeeded" and payload.confirmed:
        return action
    require(
        action.state == "waiting_verification",
        "not_ready_for_verification",
        "只有已登记终态的动作可以确认核验",
    )
    if not payload.confirmed:
        action.state = "reconciling"
        ticket.state = "reconciling"
    else:
        receipt = scoped(db, Evidence, action.receipt_evidence_id, actor.tenant_id)
        require(
            receipt.verified_at is not None
            and (not receipt.expires_at or receipt.expires_at > now()),
            "receipt_not_verified",
            "请先核验执行凭证及其有效性",
        )
        require(
            action.external_state == "succeeded"
            and action.actual_cents == action.amount_cents,
            "unconfirmed_result",
            "外部终态或实际金额不符合已批准动作",
        )
        require(
            order.reserved_cents >= action.amount_cents
            and order.refunded_cents + action.amount_cents <= order.paid_cents,
            "ledger_conflict",
            "资金账本冲突，需要人工对账",
        )
        order.reserved_cents -= action.amount_cents
        order.refunded_cents += action.amount_cents
        action.state = "succeeded"
        ticket.state = "resolved"
        ticket.resolved_at = now()
        if order.refunded_cents == order.paid_cents:
            order.status = "refunded"
        order.version += 1
    action.verified_by = actor.user_id
    action.verified_at = now()
    action.note = payload.note
    ticket.version += 1
    audit(
        db,
        actor,
        "action.verified" if payload.confirmed else "action.reconciliation_required",
        action.id,
    )
    return action


def reconcile_action(db, actor: Actor, action_id: str, payload):
    actor.need("actions.verify")
    action = scoped(db, Action, action_id, actor.tenant_id)
    ticket, order = locked_ticket(db, action.ticket_id, actor.tenant_id)
    action = scoped(db, Action, action_id, actor.tenant_id, lock=True)
    require(action.state == "reconciling", "not_reconciling", "当前动作不在对账阶段")
    require(
        action.external_state != "succeeded" and not (action.actual_cents or 0),
        "contradictory_reconciliation",
        "已登记成功或发生退款，不能按未执行释放金额，请结清实际金额或提交争议核查",
    )
    require(
        payload.confirmed_not_executed,
        "unknown_result",
        "外部结果未知时不能释放金额或再次执行",
    )
    evidence = scoped(db, Evidence, payload.evidence_id, actor.tenant_id)
    require(
        evidence.ticket_id == ticket.id
        and evidence.kind == "reconciliation"
        and evidence.verified_at is not None
        and (not evidence.expires_at or evidence.expires_at > now())
        and (not action.claimed_at or evidence.observed_at >= action.claimed_at),
        "reconciliation_evidence",
        "需要已核验的当前工单对账证据",
    )
    require(
        action.executed_by != actor.user_id and action.claimed_by != actor.user_id,
        "self_verification",
        "对账确认人与执行人必须不同",
        403,
    )
    require(
        order.reserved_cents >= action.amount_cents,
        "ledger_conflict",
        "资金占用账本冲突",
    )
    order.reserved_cents -= action.amount_cents
    action.state = "cancelled"
    scoped(db, Proposal, action.proposal_id, actor.tenant_id).state = "cancelled"
    action.note = payload.note
    ticket.state = "draft"
    ticket.version += 1
    audit(
        db,
        actor,
        "action.confirmed_not_executed",
        action.id,
        {"evidence_id": evidence.id},
    )
    return action


def handoff_action(db, actor: Actor, action_id: str, payload):
    actor.need("actions.verify")
    action = scoped(db, Action, action_id, actor.tenant_id)
    ticket, _ = locked_ticket(db, action.ticket_id, actor.tenant_id)
    action = scoped(db, Action, action_id, actor.tenant_id, lock=True)
    require(
        action.state in {"claimed", "processing", "reconciling"},
        "handoff_state",
        "仅已领取且未核验完成的任务可以交接",
    )
    require(
        action.claimed_by != actor.user_id and payload.executor_id != action.claimed_by,
        "handoff_independence",
        "交接须由原经办人之外的主管确认，并指定不同的经办人",
        403,
    )
    member = db.scalar(
        select(Membership).where(
            Membership.tenant_id == actor.tenant_id,
            Membership.user_id == payload.executor_id,
        )
    )
    user = db.get(User, payload.executor_id)
    require(
        member
        and member.enabled
        and user
        and user.enabled
        and "actions.execute" in ROLE_PERMISSIONS.get(member.role, set()),
        "invalid_executor",
        "接收人须为当前商家已启用的执行人",
        422,
    )
    proof = scoped(db, Evidence, payload.evidence_id, actor.tenant_id)
    require(
        proof.ticket_id == ticket.id
        and proof.kind == "reconciliation"
        and proof.verified_at
        and (not proof.expires_at or proof.expires_at > now())
        and proof.observed_at >= action.claimed_at,
        "handoff_evidence",
        "交接需要已核验的当前外部进度对账证明",
    )
    before = data(action)
    previous = action.claimed_by
    action.claimed_by = payload.executor_id
    action.state = "reconciling"
    action.note = payload.note
    ticket.state = "reconciling"
    ticket.version += 1
    audit(
        db,
        actor,
        "action.handed_off",
        action.id,
        {
            "previous_executor_id": previous,
            "executor_id": payload.executor_id,
            "evidence_id": proof.id,
            "note": payload.note,
            "before": before,
            "after": data(action),
        },
    )
    return action


def close_ticket(db, actor, ticket_id):
    actor.need("tickets.write")
    ticket, _ = locked_ticket(db, ticket_id, actor.tenant_id)
    require(ticket.state == "resolved", "not_resolved", "工单须先完成动作核验")
    communication = db.scalar(
        select(Communication).where(
            Communication.tenant_id == actor.tenant_id,
            Communication.ticket_id == ticket.id,
            Communication.created_at >= ticket.resolved_at,
        )
    )
    require(
        communication is not None,
        "communication_required",
        "请在结果核验后登记客户沟通结果",
    )
    ticket.state = "closed"
    ticket.closed_at = now()
    ticket.version += 1
    audit(db, actor, "ticket.closed", ticket.id)
    return ticket


def public_run(run, actor):
    result = data(run)
    if not actor.can("members.manage"):
        for key in ["cost_microusd", "reserved_microusd", "budget_microusd"]:
            result[key] = None
    return result


def ticket_detail(db, actor, ticket_id):
    ticket = scoped(db, Ticket, ticket_id, actor.tenant_id)
    result = data(ticket)
    result["order"] = data(scoped(db, Order, ticket.order_id, actor.tenant_id))
    for key, model in [
        ("evidence", Evidence),
        ("proposals", Proposal),
        ("actions", Action),
        ("communications", Communication),
        ("runs", AgentRun),
    ]:
        rows = db.scalars(
            select(model)
            .where(model.tenant_id == actor.tenant_id, model.ticket_id == ticket.id)
            .order_by(model.created_at.desc())
        ).all()
        result[key] = [
            public_run(row, actor) if model is AgentRun else data(row) for row in rows
        ]
    ids = (
        [ticket.id]
        + [p["id"] for p in result["proposals"]]
        + [a["id"] for a in result["actions"]]
    )
    result["history"] = [
        data(row)
        for row in db.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.tenant_id == actor.tenant_id, AuditEvent.resource_id.in_(ids)
            )
            .order_by(AuditEvent.created_at.desc())
            .limit(50)
        )
    ]
    return result


def settle_actual(db, actor, action_id, payload):
    """Reconcile a proved partial external success; never issues an additional payment."""
    actor.need("actions.verify")
    action = scoped(db, Action, action_id, actor.tenant_id)
    ticket, order = locked_ticket(db, action.ticket_id, actor.tenant_id)
    action = scoped(db, Action, action_id, actor.tenant_id, lock=True)
    require(action.state == "reconciling", "not_reconciling", "当前动作不在对账阶段")
    require(
        action.executed_by != actor.user_id,
        "self_verification",
        "对账确认人与执行人必须不同",
        403,
    )
    require(
        action.external_state == "succeeded"
        and action.actual_cents == payload.actual_cents
        and 0 < payload.actual_cents < action.amount_cents,
        "settlement_mismatch",
        "仅可结清已证实的部分成功金额，须与已登记外部终态一致",
    )
    proof = scoped(db, Evidence, payload.evidence_id, actor.tenant_id)
    receipt = scoped(db, Evidence, action.receipt_evidence_id, actor.tenant_id)
    require(
        proof.ticket_id == ticket.id
        and proof.kind == "reconciliation"
        and proof.observed_at >= action.claimed_at
        and all(
            e.verified_at and (not e.expires_at or e.expires_at > now())
            for e in [proof, receipt]
        ),
        "reconciliation_evidence",
        "需要当前已核验的对账证明和执行凭证",
    )
    require(
        order.reserved_cents >= action.amount_cents
        and order.refunded_cents + payload.actual_cents <= order.paid_cents,
        "ledger_conflict",
        "金额账本冲突，需要人工处理",
    )
    order.reserved_cents -= action.amount_cents
    order.refunded_cents += payload.actual_cents
    order.version += 1
    order.verified_at = None
    order.verified_by = None
    action.state = "partially_succeeded"
    action.verified_at = now()
    action.verified_by = actor.user_id
    action.note = payload.note
    ticket.state = "draft"
    ticket.version += 1
    scoped(db, Proposal, action.proposal_id, actor.tenant_id).state = "settled"
    audit(
        db,
        actor,
        "action.partial_settled",
        action.id,
        {"actual_cents": payload.actual_cents, "evidence_id": proof.id},
    )
    return action


def resolve_without_payment(db, actor, ticket_id, payload):
    actor.need("approvals.decide")
    ticket, order = locked_ticket(db, ticket_id, actor.tenant_id)
    require(
        ticket.state
        in {"new", "investigating", "waiting_evidence", "draft", "waiting_approval"},
        "state_conflict",
        "当前阶段不能无资金结案",
    )
    active = db.scalar(
        select(Action.id).where(
            Action.tenant_id == actor.tenant_id,
            Action.order_id == order.id,
            Action.state.not_in(["succeeded", "cancelled", "partially_succeeded"]),
        )
    )
    require(active is None, "order_action_active", "先完成已批准动作的核验或对账")
    run = db.scalar(
        select(AgentRun.id).where(
            AgentRun.tenant_id == actor.tenant_id, AgentRun.active_key == ticket.id
        )
    )
    require(run is None, "agent_active", "请先取消仍在调查或等待补证的运行")
    proof = scoped(db, Evidence, payload.evidence_id, actor.tenant_id)
    require(
        proof.ticket_id == ticket.id
        and proof.verified_at
        and (not proof.expires_at or proof.expires_at > now()),
        "resolution_evidence",
        "需要当前工单已核验且有效的结案证据",
    )
    if payload.outcome == "delivered":
        require(
            proof.kind == "logistics"
            and order.status == "delivered"
            and order.verified_at
            and order.verified_at > now() - timedelta(hours=24),
            "delivery_not_verified",
            "请核验送达证据并更新、核查订单签收状态",
        )
    else:
        require(
            proof.kind == "customer_request",
            "customer_choice_required",
            "需要已核验的客户撤回诉求记录",
        )
    for proposal in db.scalars(
        select(Proposal).where(
            Proposal.tenant_id == actor.tenant_id,
            Proposal.ticket_id == ticket.id,
            Proposal.state == "pending",
        )
    ):
        proposal.state = "withdrawn"
    ticket.state = "resolved"
    ticket.resolved_at = now()
    ticket.version += 1
    audit(db, actor, "ticket.resolved_without_payment", ticket.id, payload.model_dump())
    return ticket


def queue_investigation(db, actor, ticket_id):
    from sqlalchemy import func
    from .config import settings
    from .agent import runtime_signature
    from .models import Tenant
    from .telemetry import trace_context

    actor.need("runs.start")
    require(
        settings().model_enabled,
        "model_unconfigured",
        "模型接口、凭据和计费配置尚未完成；可继续人工调查",
        503,
    )
    require(
        actor.tenant.config.get("agent_enabled", True),
        "agent_paused",
        "商家已暂停智能调查",
    )
    db.scalar(select(Tenant).where(Tenant.id == actor.tenant_id).with_for_update())
    ticket = scoped(db, Ticket, ticket_id, actor.tenant_id, lock=True)
    existing = db.scalar(
        select(AgentRun).where(
            AgentRun.tenant_id == actor.tenant_id, AgentRun.active_key == ticket.id
        )
    )
    if existing:
        return existing
    require(
        ticket.state in {"new", "investigating", "waiting_evidence", "draft"},
        "run_stage",
        "当前工单阶段不适合启动调查",
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
    run = AgentRun(
        tenant_id=actor.tenant_id,
        ticket_id=ticket.id,
        requester_id=actor.user_id,
        active_key=ticket.id,
        budget_microusd=settings().llm_budget_microusd,
        model=settings().llm_model,
        checkpoint={
            "phase": "plan",
            "observations": [],
            "done": False,
            "trace_context": trace_context(),
            "runtime": runtime_signature(),
        },
    )
    db.add(run)
    db.flush()
    db.add(Job(tenant_id=actor.tenant_id, kind="agent", resource_id=run.id))
    ticket.state = "investigating"
    ticket.version += 1
    event(db, run, "run.queued", {"ticket_id": ticket.id})
    audit(db, actor, "agent.started", ticket.id, {"run_id": run.id})
    return run


def auto_investigate(db, actor, ticket):
    from .config import settings
    from .security import DomainError

    if not settings().model_enabled:
        return {"state": "not_started", "message": "模型未配置，可手动调查"}
    try:
        run = queue_investigation(db, actor, ticket.id)
        return {"state": run.state, "run_id": run.id, "message": "智能调查已排队"}
    except DomainError as exc:
        audit(db, actor, "agent.auto_not_started", ticket.id, {"code": exc.code})
        return {"state": "not_started", "message": exc.message}
