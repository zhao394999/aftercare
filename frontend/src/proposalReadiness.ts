import type { Policy, Ticket } from "./types";

export function proposalReadiness(
  ticket: Ticket,
  policies: Policy[],
  now = Date.now(),
) {
  const placed = new Date(ticket.order.placed_at).getTime();
  const applicable = policies.filter(
    (p) =>
      p.state === "published" &&
      p.allow_refund_on_loss &&
      p.max_refund_cents > 0 &&
      ["*", ticket.order.channel].includes(p.channel) &&
      new Date(p.effective_from).getTime() <= placed &&
      (!p.effective_until || new Date(p.effective_until).getTime() > placed),
  );
  const valid = (ticket.evidence || []).filter(
    (e) =>
      e.verified_at &&
      (!e.expires_at || new Date(e.expires_at).getTime() > now),
  );
  const loss = valid.filter((e) => e.kind === "carrier_confirmation");
  const customer = valid.filter((e) => e.kind === "customer_request");
  const missing: {
    key: string;
    title: string;
    description: string;
    action: string;
  }[] = [];
  if (
    !ticket.order.verified_at ||
    new Date(ticket.order.verified_at).getTime() <= now - 24 * 60 * 60 * 1000
  )
    missing.push({
      key: "snapshot",
      title: "订单快照尚未核查或已超过24小时",
      description: "需对照外部订单核实实付金额、当前状态与历史退款。",
      action: "核查订单快照",
    });
  if (!["paid", "shipped"].includes(ticket.order.status))
    missing.push({
      key: "status",
      title: "当前订单状态不支持丢件退款",
      description: "已签收、取消或退款的订单需人工重新核查真实情况。",
      action: "查看并核对订单资料",
    });
  if (
    ticket.order.paid_cents -
      ticket.order.refunded_cents -
      ticket.order.reserved_cents <=
    0
  )
    missing.push({
      key: "balance",
      title: "当前没有剩余可退金额",
      description: "请先核对已有退款或未完成资金动作，不得重复提交退款。",
      action: "",
    });
  if (!applicable.length)
    missing.push({
      key: "policy",
      title: "没有适用的已发布退款政策",
      description: policies.length
        ? `当前订单渠道“${ticket.order.channel}”。政策检查：未发布 ${policies.filter((p) => p.state !== "published").length} 项、渠道不符 ${policies.filter((p) => !["*", ticket.order.channel].includes(p.channel)).length} 项、下单时间不在适用期间 ${policies.filter((p) => new Date(p.effective_from).getTime() > placed || (p.effective_until && new Date(p.effective_until).getTime() <= placed)).length} 项、未授权丢件退款或无退款额度 ${policies.filter((p) => !p.allow_refund_on_loss || p.max_refund_cents <= 0).length} 项。请主管核对或发布适用版本。`
        : "工作空间尚无政策，请主管发布匹配订单渠道、下单时间且授权丢件退款的政策版本。",
      action: "查看知识与政策",
    });
  for (const [kind, title, rows] of [
    ["carrier_confirmation", "承运商丢件证明", loss],
    ["customer_request", "客户退款意愿", customer],
  ] as const) {
    if (rows.length) continue;
    const candidates = (ticket.evidence || []).filter((e) => e.kind === kind);
    const pending = candidates.some(
      (e) =>
        !e.verified_at &&
        (!e.expires_at || new Date(e.expires_at).getTime() > now),
    );
    missing.push({
      key: pending ? "verify-" + kind : kind,
      title: title + (pending ? "待主管核验" : "缺少有效且已核验的材料"),
      description: pending
        ? "材料已保存，核验后才可用于提案。"
        : candidates.length
          ? "原材料已过期或不可用，请补充有效材料并核验。"
          : "请先补充有来源的材料，再由主管核验。",
      action: pending ? "查看待核验材料" : "补充" + title,
    });
  }
  return { policies: applicable, loss, customer, missing };
}
