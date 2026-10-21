import { useEffect, useState } from "react";
import {
  Link,
  useLocation,
  useNavigate,
  useSearchParams,
} from "react-router-dom";
import { cents, localNow, money, post } from "./api";
import { trackingFields } from "./management";
import {
  Empty,
  OrderPicker,
  QueryState,
  useAuth,
  useData,
  useUI,
  type FieldSpec,
} from "./ui";
import type { Order, Page, Ticket } from "./types";

type Values = Record<string, string>;
export const issues = [
  ["logistics_delay", "物流长期未更新"],
  ["delivery_overdue", "超过承诺送达时效"],
  ["confirmed_loss", "承运商已确认丢件"],
];
const orderFields: FieldSpec[] = [
  { name: "external_id", label: "外部订单号", required: true },
  { name: "channel", label: "来源渠道", required: true },
  { name: "product", label: "商品名称", required: true },
  { name: "quantity", label: "数量", type: "number", required: true },
  { name: "paid_amount", label: "实付金额（元）", required: true },
  { name: "refunded_amount", label: "累计已退款（元）", required: true },
  { name: "customer", label: "脱敏客户标识" },
  {
    name: "status",
    label: "订单状态",
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
  ...trackingFields,
];
const localDate = (value: string | null) =>
  value
    ? new Date(
        new Date(value).getTime() - new Date(value).getTimezoneOffset() * 60000,
      )
        .toISOString()
        .slice(0, 16)
    : "";
function initialOrder(order?: Order): Values {
  return order
    ? {
        external_id: order.external_id,
        channel: order.channel,
        product: order.product,
        quantity: String(order.quantity),
        paid_amount: (order.paid_cents / 100).toFixed(2),
        refunded_amount: (order.refunded_cents / 100).toFixed(2),
        customer: order.customer,
        status: order.status,
        carrier_code: order.carrier_code,
        tracking_number: order.tracking_number,
        tracking_phone: "",
        placed_at: localDate(order.placed_at),
        shipped_at: localDate(order.shipped_at),
        promised_at: localDate(order.promised_at),
      }
    : {
        channel: "商家导入",
        quantity: "1",
        refunded_amount: "0.00",
        status: "shipped",
        placed_at: localNow(),
      };
}
function orderPayload(values: Values, existing?: Order) {
  const at = (key: "placed_at" | "shipped_at" | "promised_at") =>
    existing && values[key] === localDate(existing[key])
      ? existing[key]
      : values[key]
        ? new Date(values[key]).toISOString()
        : null;
  return {
    external_id: values.external_id,
    channel: values.channel,
    product: values.product,
    quantity: Number(values.quantity),
    paid_cents: cents(values.paid_amount || null),
    refunded_cents: cents(values.refunded_amount || null, true),
    customer: values.customer || "",
    status: values.status,
    carrier_code: values.carrier_code || "",
    tracking_number: values.tracking_number || "",
    ...(!existing || values.tracking_phone
      ? { tracking_phone: values.tracking_phone || "" }
      : {}),
    placed_at: at("placed_at"),
    shipped_at: at("shipped_at"),
    promised_at: at("promised_at"),
  };
}
function OrderFields({
  values,
  onChange,
  prefix,
}: {
  values: Values;
  onChange: (key: string, value: string) => void;
  prefix: string;
}) {
  return (
    <div className="intake-fields">
      {orderFields.map((field) => (
        <div className="field" key={field.name}>
          <label htmlFor={prefix + field.name}>
            {field.label}
            {field.required && <span aria-hidden="true"> *</span>}
          </label>
          {field.type === "select" ? (
            <select
              id={prefix + field.name}
              required={field.required}
              value={values[field.name] || ""}
              onChange={(e) => onChange(field.name, e.target.value)}
            >
              {field.options?.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          ) : (
            <input
              id={prefix + field.name}
              type={field.type || "text"}
              required={field.required}
              value={values[field.name] || ""}
              min={field.type === "number" ? 1 : undefined}
              max={field.type === "number" ? 10000 : undefined}
              maxLength={
                field.name === "tracking_number"
                  ? 32
                  : field.name === "external_id"
                    ? 120
                    : field.name === "product"
                      ? 200
                      : undefined
              }
              onChange={(e) => onChange(field.name, e.target.value)}
              autoComplete={field.type === "password" ? "off" : undefined}
            />
          )}
          {field.help && <small>{field.help}</small>}
        </div>
      ))}
    </div>
  );
}
function TicketFields({
  values,
  onChange,
  prefix,
}: {
  values: Values;
  onChange: (key: string, value: string) => void;
  prefix: string;
}) {
  return (
    <>
      <div className="field">
        <label htmlFor={prefix + "issue"}>异常情况 *</label>
        <select
          id={prefix + "issue"}
          required
          value={values.issue || ""}
          onChange={(e) => onChange("issue", e.target.value)}
        >
          <option value="">请选择真实异常情况</option>
          {issues.map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </div>
      <div className="field">
        <label htmlFor={prefix + "title"}>工单标题 *</label>
        <input
          id={prefix + "title"}
          required
          minLength={2}
          maxLength={200}
          value={values.title || ""}
          onChange={(e) => onChange("title", e.target.value)}
        />
      </div>
      <div className="field">
        <label htmlFor={prefix + "request"}>客户诉求 *</label>
        <textarea
          id={prefix + "request"}
          required
          minLength={2}
          maxLength={4000}
          value={values.request || ""}
          onChange={(e) => onChange("request", e.target.value)}
          placeholder="填写这位客户实际提出的诉求，不同订单分别确认。"
        />
      </div>
    </>
  );
}

export function OrderIntakePage() {
  const { profile } = useAuth();
  const navigate = useNavigate();
  const ui = useUI();
  const location = useLocation();
  const [params] = useSearchParams();
  const aftercare = location.pathname.startsWith("/tickets");
  const [existing, setExisting] = useState(
    aftercare || !!params.get("order_id"),
  );
  const [orderId, setOrderId] = useState(params.get("order_id") || "");
  const query = useData<Page<Order>>(
    "/orders?ids=" + encodeURIComponent(orderId || "none"),
  );
  const order = existing
    ? query.data?.items.find((o) => o.id === orderId)
    : undefined;
  const [values, setValues] = useState<Values>(() => initialOrder());
  const [withTicket, setWithTicket] = useState(aftercare);
  const [ticket, setTicket] = useState<Values>({});
  const [requestId] = useState(() => crypto.randomUUID());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (order) setValues(initialOrder(order));
  }, [order?.id, order?.version]);
  const change = (key: string, value: string) =>
    setValues((v) => ({ ...v, [key]: value }));
  if (!profile.permissions.includes("orders.write"))
    return <Empty title="需要订单管理权限" />;
  return (
    <>
      <div className="page-header">
        <div>
          <h1>{aftercare ? "创建售后工单" : "录入订单"}</h1>
          <p>
            订单档案保存全部订单，仅在确认异常后创建售后。物流资料在这里一次填写。
          </p>
        </div>
      </div>
      <section className="card form-card intake-card">
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            setBusy(true);
            setError("");
            try {
              const result = await post<{
                order: Order;
                ticket: Ticket | null;
              }>("/orders/intake", {
                request_id: requestId,
                order: orderPayload(values, order),
                ...(existing
                  ? {
                      existing_order_id: orderId,
                      order_version: order?.version,
                    }
                  : {}),
                ticket: withTicket ? ticket : null,
              });
              ui.toast(
                result.ticket
                  ? result.ticket.investigation?.message || "订单与售后已保存。"
                  : "订单已保存到档案，未创建售后工单。",
              );
              navigate(
                result.ticket
                  ? "/tickets/" + result.ticket.id
                  : "/orders?q=" +
                      encodeURIComponent(
                        result.order.external_id.slice(0, 100),
                      ),
              );
            } catch (e) {
              setError((e as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        >
          <div className="intake-mode">
            <label>
              <input
                type="radio"
                name="order-source"
                checked={!existing}
                onChange={() => {
                  setExisting(false);
                  setValues(initialOrder());
                }}
              />
              新录入订单
            </label>
            <label>
              <input
                type="radio"
                name="order-source"
                checked={existing}
                onChange={() => {
                  setExisting(true);
                  if (order) setValues(initialOrder(order));
                }}
              />
              从订单档案选择
            </label>
          </div>
          {existing && (
            <div className="field">
              <label htmlFor="field-order_id">关联订单 *</label>
              <OrderPicker
                value={orderId}
                onChange={setOrderId}
                selected={order}
              />
              {orderId && (
                <QueryState query={query}>
                  {!order && (
                    <p role="alert">订单不存在或无权访问，请重新选择。</p>
                  )}
                </QueryState>
              )}
            </div>
          )}
          {(!existing || order) && (
            <>
              <h2>订单与物流资料</h2>
              {order && (
                <p className="subtle">
                  已带出档案资料；只有修改订单事实时才需重新核查快照。此订单已有{" "}
                  {order.ticket_count || 0} 笔售后，
                  <Link
                    className="text-link"
                    to={"/tickets?order_id=" + order.id}
                  >
                    查看关联售后
                  </Link>
                  。
                </p>
              )}
              <OrderFields values={values} onChange={change} prefix="intake-" />
              {profile.permissions.includes("tickets.write") && (
                <label className="intake-choice">
                  <input
                    type="checkbox"
                    checked={withTicket}
                    onChange={(e) => setWithTicket(e.target.checked)}
                  />
                  同时创建售后工单
                </label>
              )}
              {withTicket && (
                <section className="intake-ticket">
                  <h2>售后诉求</h2>
                  <TicketFields
                    values={ticket}
                    onChange={(key, value) =>
                      setTicket((v) => ({ ...v, [key]: value }))
                    }
                    prefix="intake-ticket-"
                  />
                </section>
              )}
            </>
          )}
          {error && (
            <p className="form-error" role="alert">
              {error}
            </p>
          )}
          <div className="header-actions">
            <Link className="button" to="/orders">
              返回订单档案
            </Link>
            <button
              className="button primary"
              disabled={
                busy || (existing && (!order || query.loading || !!query.error))
              }
            >
              {busy ? "正在保存…" : withTicket ? "保存并进入售后" : "保存订单"}
            </button>
          </div>
        </form>
      </section>
    </>
  );
}

export function BatchTicketPage() {
  const [params] = useSearchParams();
  const { profile } = useAuth();
  const navigate = useNavigate();
  const ui = useUI();
  const ids = (params.get("orders") || "").split(",").filter(Boolean);
  const query = useData<Page<Order>>(
    "/orders?ids=" +
      encodeURIComponent(ids.join(",") || "none") +
      "&page_size=50",
  );
  const [drafts, setDrafts] = useState<
    Record<string, { order: Values; ticket: Values }>
  >({});
  const [index, setIndex] = useState(0);
  const [requestId] = useState(() => crypto.randomUUID());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const rows = query.data?.items || [];
  const order = rows[index];
  const draft = order
    ? drafts[order.id] || { order: initialOrder(order), ticket: {} }
    : null;
  const complete = rows.every((o) => {
    const t = drafts[o.id]?.ticket;
    return (
      t?.issue && t.title?.trim().length >= 2 && t.request?.trim().length >= 2
    );
  });
  const change = (part: "order" | "ticket", key: string, value: string) => {
    if (order && draft)
      setDrafts((v) => ({
        ...v,
        [order.id]: { ...draft, [part]: { ...draft[part], [key]: value } },
      }));
  };
  if (
    !profile.permissions.includes("tickets.write") ||
    !profile.permissions.includes("orders.write")
  )
    return <Empty title="需要订单与售后管理权限" />;
  if (!ids.length || ids.length > 50 || new Set(ids).size !== ids.length)
    return (
      <Empty
        title="请在订单档案选择1至50笔异常订单"
        action={
          <Link className="button" to="/orders">
            前往订单档案
          </Link>
        }
      />
    );
  return (
    <>
      <div className="page-header">
        <div>
          <h1>确认异常订单并创建售后</h1>
          <p>
            共 {ids.length}{" "}
            笔，请逐笔核对订单、物流和该客户的实际诉求。未选择的正常订单不会创建售后。
          </p>
        </div>
      </div>
      <QueryState query={query}>
        {rows.length !== ids.length ? (
          <Empty title="部分订单不存在或无权访问，请返回档案重新选择" />
        ) : (
          order &&
          draft && (
            <section className="card form-card intake-card">
              <form
                onSubmit={async (e) => {
                  e.preventDefault();
                  setError("");
                  if (index < rows.length - 1) {
                    setIndex(index + 1);
                    return;
                  }
                  if (!complete) {
                    setError(
                      "请逐笔填写异常情况、标题和客户诉求后再统一提交。",
                    );
                    return;
                  }
                  setBusy(true);
                  try {
                    const result = await post<{ intake_id: string }>(
                      "/tickets/batch",
                      {
                        request_id: requestId,
                        items: rows.map((o) => ({
                          order_id: o.id,
                          order_version: o.version,
                          ...drafts[o.id].ticket,
                          order: orderPayload(drafts[o.id].order, o),
                        })),
                      },
                    );
                    ui.toast("已创建 " + rows.length + " 笔售后工单。");
                    navigate("/tickets?intake_id=" + result.intake_id);
                  } catch (e) {
                    setError((e as Error).message);
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                <h2>
                  第 {index + 1} / {rows.length} 笔 · {order.external_id}
                </h2>
                <p className="subtle">
                  {order.product} · {order.quantity} 件 ·{" "}
                  {money(order.paid_cents)}
                </p>
                <TicketFields
                  values={draft.ticket}
                  onChange={(k, v) => change("ticket", k, v)}
                  prefix="batch-ticket-"
                />
                <details className="batch-order">
                  <summary>核对或补充订单与物流资料</summary>
                  <OrderFields
                    values={draft.order}
                    onChange={(k, v) => change("order", k, v)}
                    prefix="batch-order-"
                  />
                </details>
                {error && (
                  <p className="form-error" role="alert">
                    {error}
                  </p>
                )}
                <div className="header-actions">
                  <Link className="button" to="/orders">
                    返回档案
                  </Link>
                  <button
                    className="button"
                    type="button"
                    disabled={!index || busy}
                    onClick={() => setIndex(index - 1)}
                  >
                    上一笔
                  </button>
                  <button className="button primary" disabled={busy}>
                    {busy
                      ? "正在创建…"
                      : index < rows.length - 1
                        ? "确认并查看下一笔"
                        : "统一创建 " + rows.length + " 笔售后"}
                  </button>
                </div>
              </form>
            </section>
          )
        )}
      </QueryState>
    </>
  );
}
