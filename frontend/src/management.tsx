import { useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { api, cents, date, post, setCsrf } from "./api";
import type { Order, Profile, Run } from "./types";
import {
  Empty,
  QueryState,
  type DialogSpec,
  useAuth,
  useData,
  useUI,
} from "./ui";

export const trackingFields = [
  {
    name: "carrier_code",
    label: "快递公司编码",
    help: "快递100编码，例如 yuantong、shunfeng、zhongtong；全部小写。",
  },
  {
    name: "tracking_number",
    label: "运单号",
    help: "6至32位字母、数字或连字符。",
  },
  {
    name: "tracking_phone",
    label: "查件校验号码",
    type: "password",
    help: "仅用于物流查询，不提供给模型；更新时留空保留已有号码。",
  },
];

export function editOrder(
  o: Order,
  ui: { open: (spec: DialogSpec) => void; toast: (text: string) => void },
) {
  const local = (s: string | null) =>
    s
      ? new Date(
          new Date(s).getTime() - new Date(s).getTimezoneOffset() * 60000,
        )
          .toISOString()
          .slice(0, 16)
      : "";
  ui.open({
    title: "更新订单事实",
    description:
      "保存后当前快照须重新核查，已有提案也须重新审核。已退款和占用金额由资金流程维护。",
    fields: [
      { name: "external_id", label: "外部订单号", required: true },
      { name: "product", label: "商品名称", required: true },
      { name: "quantity", label: "数量", type: "number", required: true },
      { name: "paid_amount", label: "实付金额（元）", required: true },
      {
        name: "refunded_amount",
        label: "累计已退款金额（元）",
        required: true,
        help: "只能增加；已有执行任务须先核验或对账，更新历史退款需要主管权限。",
      },
      { name: "customer", label: "脱敏客户标识" },
      { name: "channel", label: "来源渠道", required: true },
      ...trackingFields,
      {
        name: "status",
        label: "当前状态",
        type: "select",
        required: true,
        options: [
          { value: "paid", label: "已付款" },
          { value: "shipped", label: "已发货" },
          { value: "delivered", label: "已签收" },
          { value: "cancelled", label: "已取消" },
          { value: "refunded", label: "已退款" },
        ],
      },
      {
        name: "placed_at",
        label: "下单时间",
        type: "datetime-local",
        required: true,
      },
      { name: "shipped_at", label: "发货时间", type: "datetime-local" },
      { name: "promised_at", label: "承诺送达时间", type: "datetime-local" },
    ],
    initial: {
      external_id: o.external_id,
      product: o.product,
      quantity: String(o.quantity),
      paid_amount: (o.paid_cents / 100).toFixed(2),
      refunded_amount: (o.refunded_cents / 100).toFixed(2),
      customer: o.customer,
      channel: o.channel,
      carrier_code: o.carrier_code,
      tracking_number: o.tracking_number,
      status: o.status,
      placed_at: local(o.placed_at),
      shipped_at: local(o.shipped_at),
      promised_at: local(o.promised_at),
    },
    onSubmit: async (fd) => {
      const at = (key: string) =>
        fd.get(key) ? new Date(String(fd.get(key))).toISOString() : null;
      await api("/orders/" + o.id, {
        method: "PUT",
        body: JSON.stringify({
          version: o.version,
          external_id: fd.get("external_id"),
          product: fd.get("product"),
          quantity: Number(fd.get("quantity")),
          paid_cents: cents(fd.get("paid_amount")),
          refunded_cents: cents(fd.get("refunded_amount"), true),
          customer: fd.get("customer"),
          channel: fd.get("channel"),
          carrier_code: fd.get("carrier_code"),
          tracking_number: fd.get("tracking_number"),
          ...(fd.get("tracking_phone")
            ? { tracking_phone: fd.get("tracking_phone") }
            : {}),
          status: fd.get("status"),
          placed_at: at("placed_at"),
          shipped_at: at("shipped_at"),
          promised_at: at("promised_at"),
        }),
      });
      ui.toast("订单事实已更新，请重新核查快照。");
    },
  });
}
export function InvitationPage({
  onLogin,
}: {
  onLogin: (profile: Profile) => void;
}) {
  const token = useLocation().pathname.split("/").pop() || "";
  const navigate = useNavigate();
  const info = useData<{
    tenant: string;
    email: string;
    role: string;
    expires_at: string;
  }>("/invitations/" + encodeURIComponent(token));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  return (
    <main className="invitation-page">
      <section className="card invitation-box">
        <span className="eyebrow">WELCOME TO AFTERCARE</span>
        <h1>加入商家空间</h1>
        <QueryState query={info}>
          {info.data && (
            <>
              <p>
                你受邀加入 <strong>{info.data.tenant}</strong>
              </p>
              <div className="notice">
                账户邮箱：{info.data.email}
                <br />
                有效期至 {date(info.data.expires_at)}
              </div>
              <p className="modal-copy">
                已有账户请输入原账户密码；首次加入请设置至少12位密码。邀请只能使用一次。
              </p>
              <form
                onSubmit={async (e) => {
                  e.preventDefault();
                  setBusy(true);
                  setError("");
                  try {
                    const fd = new FormData(e.currentTarget);
                    const profile = await post<Profile>("/invitations/accept", {
                      token,
                      name: fd.get("name"),
                      password: fd.get("password"),
                    });
                    setCsrf(profile.csrf);
                    onLogin(profile);
                    navigate("/overview", { replace: true });
                  } catch (err) {
                    setError((err as Error).message);
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                <div className="field">
                  <label htmlFor="invite-name">你的姓名</label>
                  <input
                    id="invite-name"
                    name="name"
                    maxLength={60}
                    required
                    autoComplete="name"
                  />
                </div>
                <div className="field">
                  <label htmlFor="invite-password">账户密码</label>
                  <input
                    id="invite-password"
                    name="password"
                    type="password"
                    minLength={12}
                    maxLength={128}
                    autoComplete="current-password"
                    required
                  />
                </div>
                {error && (
                  <p role="alert" className="form-error">
                    {error}
                  </p>
                )}
                <button className="button primary wide" disabled={busy}>
                  {busy ? "正在加入…" : "确认加入工作空间"}
                </button>
              </form>
            </>
          )}
        </QueryState>
        <Link className="back-link" to="/login">
          返回工作台登录
        </Link>
      </section>
    </main>
  );
}
interface Evaluation {
  period_days: number;
  runs: number;
  reviewed: number;
  correct: number | null;
  evidence_supported: number | null;
  handoff_appropriate: number | null;
  review_coverage: number | null;
  runtime_error_rate: number | null;
  terminal_runs: number;
  avg_duration_ms: number | null;
}
export function EvaluationSummary() {
  const query = useData<Evaluation>("/evaluations");
  const pct = (v: number | null) =>
    v === null ? "暂无样本" : (v * 100).toFixed(1) + "%";
  return (
    <section className="card section-gap">
      <div className="card-head">
        <div>
          <h2>调查质量评估</h2>
          <p>近30日 · 独立人员复核实际运行结果</p>
        </div>
      </div>
      <QueryState query={query}>
        {query.data && (
          <>
            <div className="evaluation-grid">
              {[
                ["已复核运行", query.data.reviewed + " / " + query.data.runs],
                ["业务结论正确率", pct(query.data.correct)],
                ["证据支持率", pct(query.data.evidence_supported)],
                ["人工接管恰当率", pct(query.data.handoff_appropriate)],
                ["运行异常率", pct(query.data.runtime_error_rate)],
              ].map(([name, value]) => (
                <div key={name}>
                  <span>{name}</span>
                  <strong>{value}</strong>
                </div>
              ))}
            </div>
            <p className="request-copy">
              正确率、证据支持率和接管恰当率以已复核样本为分母；未标注时不生成效果数据。复核覆盖率{" "}
              {pct(query.data.review_coverage)}，终结运行{" "}
              {query.data.terminal_runs} 次。
            </p>
          </>
        )}
      </QueryState>
    </section>
  );
}
export function ReviewRun({ run }: { run: Run }) {
  const { profile } = useAuth();
  const ui = useUI();
  const query = useData<{ review: Record<string, unknown> | null }>(
    "/runs/" + run.id + "/review",
  );
  if (
    !profile.permissions.includes("audit.read") ||
    !["completed", "human_needed", "waiting_input", "cancelled"].includes(
      run.state,
    )
  )
    return null;
  return (
    <div className="run-review">
      <strong>独立质量复核</strong>
      <p>
        {query.data?.review
          ? "此运行已有复核记录，可补充新的复核意见。"
          : "对照业务事实、证据与应有处置进行标注。"}
      </p>
      <button
        className="button"
        onClick={() =>
          ui.open({
            title: "复核调查质量",
            description:
              "由发起人以外的授权人员评估。请先阅读工单与完整调查链路，未勾选表示该项不通过。",
            fields: [
              {
                name: "business_correct",
                label: "业务结论正确",
                type: "checkbox",
              },
              {
                name: "evidence_supported",
                label: "结论有有效证据支持",
                type: "checkbox",
              },
              {
                name: "handoff_appropriate",
                label: "补证、审批与人工接管时机恰当",
                type: "checkbox",
              },
              {
                name: "note",
                label: "复核依据与问题说明",
                type: "textarea",
                required: true,
              },
            ],
            onSubmit: async (fd) => {
              await post("/runs/" + run.id + "/review", {
                business_correct: fd.get("business_correct") === "true",
                evidence_supported: fd.get("evidence_supported") === "true",
                handoff_appropriate: fd.get("handoff_appropriate") === "true",
                note: fd.get("note"),
              });
              ui.toast("质量复核已记录，统计将更新。");
            },
          })
        }
      >
        登记质量复核
      </button>
    </div>
  );
}
