import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  ArrowLeft,
  BookOpen,
  Check,
  Clock,
  FileText,
  MessageSquare,
  Plus,
  ShieldCheck,
  Sparkles,
  Wallet,
} from "lucide-react";
import { api, cents, date, localNow, money, post } from "./api";
import {
  Arrow,
  Badge,
  Empty,
  Glyph,
  QueryState,
  useAuth,
  useData,
  useUI,
} from "./ui";
import { useDecision } from "./pages";
import { editOrder } from "./management";
import { proposalReadiness } from "./proposalReadiness";
import type {
  Action,
  LogisticsSnapshot,
  Evidence,
  Member,
  Policy,
  Proposal,
  Ticket,
} from "./types";

const evidenceKinds: Record<string, string> = {
  carrier_confirmation: "承运商丢件证明",
  logistics: "物流记录",
  payment: "付款与历史退款",
  inventory: "库存证明",
  customer_request: "客户诉求",
  execution_receipt: "执行凭证",
  reconciliation: "对账证明",
};
function Summary({
  ticket: t,
  onVerify,
  canVerify,
}: {
  ticket: Ticket;
  onVerify: () => void;
  canVerify: boolean;
}) {
  return (
    <div className="card summary-strip">
      <div className="summary-item">
        <small>关联订单</small>
        <strong>{t.order.external_id}</strong>
        <p>{t.order.channel}</p>
      </div>
      <div className="summary-item">
        <small>商品</small>
        <strong>{t.order.product}</strong>
        <p>数量 {t.order.quantity} 件</p>
      </div>
      <div className="summary-item">
        <small>实付金额</small>
        <strong>{money(t.order.paid_cents)}</strong>
        <p>
          已退 {money(t.order.refunded_cents)} · 占用{" "}
          {money(t.order.reserved_cents)}
        </p>
      </div>
      <div className="summary-item">
        <small>订单事实核查</small>
        <strong>
          {t.order.verified_at ? date(t.order.verified_at) : "尚未人工核查"}
        </strong>
        {canVerify && (
          <button className="inline-button" onClick={onVerify}>
            重新核查快照
            <Arrow />
          </button>
        )}
      </div>
    </div>
  );
}
export default function TicketPage() {
  const { id } = useParams();
  const { profile } = useAuth();
  const ui = useUI();
  const query = useData<Ticket>("/tickets/" + id, 5000);
  const policies = useData<Policy[]>("/policies");
  const members = useData<Member[]>("/members");
  const decide = useDecision();
  const can = (permission: string) => profile.permissions.includes(permission);
  const t = query.data;
  const [checkingLogistics, setCheckingLogistics] = useState(false);
  const [logisticsResult, setLogisticsResult] = useState<{
    orderId: string;
    orderVersion: number;
    data: LogisticsSnapshot;
  } | null>(null);
  const names = Object.fromEntries(
    (members.data || []).map((m) => [m.id, m.name]),
  );
  const verifyOrder = () => {
    if (!t) return;
    ui.open({
      title: "核查当前订单事实",
      description:
        "请核对外部订单的付款、历史退款与当前状态。仅确认已有快照准确，不代表系统已经访问电商平台。",
      fields: [
        {
          name: "confirmed",
          label: "我已核查外部系统，当前快照准确",
          type: "checkbox",
          required: true,
        },
      ],
      onSubmit: async () => {
        await post("/orders/" + t.order.id + "/verify", {
          version: t.order.version,
        });
        ui.toast("订单核查已记录。");
      },
    });
  };
  const addEvidence = (kind = "logistics") =>
    ui.open({
      title: "补充业务证据",
      description:
        "保留材料来源与观察时间。文件或摘要由人员核验后才能支持资金处置。",
      fields: [
        {
          name: "kind",
          label: "证据类型",
          type: "select",
          required: true,
          options: Object.entries(evidenceKinds).map(([value, label]) => ({
            value,
            label,
          })),
        },
        { name: "title", label: "材料标题", required: true },
        { name: "source", label: "来源与核查渠道", required: true },
        {
          name: "observed_at",
          label: "观察 / 发生时间",
          type: "datetime-local",
          required: true,
        },
        { name: "expires_at", label: "有效期截止时间", type: "datetime-local" },
        {
          name: "body",
          label: "证据摘要",
          type: "textarea",
          help: "无附件时至少填写5个字；图片和扫描PDF需要人工摘要。",
        },
        {
          name: "file",
          label: "附件",
          type: "file",
          accept: ".pdf,.png,.jpg,.jpeg,.txt,.md",
          help: "支持PDF、图片和文本，单文件20MB以内。",
        },
      ],
      initial: { kind, observed_at: localNow() },
      onSubmit: async (fd) => {
        fd.set(
          "observed_at",
          new Date(String(fd.get("observed_at"))).toISOString(),
        );
        if (fd.get("expires_at"))
          fd.set(
            "expires_at",
            new Date(String(fd.get("expires_at"))).toISOString(),
          );
        await api("/tickets/" + id + "/evidence", { method: "POST", body: fd });
        ui.toast("证据已保存，等待人工核验。");
      },
    });
  const verifyEvidence = (e: Evidence) =>
    ui.open({
      title: "核验业务证据",
      description: `请对照原始来源核查“${e.title}”。该签认用于业务判断，不能仅因模型抽取成功就确认事实。`,
      fields: [
        {
          name: "confirmed",
          label: "我已对照原始来源核实内容与适用范围",
          type: "checkbox",
          required: true,
        },
      ],
      onSubmit: async () => {
        await post("/evidence/" + e.id + "/verify");
        ui.toast("证据核验已签认。");
      },
    });
  const submitProposal = () => {
    if (!t) return;
    if (policies.loading) {
      ui.toast("适用政策仍在加载，请稍候再提交。");
      return;
    }
    if (policies.error) {
      ui.toast("政策读取失败，请点击页面上的重试后再提交。");
      return;
    }
    const ready = proposalReadiness(t, policies.data || []);
    if (ready.missing.length) {
      ui.open({
        title: "提交提案前还需完成",
        description:
          "补齐下列条件后即可提交；未核验或过期材料不能用于资金处置。",
        submitLabel: "关闭",
        body: (
          <div className="proposal-prerequisites">
            {ready.missing.map((item) => (
              <section key={item.key}>
                <h3>{item.title}</h3>
                <p>{item.description}</p>
                {item.key === "policy" ? (
                  <Link
                    className="button"
                    to="/policies"
                    onClick={() => ui.open(null)}
                  >
                    {item.action}
                  </Link>
                ) : (
                  item.action && (
                    <button
                      type="button"
                      className="button"
                      onClick={() => {
                        if (item.key === "snapshot") verifyOrder();
                        else if (item.key === "status") editOrder(t.order, ui);
                        else if (item.key.startsWith("verify-")) {
                          ui.open({
                            title: "核验业务材料",
                            description: can("evidence.verify")
                              ? "请对照来源核验下列材料。"
                              : "材料已进入主管的待核查证据队列，请主管核验；客服不能代替主管签认。",
                            submitLabel: "关闭",
                            body: (
                              <div>
                                {t.evidence
                                  ?.filter(
                                    (e) =>
                                      e.kind === item.key.slice(7) &&
                                      !e.verified_at &&
                                      (!e.expires_at ||
                                        new Date(e.expires_at).getTime() >
                                          Date.now()),
                                  )
                                  .map((e) => (
                                    <section key={e.id}>
                                      <h3>{e.title}</h3>
                                      <p>{e.body}</p>
                                      <p>
                                        来源：{e.source} · {date(e.observed_at)}
                                      </p>
                                      {e.file_key && (
                                        <a
                                          className="button"
                                          href={
                                            "/api/v1/evidence/" + e.id + "/file"
                                          }
                                          target="_blank"
                                          rel="noreferrer"
                                        >
                                          查看原始附件
                                        </a>
                                      )}
                                      {can("evidence.verify") && (
                                        <button
                                          type="button"
                                          className="button"
                                          onClick={() => verifyEvidence(e)}
                                        >
                                          核验这份材料
                                        </button>
                                      )}
                                    </section>
                                  ))}
                              </div>
                            ),
                          });
                        } else addEvidence(item.key);
                      }}
                    >
                      {item.action}
                    </button>
                  )
                )}
              </section>
            ))}
          </div>
        ),
      });
      return;
    }
    const validPolicies = ready.policies;
    const evidence = ready.loss;
    ui.open({
      title: "提交退款处置提案",
      description:
        "提案由服务端校验订单、资金上限、适用政策与已核验证据，再进入人工审批。",
      fields: [
        {
          name: "policy_id",
          label: "适用政策",
          type: "select",
          required: true,
          options: validPolicies.map((p) => ({
            value: p.id,
            label: p.title + " · V" + p.version,
          })),
        },
        {
          name: "evidence_id",
          label: "已核验的丢件证明",
          type: "select",
          required: true,
          options: evidence.map((e) => ({ value: e.id, label: e.title })),
        },
        {
          name: "customer_evidence_id",
          label: "已核验的客户退款意愿",
          type: "select",
          required: true,
          options: ready.customer.map((e) => ({ value: e.id, label: e.title })),
        },
        { name: "amount", label: "建议退款金额（元）", required: true },
        { name: "reason", label: "处理理由", type: "textarea", required: true },
      ],
      initial: {
        policy_id: validPolicies[0]?.id || "",
        evidence_id: evidence[0]?.id || "",
        customer_evidence_id: ready.customer[0]?.id || "",
        amount: (
          (t.order.paid_cents -
            t.order.refunded_cents -
            t.order.reserved_cents) /
          100
        ).toFixed(2),
      },
      onSubmit: async (fd) => {
        await post("/tickets/" + id + "/proposals", {
          policy_id: fd.get("policy_id"),
          evidence_ids: [fd.get("evidence_id"), fd.get("customer_evidence_id")],
          amount_cents: cents(fd.get("amount")),
          reason: fd.get("reason"),
        });
        ui.toast("处置提案已提交，等待其他授权人员审批。");
      },
    });
  };
  const claim = (a: Action) =>
    ui.open({
      title: "核查并领取执行任务",
      description:
        "领取后代表外部操作可能开始。若结果不明，必须对账，不能直接重新执行。",
      fields: [
        {
          name: "checked_at",
          label: "外部状态核查时间",
          type: "datetime-local",
          required: true,
        },
        {
          name: "confirmed",
          label: "我已核对当前订单、历史退款与剩余可退金额",
          type: "checkbox",
          required: true,
        },
      ],
      initial: { checked_at: localNow() },
      onSubmit: async (fd) => {
        await post("/actions/" + a.id + "/claim", {
          order_version: t!.order.version,
          checked_at: new Date(String(fd.get("checked_at"))).toISOString(),
          current_state_confirmed: fd.get("confirmed") === "true",
        });
        ui.toast("任务已领取，请在外部系统执行后登记结果。");
      },
    });
  const receipt = (a: Action) =>
    ui.open({
      title: "登记外部执行结果",
      description:
        "如实记录外部系统返回的状态和实际金额。金额不符、失败或结果未知将进入对账，不会显示为成功。",
      fields: [
        { name: "external_ref", label: "外部动作 / 流水编号", required: true },
        {
          name: "external_state",
          label: "外部状态",
          type: "select",
          required: true,
          options: [
            { value: "succeeded", label: "外部显示终态成功" },
            { value: "processing", label: "已受理，仍在处理中" },
            { value: "unknown", label: "无法确认是否执行" },
            { value: "failed", label: "外部显示失败，需核查" },
          ],
        },
        { name: "amount", label: "实际执行金额（元）", required: true },
        {
          name: "evidence_id",
          label: "执行凭证",
          type: "select",
          required: true,
          options: (t?.evidence || [])
            .filter((e) => e.kind === "execution_receipt")
            .map((e) => ({ value: e.id, label: e.title })),
        },
        { name: "note", label: "执行说明", type: "textarea" },
      ],
      initial: {
        external_state: "processing",
        amount: (a.amount_cents / 100).toFixed(2),
      },
      onSubmit: async (fd) => {
        await post("/actions/" + a.id + "/receipt", {
          external_ref: fd.get("external_ref"),
          external_state: fd.get("external_state"),
          actual_cents: cents(fd.get("amount"), true),
          evidence_id: fd.get("evidence_id"),
          note: fd.get("note"),
        });
        ui.toast("外部结果已登记，后续状态以核验为准。");
      },
    });
  const verifyAction = (a: Action, confirmed: boolean) =>
    ui.open({
      title: confirmed ? "确认业务结果核验" : "核验不通过，转入对账",
      description: confirmed
        ? "请核对外部订单、流水、金额与终态，且执行凭证已人工核验。核验人必须与执行人不同。"
        : "不会退回再次执行。保留当前资金占用，先查明外部动作是否发生。",
      fields: [
        {
          name: "note",
          label: "核验依据与结论",
          type: "textarea",
          required: true,
        },
        ...(confirmed
          ? [
              {
                name: "confirmed",
                label: "我已对照外部记录核验终态与金额",
                type: "checkbox",
                required: true,
              },
            ]
          : []),
      ],
      onSubmit: async (fd) => {
        await post("/actions/" + a.id + "/verify", {
          confirmed,
          note: fd.get("note"),
        });
        ui.toast(
          confirmed
            ? "核验已确认，工单进入已解决。"
            : "已转入对账，未释放资金占用。",
        );
      },
    });
  const reconcile = (a: Action) =>
    ui.open({
      title: "确认未执行并结束原动作",
      description:
        "仅在已查明外部动作确实未执行时使用。确认后释放占用，原提案不能复用，后续动作需要重新审批。",
      fields: [
        {
          name: "evidence_id",
          label: "已核验的对账证明",
          type: "select",
          required: true,
          options: (t?.evidence || [])
            .filter((e) => e.kind === "reconciliation" && e.verified_at)
            .map((e) => ({ value: e.id, label: e.title })),
        },
        {
          name: "note",
          label: "对账依据（至少10字）",
          type: "textarea",
          required: true,
        },
        {
          name: "confirmed",
          label: "已证实外部动作没有执行，允许结束原动作",
          type: "checkbox",
          required: true,
        },
      ],
      onSubmit: async (fd) => {
        await post("/actions/" + a.id + "/reconcile", {
          confirmed_not_executed: fd.get("confirmed") === "true",
          evidence_id: fd.get("evidence_id"),
          note: fd.get("note"),
        });
        ui.toast("原动作已结束，后续处置需要重新提案审批。");
      },
    });
  const communicate = () =>
    ui.open({
      title: "登记客户沟通结果",
      description:
        "记录你通过既有渠道实际完成的沟通；工作台不会自动发送此消息。",
      fields: [
        { name: "channel", label: "沟通渠道", required: true },
        {
          name: "content",
          label: "沟通内容与结果",
          type: "textarea",
          required: true,
        },
      ],
      onSubmit: async (fd) => {
        await post(
          "/tickets/" + id + "/communications",
          Object.fromEntries(fd),
        );
        ui.toast("沟通结果已登记。");
      },
    });
  return (
    <QueryState query={query}>
      {t &&
        (() => {
          const proposal = t.proposals?.find((p) => p.state === "pending");
          const action =
            t.actions?.find((a) => a.state !== "cancelled") || t.actions?.[0];
          const hasLoss = t.evidence?.some(
            (e) => e.kind === "carrier_confirmation" && e.verified_at,
          );
          const nonfinancial =
            ["resolved", "closed"].includes(t.state) && !action;
          const steps = nonfinancial
            ? [
                {
                  title: "核查订单与业务证据",
                  text: "由授权人员对照原始来源核查",
                  done: true,
                },
                {
                  title: "人工确认无需资金处置",
                  text: "已送达或客户撤回，结论与依据已留存",
                  done: true,
                },
                {
                  title: "完成沟通并关闭",
                  text:
                    t.state === "closed" ? "工单已闭环" : "完成结果沟通后关闭",
                  done: t.state === "closed",
                },
              ]
            : [
                {
                  title: "核查订单事实",
                  text: t.order.verified_at
                    ? "人工核查于 " + date(t.order.verified_at)
                    : "核对付款、当前状态与历史退款",
                  done: !!t.order.verified_at,
                },
                {
                  title: "收集并核验关键证据",
                  text: hasLoss
                    ? "已核验承运商丢件证明"
                    : "物流停更本身不能作为丢件证明",
                  done: !!hasLoss,
                },
                {
                  title: "形成可审阅的处置提案",
                  text:
                    proposal?.reason || "关联适用政策，列明动作、金额与证据",
                  done: !!t.proposals?.length,
                },
                {
                  title: "人工审批",
                  text: action
                    ? "批准后转交经办人执行"
                    : "由提交人之外的授权人员确认",
                  done: !!action && action.state !== "cancelled",
                },
                {
                  title: "登记与核验外部结果",
                  text:
                    action?.state === "succeeded"
                      ? "外部终态与金额已人工核验"
                      : "外部受理中或结果未知时，不能确认业务成功",
                  done: action?.state === "succeeded",
                },
                {
                  title: "完成沟通并关闭",
                  text:
                    t.state === "closed"
                      ? "工单已闭环"
                      : "完成必要沟通后关闭工单",
                  done: t.state === "closed",
                },
              ];
          return (
            <>
              <Link className="back-link" to="/tickets">
                <Glyph icon={ArrowLeft} />
                返回工单列表
              </Link>
              <div className="detail-header">
                <div>
                  <div className="detail-title-line">
                    <h1>{t.title}</h1>
                    <Badge state={t.state} />
                  </div>
                  <div className="subtitle">
                    {t.number} · 创建于 {date(t.created_at)} · 负责人{" "}
                    {names[t.owner_id] || "工作空间成员"}
                  </div>
                </div>
                <div className="header-actions">
                  {can("evidence.write") && (
                    <button className="button" onClick={() => addEvidence()}>
                      <Glyph icon={Plus} />
                      补充证据
                    </button>
                  )}
                  {can("runs.start") && (
                    <button
                      className="button"
                      disabled={
                        !profile.model_enabled ||
                        ![
                          "new",
                          "investigating",
                          "waiting_evidence",
                          "draft",
                        ].includes(t.state)
                      }
                      title={
                        !profile.model_enabled
                          ? "需要由维护人员配置模型服务"
                          : "启动有边界的调查"
                      }
                      onClick={() =>
                        ui.open({
                          title: "开始智能调查",
                          description:
                            "系统将按需要查询订单、证据和适用政策，缺证时暂停，提案仍需人工审批。",
                          onSubmit: async () => {
                            const run = await post<{ id: string }>(
                              "/tickets/" + id + "/runs",
                            );
                            ui.toast(
                              "调查已排队，可在智能调查记录中查看进度。",
                            );
                          },
                        })
                      }
                    >
                      <Glyph icon={Sparkles} />
                      智能调查
                    </button>
                  )}
                </div>
              </div>
              {t.state === "reconciling" && (
                <div className="notice amber">
                  <Glyph icon={Clock} />
                  外部执行结果需要对账。当前金额占用保留，请勿直接重复操作。
                </div>
              )}
              <Summary
                ticket={t}
                onVerify={verifyOrder}
                canVerify={can("orders.write")}
              />
              {can("approvals.decide") &&
                [
                  "new",
                  "investigating",
                  "waiting_evidence",
                  "draft",
                  "waiting_approval",
                ].includes(t.state) && (
                  <div className="nonfinancial-actions">
                    <button
                      className="text-link"
                      onClick={() =>
                        ui.open({
                          title: "确认无需资金处置",
                          description:
                            "适用于已证实送达或客户撤回诉求。仍需登记已核验的证明，结果确认后完成客户沟通并关闭。存在未完成执行或调查时会阻止结案。",
                          fields: [
                            {
                              name: "outcome",
                              label: "结案原因",
                              type: "select",
                              required: true,
                              options: [
                                { value: "delivered", label: "已证实正常送达" },
                                {
                                  value: "customer_withdrawn",
                                  label: "客户已明确撤回诉求",
                                },
                              ],
                            },
                            {
                              name: "evidence_id",
                              label: "已核验的结案证明",
                              type: "select",
                              required: true,
                              options: (t.evidence || [])
                                .filter(
                                  (e) =>
                                    e.verified_at &&
                                    ["logistics", "customer_request"].includes(
                                      e.kind,
                                    ),
                                )
                                .map((e) => ({ value: e.id, label: e.title })),
                            },
                            {
                              name: "note",
                              label: "核查依据与结论",
                              type: "textarea",
                              required: true,
                            },
                          ],
                          onSubmit: async (fd) => {
                            await post(
                              "/tickets/" + id + "/resolve-without-payment",
                              Object.fromEntries(fd),
                            );
                            ui.toast(
                              "无需资金处置的结果已确认，请完成客户沟通。",
                            );
                          },
                        })
                      }
                    >
                      已送达或客户撤回，确认无需资金处置
                      <Arrow />
                    </button>
                  </div>
                )}
              <section
                className="card logistics-card"
                aria-labelledby="logistics-title"
              >
                <div className="logistics-heading">
                  <div>
                    <h2 id="logistics-title">物流查询与异常核查</h2>
                    <p className="subtle">
                      {t.order.carrier_code || "快递公司未填写"} ·{" "}
                      {t.order.tracking_number || "运单号未填写"}
                    </p>
                  </div>
                  <div className="header-actions">
                    {can("orders.write") && (
                      <button
                        className="button"
                        onClick={() => editOrder(t.order, ui)}
                      >
                        修改订单与物流资料
                      </button>
                    )}
                    {can("runs.start") && (
                      <button
                        className="button"
                        disabled={checkingLogistics}
                        onClick={async () => {
                          setCheckingLogistics(true);
                          try {
                            const result = await post<LogisticsSnapshot>(
                              "/tickets/" + id + "/logistics",
                            );
                            if (result.queried_at)
                              setLogisticsResult({
                                orderId: t.order.id,
                                orderVersion: t.order.version,
                                data: result,
                              });
                            ui.toast(result.message || "物流查询已完成。");
                          } catch (error) {
                            ui.toast((error as Error).message);
                          } finally {
                            setCheckingLogistics(false);
                          }
                        }}
                      >
                        {checkingLogistics ? "查询中…" : "查询物流"}
                      </button>
                    )}
                  </div>
                </div>
                {(() => {
                  const snapshot =
                    logisticsResult?.orderId === t.order.id &&
                    logisticsResult?.orderVersion === t.order.version
                      ? logisticsResult.data
                      : t.order.logistics_snapshot;
                  const analysis = t.runs?.[0]?.checkpoint.observations
                    ?.slice()
                    .reverse()
                    .find(
                      (item) =>
                        ["request_evidence", "finish"].includes(item.tool) &&
                        typeof item.result?.reason === "string",
                    )?.result;
                  return (
                    <div aria-live="polite">
                      <p>
                        {snapshot.state_label ||
                          snapshot.message ||
                          "尚未查询。需要快递100企业服务凭据；未配置时不会生成物流结果。"}
                      </p>
                      {snapshot.queried_at && (
                        <p className="subtle">
                          {snapshot.source} · 查询于 {date(snapshot.queried_at)}
                          {snapshot.cached ? " · 使用半小时内的查询记录" : ""}
                          {snapshot.simulated ? " · 合成模拟资料" : ""}
                        </p>
                      )}
                      <p className="subtle">
                        轨迹和异常码仅供核查；接口未说明的具体原因仍属未知，不能替代人工核验的丢件证明。
                      </p>
                      {analysis && typeof analysis.reason === "string" && (
                        <div className="logistics-analysis">
                          <h3>模型分析与下一步建议 · 待客服核实</h3>
                          <p>{analysis.reason}</p>
                          {Array.isArray(analysis.missing_items) && (
                            <ul>
                              {analysis.missing_items
                                .filter(
                                  (item): item is string =>
                                    typeof item === "string",
                                )
                                .map((item, i) => (
                                  <li key={i}>{item}</li>
                                ))}
                            </ul>
                          )}
                        </div>
                      )}
                      {snapshot.events?.length ? (
                        <ol className="logistics-events">
                          {snapshot.events.map((event, index) => (
                            <li key={index}>
                              <time>{event.time}</time>
                              <p>{event.description}</p>
                            </li>
                          ))}
                        </ol>
                      ) : null}
                    </div>
                  );
                })()}
              </section>
              <div className="detail-grid">
                <div className="detail-left">
                  <section className="card">
                    <div className="card-head">
                      <h2>客户诉求</h2>
                      <span className="subtle">
                        {t.source_ref?.startsWith("feishu:")
                          ? "飞书表格填报"
                          : "商家录入"}
                      </span>
                    </div>
                    <div className="request-copy">
                      <blockquote>{t.request}</blockquote>
                      <small>
                        {t.order.customer || "未采集客户标识"} ·{" "}
                        {t.order.channel}
                      </small>
                    </div>
                  </section>
                  <section className="card">
                    <div className="card-head">
                      <div>
                        <h2>处理进度</h2>
                        <p>每个阶段都有明确的业务完成条件</p>
                      </div>
                      <span className="badge green">
                        {steps.filter((s) => s.done).length} / {steps.length}
                      </span>
                    </div>
                    <div className="plan">
                      {steps.map((s, i) => (
                        <div
                          className={
                            "plan-step " +
                            (!s.done && steps.slice(0, i).every((x) => x.done)
                              ? "current"
                              : "")
                          }
                          key={s.title}
                        >
                          <span className="step-marker">
                            <Glyph icon={s.done ? Check : Clock} />
                          </span>
                          <div className="step-copy">
                            <h3>{s.title}</h3>
                            <p>{s.text}</p>
                          </div>
                        </div>
                      ))}
                    </div>
                  </section>
                  <section className="card">
                    <div className="card-head">
                      <h2>证据与材料</h2>
                      <span className="subtle">
                        {t.evidence?.length || 0} 份 · 来源独立核查
                      </span>
                    </div>
                    {t.evidence?.length ? (
                      <div className="evidence-list">
                        {t.evidence.map((e) => (
                          <div className="evidence-row" key={e.id}>
                            <span className="file-icon">
                              <Glyph icon={FileText} />
                            </span>
                            <div>
                              <h3>{e.title}</h3>
                              <p>
                                {evidenceKinds[e.kind]} · {e.source} ·{" "}
                                {date(e.observed_at)}
                              </p>
                            </div>
                            <span
                              className={
                                "badge " + (e.verified_at ? "green" : "amber")
                              }
                            >
                              {e.verified_at ? "已核验" : "待核验"}
                            </span>
                            <button
                              onClick={() =>
                                ui.open({
                                  title: e.title,
                                  body: (
                                    <div className="evidence-body">
                                      <p>
                                        {e.body ||
                                          "图片或扫描文件，请对照原始附件核查。"}
                                      </p>
                                      <small>
                                        来源：{e.source}
                                        <br />
                                        观察于 {date(e.observed_at)}
                                        <br />
                                        核验人：
                                        {e.verified_by
                                          ? names[e.verified_by] || "授权人员"
                                          : "尚未核验"}
                                      </small>
                                      {e.file_key && (
                                        <a
                                          className="button"
                                          href={
                                            "/api/v1/evidence/" + e.id + "/file"
                                          }
                                        >
                                          下载原始附件
                                        </a>
                                      )}
                                    </div>
                                  ),
                                })
                              }
                            >
                              查看
                            </button>
                            {can("evidence.verify") && !e.verified_at && (
                              <button onClick={() => verifyEvidence(e)}>
                                核验
                              </button>
                            )}
                          </div>
                        ))}
                      </div>
                    ) : (
                      <Empty
                        title="关键证据尚未补充"
                        description="上传材料或填写有来源的事实摘要。"
                      />
                    )}
                  </section>
                  {t.runs?.length ? (
                    <section className="card">
                      <div className="card-head">
                        <h2>智能调查</h2>
                      </div>
                      <div className="evidence-list">
                        {t.runs.map((r) => (
                          <div className="run-summary" key={r.id}>
                            <Badge state={r.state} />
                            <span>
                              {r.call_count} 次模型调用 · {date(r.created_at)}
                            </span>
                            <Link className="text-link" to={"/runs/" + r.id}>
                              查看链路
                              <Arrow />
                            </Link>
                          </div>
                        ))}
                      </div>
                    </section>
                  ) : null}
                  <section className="card">
                    <div className="card-head">
                      <h2>客户沟通</h2>
                      {can("tickets.write") && (
                        <button className="text-link" onClick={communicate}>
                          <Glyph icon={Plus} />
                          登记结果
                        </button>
                      )}
                    </div>
                    {t.communications?.length ? (
                      <div className="request-copy">
                        {t.communications.map((c) => (
                          <div className="communication" key={c.id}>
                            <strong>{c.channel}</strong>
                            <p>{c.content}</p>
                            <small>
                              {date(c.created_at)} ·{" "}
                              {names[c.created_by] || "客服"}
                            </small>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div className="request-copy">
                        暂无沟通结果。客户通知通过商家既有渠道完成后，在此登记。
                      </div>
                    )}
                  </section>
                  <section className="card">
                    <div className="card-head">
                      <h2>处理记录</h2>
                      <span className="subtle">按时间追踪</span>
                    </div>
                    <div className="event-list">
                      {t.history?.map((e) => (
                        <div className="event-row" key={e.id}>
                          <time>{date(e.created_at)}</time>
                          <span>
                            <strong>{names[e.actor_id] || "授权人员"}</strong> ·{" "}
                            {{
                              "ticket.feishu_created": "飞书表格自动建单",
                              "logistics.queried": "查询快递100物流",
                              "agent.started": "启动智能调查",
                              "agent.evidence_requested": "调查要求补充材料",
                              "ticket.created": "创建工单",
                            }[e.event] || e.event}
                          </span>
                        </div>
                      ))}
                    </div>
                  </section>
                </div>
                <aside className="detail-right">
                  {proposal ? (
                    <section className="card decision">
                      <div className="decision-top">
                        <Glyph icon={Clock} />
                        等待人工审批
                      </div>
                      <div className="decision-body">
                        <div className="decision-label">建议原路退款</div>
                        <div className="decision-amount">
                          <span>¥</span>
                          {(proposal.amount_cents / 100).toFixed(2)}
                        </div>
                        <div className="decision-method">
                          <Glyph icon={Wallet} />
                          历史退款 {money(t.order.refunded_cents)}
                        </div>
                        <div className="decision-rule">
                          <h3>处置依据</h3>
                          <p className="modal-copy">{proposal.reason}</p>
                        </div>
                        <div className="decision-policy">
                          <Glyph icon={BookOpen} />
                          {policies.data?.find(
                            (p) => p.id === proposal.policy_id,
                          )?.title || "已关联政策版本"}
                        </div>
                        {can("approvals.decide") ? (
                          <div className="decision-actions">
                            <button
                              className="button primary wide"
                              disabled={proposal.created_by === profile.user.id}
                              onClick={() =>
                                decide(
                                  { ...proposal, order: t.order },
                                  "approve",
                                )
                              }
                            >
                              <Glyph icon={Check} />
                              批准并转执行
                            </button>
                            {proposal.created_by === profile.user.id && (
                              <small>
                                当前提案由你提交，需要其他审批人确认。
                              </small>
                            )}
                            <div className="action-pair">
                              <button
                                className="button"
                                onClick={() => decide(proposal, "supplement")}
                              >
                                要求补证
                              </button>
                              <button
                                className="button"
                                onClick={() => decide(proposal, "reject")}
                              >
                                拒绝
                              </button>
                            </div>
                          </div>
                        ) : (
                          <div className="status-message">
                            提案已提交。等待其他授权人员审批。
                          </div>
                        )}
                        <p className="decision-note">
                          批准仅生成执行任务。外部操作完成并核验后，才能确认业务结果。
                        </p>
                      </div>
                    </section>
                  ) : action &&
                    !["cancelled", "partially_succeeded"].includes(
                      action.state,
                    ) ? (
                    <section className="card decision">
                      <div className="decision-top">
                        <Glyph icon={ShieldCheck} />
                        执行与核验
                      </div>
                      <div className="decision-body">
                        <Badge state={action.state} />
                        <div className="decision-amount">
                          <span>¥</span>
                          {(action.amount_cents / 100).toFixed(2)}
                        </div>
                        <div className="decision-rule">
                          <div className="info-row">
                            <span>执行人</span>
                            <span>
                              {action.claimed_by
                                ? names[action.claimed_by] || "授权执行人"
                                : "尚未领取"}
                            </span>
                          </div>
                          <div className="info-row">
                            <span>外部流水</span>
                            <span>{action.external_ref || "尚未登记"}</span>
                          </div>
                          <div className="info-row">
                            <span>外部状态</span>
                            <span>
                              {(
                                {
                                  succeeded: "登记为终态成功",
                                  processing: "仍在处理中",
                                  unknown: "结果未知",
                                  failed: "失败，需对账",
                                } as Record<string, string>
                              )[action.external_state || ""] || "尚未登记"}
                            </span>
                          </div>
                          {action.note && (
                            <p className="decision-note">{action.note}</p>
                          )}
                        </div>
                        <div className="decision-actions">
                          {can("actions.verify") &&
                            action.claimed_by !== profile.user.id &&
                            ["claimed", "processing", "reconciling"].includes(
                              action.state,
                            ) && (
                              <button
                                className="button"
                                onClick={() =>
                                  ui.open({
                                    title: "交接执行任务",
                                    description:
                                      "先联系原经办人核实外部操作进度，并添加、核验对账证明。交接后保留全部金额占用，接收人先对账，禁止直接重复退款。",
                                    fields: [
                                      {
                                        name: "executor_id",
                                        label: "接收执行人",
                                        type: "select",
                                        required: true,
                                        options: (members.data || [])
                                          .filter(
                                            (m) =>
                                              m.enabled &&
                                              [
                                                "staff",
                                                "executor",
                                                "admin",
                                              ].includes(m.role) &&
                                              m.id !== action.claimed_by,
                                          )
                                          .map((m) => ({
                                            value: m.id,
                                            label: m.name,
                                          })),
                                      },
                                      {
                                        name: "evidence_id",
                                        label: "已核验的外部进度对账证明",
                                        type: "select",
                                        required: true,
                                        options: (t.evidence || [])
                                          .filter(
                                            (e) =>
                                              e.kind === "reconciliation" &&
                                              e.verified_at,
                                          )
                                          .map((e) => ({
                                            value: e.id,
                                            label: e.title,
                                          })),
                                      },
                                      {
                                        name: "note",
                                        label: "交接原因及外部操作进度",
                                        type: "textarea",
                                        required: true,
                                      },
                                    ],
                                    onSubmit: async (fd) => {
                                      await post(
                                        "/actions/" + action.id + "/handoff",
                                        Object.fromEntries(fd),
                                      );
                                      ui.toast(
                                        "任务已交接并转入对账，金额占用保持不变。",
                                      );
                                    },
                                  })
                                }
                              >
                                核对进度并交接
                              </button>
                            )}
                          {can("actions.verify") &&
                            action.state === "approved" && (
                              <button
                                className="button"
                                onClick={() =>
                                  ui.open({
                                    title: "结束尚未领取的动作",
                                    description:
                                      "仅适用于无人领取、尚未开始外部执行的任务。结束后需要重新提案审批。",
                                    fields: [
                                      {
                                        name: "note",
                                        label: "结束原因",
                                        type: "textarea",
                                        required: true,
                                      },
                                    ],
                                    onSubmit: async (fd) => {
                                      await post(
                                        "/actions/" + action.id + "/cancel",
                                        { note: fd.get("note") },
                                      );
                                      ui.toast(
                                        "未执行任务已结束，金额占用已释放。",
                                      );
                                    },
                                  })
                                }
                              >
                                结束未领取任务
                              </button>
                            )}
                          {can("actions.execute") &&
                            action.state === "approved" && (
                              <button
                                className="button primary wide"
                                onClick={() => claim(action)}
                              >
                                核查并领取
                              </button>
                            )}
                          {can("actions.execute") &&
                            action.claimed_by === profile.user.id &&
                            ["claimed", "processing", "reconciling"].includes(
                              action.state,
                            ) && (
                              <>
                                <button
                                  className="button"
                                  onClick={() =>
                                    addEvidence("execution_receipt")
                                  }
                                >
                                  添加执行凭证
                                </button>
                                <button
                                  className="button primary"
                                  onClick={() => receipt(action)}
                                >
                                  登记外部结果
                                </button>
                              </>
                            )}
                          {can("actions.verify") &&
                            action.state === "waiting_verification" && (
                              <>
                                <button
                                  className="button primary"
                                  disabled={
                                    action.executed_by === profile.user.id
                                  }
                                  onClick={() => verifyAction(action, true)}
                                >
                                  确认业务核验
                                </button>
                                <button
                                  className="button"
                                  onClick={() => verifyAction(action, false)}
                                >
                                  核验不通过，转对账
                                </button>
                              </>
                            )}
                          {can("actions.verify") &&
                            action.state === "reconciling" && (
                              <>
                                <button
                                  className="button"
                                  onClick={() =>
                                    ui.open({
                                      title: "结清已证实的部分成功",
                                      description:
                                        "仅登记已发生的部分退款，不会执行新退款。需要已核验的执行凭证和对账证明；结清后重新核查订单，再处理剩余诉求。",
                                      fields: [
                                        {
                                          name: "amount",
                                          label: "经对账确认的已退款金额（元）",
                                          required: true,
                                        },
                                        {
                                          name: "evidence_id",
                                          label: "已核验的对账证明",
                                          type: "select",
                                          required: true,
                                          options: (t.evidence || [])
                                            .filter(
                                              (e) =>
                                                e.kind === "reconciliation" &&
                                                e.verified_at,
                                            )
                                            .map((e) => ({
                                              value: e.id,
                                              label: e.title,
                                            })),
                                        },
                                        {
                                          name: "note",
                                          label: "对账依据与剩余诉求处理说明",
                                          type: "textarea",
                                          required: true,
                                        },
                                      ],
                                      initial: {
                                        amount: (
                                          (action.actual_cents || 0) / 100
                                        ).toFixed(2),
                                      },
                                      onSubmit: async (fd) => {
                                        await post(
                                          "/actions/" + action.id + "/settle",
                                          {
                                            actual_cents: cents(
                                              fd.get("amount"),
                                            ),
                                            evidence_id: fd.get("evidence_id"),
                                            note: fd.get("note"),
                                          },
                                        );
                                        ui.toast(
                                          "实际发生金额已结清，剩余诉求需要重新提案。",
                                        );
                                      },
                                    })
                                  }
                                >
                                  结清已证实的部分成功
                                </button>
                                <button
                                  className="button"
                                  onClick={() => addEvidence("reconciliation")}
                                >
                                  添加对账证明
                                </button>
                                <button
                                  className="button"
                                  onClick={() => reconcile(action)}
                                >
                                  确认未执行，结束原动作
                                </button>
                              </>
                            )}
                          {can("tickets.write") && t.state === "resolved" && (
                            <button
                              className="button primary"
                              onClick={() =>
                                ui.open({
                                  title: "关闭售后工单",
                                  description:
                                    "确认业务结果已核验，且必要的客户沟通已经登记。",
                                  onSubmit: async () => {
                                    await post("/tickets/" + id + "/close");
                                    ui.toast(
                                      "工单已关闭，全部处理记录已保留。",
                                    );
                                  },
                                })
                              }
                            >
                              确认关闭工单
                            </button>
                          )}
                        </div>
                        <p className="decision-note">
                          外部受理成功不等于业务完成。结果未知时保留金额占用，等待对账。
                        </p>
                      </div>
                    </section>
                  ) : (
                    <section className="card decision">
                      <div className="decision-top">
                        <Glyph icon={Clock} />
                        下一步处理
                      </div>
                      <div className="decision-body">
                        <h2>
                          {t.state === "closed"
                            ? "工单已关闭"
                            : t.state === "resolved"
                              ? "结果已确认，完成沟通"
                              : "补齐事实，形成方案"}
                        </h2>
                        {t.state === "resolved" && can("tickets.write") && (
                          <button
                            className="button primary"
                            onClick={() =>
                              ui.open({
                                title: "关闭售后工单",
                                description: "请先登记结果确认后的客户沟通。",
                                onSubmit: async () => {
                                  await post("/tickets/" + id + "/close");
                                  ui.toast("工单已关闭。");
                                },
                              })
                            }
                          >
                            确认关闭工单
                          </button>
                        )}
                        <p className="modal-copy next-step-copy">
                          {["resolved", "closed"].includes(t.state)
                            ? "无需资金处置的业务结论与依据已保留。确认结果后完成客户沟通，即可关闭。"
                            : profile.model_enabled
                              ? "可以启动智能调查，或由客服核查材料后提交提案。"
                              : "模型尚未启用。客服可完成订单核查、证据收集和提案，业务流程可继续使用。"}
                        </p>
                        {policies.loading && (
                          <p role="status">正在加载适用政策…</p>
                        )}
                        {policies.error && (
                          <div className="form-error" role="alert">
                            政策读取失败：{policies.error.message}
                            <button
                              className="button"
                              onClick={policies.reload}
                            >
                              重试读取政策
                            </button>
                          </div>
                        )}
                        {can("proposals.write") &&
                          [
                            "new",
                            "investigating",
                            "waiting_evidence",
                            "draft",
                            "waiting_approval",
                          ].includes(t.state) && (
                            <div className="decision-actions">
                              <button
                                className="button primary wide"
                                onClick={submitProposal}
                              >
                                提交处置提案
                              </button>
                            </div>
                          )}
                      </div>
                    </section>
                  )}
                  <section className="card info-card">
                    <h3>工单信息</h3>
                    <div className="info-row">
                      <span>处理时限</span>
                      <span>{date(t.due_at)}</span>
                    </div>
                    <div className="info-row">
                      <span>负责人</span>
                      <span>{names[t.owner_id] || "工作空间成员"}</span>
                    </div>
                    <div className="info-row">
                      <span>订单版本</span>
                      <span>V{t.order.version}</span>
                    </div>
                    <div className="info-row">
                      <span>证据版本</span>
                      <span>V{t.evidence_version}</span>
                    </div>
                    <div className="info-row">
                      <span>外部操作</span>
                      <span>人工执行与登记</span>
                    </div>
                    <div className="info-row">
                      <span>核验方式</span>
                      <span>外部记录 + 人工签认</span>
                    </div>
                  </section>
                </aside>
              </div>
            </>
          );
        })()}
    </QueryState>
  );
}
