from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class LoginInput(Input):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)
    tenant_id: str | None = None


class RegisterInput(Input):
    email: str = Field(max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    password: str = Field(min_length=12, max_length=128)
    name: str = Field(min_length=1, max_length=60)
    workspace: str = Field(min_length=2, max_length=100)


class KnowledgeInput(Input):
    question: str = Field(min_length=2, max_length=500)
    channel: str = Field(default="*", max_length=50)
    order_date: datetime | None = None


class DemoInput(Input):
    role: Literal["staff", "supervisor", "executor", "auditor", "admin"] = "supervisor"


class InvitationInput(Input):
    email: str = Field(min_length=3, max_length=254)
    role: Literal["staff", "supervisor", "executor", "auditor", "admin"]


class AcceptInvitation(Input):
    token: str = Field(min_length=20, max_length=100)
    name: str = Field(min_length=1, max_length=60)
    password: str = Field(min_length=12, max_length=128)


class OrderInput(Input):
    external_id: str = Field(min_length=1, max_length=120)
    channel: str = Field(default="商家导入", max_length=50)
    product: str = Field(min_length=1, max_length=200)
    quantity: int = Field(default=1, ge=1, le=10000)
    paid_cents: int = Field(ge=1, le=100_000_000)
    refunded_cents: int = Field(default=0, ge=0, le=100_000_000)
    customer: str = Field(default="", max_length=80)
    carrier_code: str = Field(default="", max_length=50, pattern=r"^[a-z0-9_]*$")
    tracking_number: str = Field(default="", max_length=32, pattern=r"^(?:[A-Za-z0-9-]{6,32})?$")
    tracking_phone: str = Field(default="", max_length=32, pattern=r"^[0-9+ -]*$")
    status: Literal["paid", "shipped", "delivered", "cancelled", "refunded"] = "shipped"
    placed_at: datetime
    shipped_at: datetime | None = None
    promised_at: datetime | None = None

    @model_validator(mode="after")
    def check_money(self):
        if self.refunded_cents > self.paid_cents:
            raise ValueError("累计已退金额不能超过实付")
        if self.status == "refunded" and self.refunded_cents != self.paid_cents:
            raise ValueError("已退款订单须登记完整累计已退金额")
        return self


class VersionInput(Input):
    version: int = Field(ge=1)


class OrderUpdate(OrderInput):
    version: int = Field(ge=1)


class ReasonInput(Input):
    note: str = Field(min_length=5, max_length=2000)


class MemberUpdate(Input):
    role: Literal["staff", "supervisor", "executor", "auditor", "admin"]
    enabled: bool


class TicketDetails(Input):
    auto_investigate: bool = True
    title: str = Field(min_length=2, max_length=200)
    request: str = Field(min_length=2, max_length=4000)
    issue: Literal["logistics_delay", "delivery_overdue", "confirmed_loss"] = (
        "logistics_delay"
    )


class TicketInput(TicketDetails):
    order_id: str


class OrderIntakeInput(Input):
    request_id: UUID
    order: OrderInput
    existing_order_id: str | None = None
    order_version: int | None = Field(default=None, ge=1)
    ticket: TicketDetails | None = None

    @model_validator(mode="after")
    def check_version(self):
        if bool(self.existing_order_id) != (self.order_version is not None):
            raise ValueError("使用已有订单时必须提供当前订单版本")
        return self


class BatchTicketRow(TicketInput):
    order_version: int = Field(ge=1)
    order: OrderInput | None = None


class BatchTicketInput(Input):
    request_id: UUID
    items: list[BatchTicketRow] = Field(min_length=1, max_length=50)


class ProposalInput(Input):
    amount_cents: int = Field(ge=1)
    reason: str = Field(min_length=5, max_length=3000)
    policy_id: str
    evidence_ids: list[str] = Field(min_length=1, max_length=20)


class DecisionInput(Input):
    decision: Literal["approve", "reject", "supplement"]
    payload_hash: str = Field(min_length=64, max_length=64)
    note: str = Field(default="", max_length=2000)


class ClaimInput(Input):
    order_version: int = Field(ge=1)
    checked_at: datetime
    current_state_confirmed: bool


class ReceiptInput(Input):
    external_ref: str = Field(min_length=3, max_length=160)
    external_state: Literal["succeeded", "processing", "unknown", "failed"]
    actual_cents: int = Field(ge=0)
    evidence_id: str
    note: str = Field(default="", max_length=2000)


class VerificationInput(Input):
    confirmed: bool
    note: str = Field(min_length=5, max_length=2000)


class ReconcileInput(Input):
    confirmed_not_executed: bool
    evidence_id: str
    note: str = Field(min_length=10, max_length=2000)


class HandoffInput(Input):
    executor_id: str
    evidence_id: str
    note: str = Field(min_length=10, max_length=2000)


class CommunicationInput(Input):
    channel: str = Field(min_length=1, max_length=60)
    content: str = Field(min_length=5, max_length=4000)


class PolicyInput(Input):
    title: str = Field(min_length=2, max_length=160)
    body: str = Field(min_length=10, max_length=30000)
    channel: str = Field(default="*", max_length=50)
    effective_from: datetime
    effective_until: datetime | None = None
    allow_refund_on_loss: bool = False
    max_refund_cents: int = Field(default=100_000, ge=0, le=100_000_000)


class TenantConfigInput(Input):
    name: str = Field(min_length=2, max_length=100)
    sla_hours: int = Field(default=24, ge=1, le=720)
    approval_hours: int = Field(default=24, ge=1, le=72)
    agent_enabled: bool = True


class RunReviewInput(Input):
    business_correct: bool
    evidence_supported: bool
    handoff_appropriate: bool
    note: str = Field(min_length=10, max_length=2000)


class ActualSettlementInput(Input):
    actual_cents: int = Field(ge=1)
    evidence_id: str
    note: str = Field(min_length=10, max_length=2000)


class NonfinancialResolutionInput(Input):
    outcome: Literal["delivered", "customer_withdrawn"]
    evidence_id: str
    note: str = Field(min_length=10, max_length=2000)
