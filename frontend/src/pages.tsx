import {
  useContext,
  useDeferredValue,
  useEffect,
  useRef,
  useState,
} from "react";
import {
  Link,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router-dom";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  ArrowDownToLine,
  ArrowUpRight,
  BookOpen,
  Box,
  Check,
  ChevronRight,
  Clock,
  FileCheck,
  FileText,
  Plus,
  Search,
  ShieldCheck,
  Sparkles,
  Upload,
  Users,
  Wallet,
} from "lucide-react";
import { api, cents, date, localNow, money, post } from "./api";
import {
  Arrow,
  Badge,
  Changes,
  Empty,
  Glyph,
  Pagination,
  QueryState,
  useAuth,
  useData,
  useUI,
} from "./ui";
import type {
  Action,
  Audit,
  Member,
  Order,
  Overview,
  Page,
  Policy,
  Proposal,
  Run,
  Ticket,
} from "./types";
import { editOrder, EvaluationSummary, ReviewRun } from "./management";
import { RunEventSummary } from "./RunEventSummary";
import { investigationResult } from "./runSummary";
import { KnowledgeQuestions } from "./KnowledgeQuestions";

function Header({
  title,
  description,
  eyebrow,
  children,
}: {
  title: string;
  description: string;
  eyebrow?: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="page-header">
      <div>
        {eyebrow && <div className="eyebrow">{eyebrow}</div>}
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      <div className="header-actions">{children}</div>
    </div>
  );
}
export function TicketTable({ rows }: { rows: Ticket[] }) {
  const navigate = useNavigate();
  return rows.length ? (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th scope="col">工单 / 订单</th>
            <th scope="col">当前状态</th>
            <th className="hide-mobile" scope="col">
              处理时限
            </th>
            <th className="hide-mobile" scope="col">
              金额
            </th>
            <th scope="col">查看</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((t, i) => (
            <tr
              key={t.id}
              className="linked-row"
              onClick={(e) => {
                if (!(e.target as HTMLElement).closest("a,button,input,select"))
                  navigate("/tickets/" + t.id);
              }}
            >
              <td>
                <div className="issue-cell">
                  <span
                    className={
                      "product-thumb " +
                      (i % 3 === 1 ? "rose" : i % 3 === 2 ? "blue" : "")
                    }
                  >
                    <Glyph icon={Box} />
                  </span>
                  <span>
                    <Link className="issue-title" to={"/tickets/" + t.id}>
                      {t.title}
                    </Link>
                    <span className="issue-id">{t.order.external_id}</span>
                  </span>
                </div>
              </td>
              <td>
                <Badge state={t.state} />
              </td>
              <td className="hide-mobile">
                <span
                  className={
                    "sla " +
                    (new Date(t.due_at).getTime() < Date.now() ? "soon" : "")
                  }
                >
                  {date(t.due_at)}
                </span>
              </td>
              <td className="hide-mobile">{money(t.order.paid_cents)}</td>
              <td>
                <Link
                  className="row-button"
                  to={"/tickets/" + t.id}
                  aria-label={"查看工单 " + t.number}
                >
                  <Arrow />
                </Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  ) : (
    <Empty
      title="暂无匹配的工单"
      description="创建售后工单，或调整当前筛选条件。"
    />
  );
}
export function useNewTicket() {
  const navigate = useNavigate();
  return () => navigate("/tickets/new");
}
export function OverviewPage() {
  const { profile } = useAuth();
  const query = useData<Overview>("/overview?scope=mine", 15000);
  const data = query.data;
  const supervisor = ["supervisor", "admin"].includes(profile.role);
  const stats = supervisor
    ? [
        [
          "待我审批",
          data?.waiting_approval || 0,
          ShieldCheck,
          "waiting_approval",
        ],
        [
          "待我核验",
          data?.states.waiting_verification || 0,
          FileCheck,
          "waiting_verification",
        ],
        ["需要对账", data?.states.reconciling || 0, Clock, "reconciling"],
        ["待核查证据", data?.evidence_review || 0, FileText, "evidence_review"],
      ]
    : [
        ["待我处理工单", data?.open || 0, FileText, "open"],
        [
          "待补充材料",
          data?.states.waiting_evidence || 0,
          Clock,
          "waiting_evidence",
        ],
        [
          "待执行登记",
          data?.states.waiting_execution || 0,
          ShieldCheck,
          "waiting_execution",
        ],
        ["今日已闭环", data?.closed_today || 0, Check, "closed_today"],
      ];
  return (
    <>
      <Header
        title={"你好，" + profile.user.name}
        description={
          supervisor
            ? "优先处理可由你审批、核验和对账的事项。团队完整数据见业务分析。"
            : "优先处理你负责的工单和当前角色待办。团队完整数据见业务分析。"
        }
        eyebrow="YOUR DAILY WORKSPACE"
      >
        {profile.permissions.includes("orders.write") && (
          <>
            <Link className="button primary" to="/orders/new">
              <Glyph icon={Plus} />
              录入订单
            </Link>
            <Link className="button" to="/orders/import">
              <Glyph icon={Upload} />
              批量导入
            </Link>
          </>
        )}
      </Header>
      <QueryState query={query}>
        {data && (
          <>
            <div className="stats">
              {stats.map(([label, value, I, state]) => (
                <Link
                  className="stat stat-link"
                  key={label as string}
                  to={"/tickets?scope=mine&state=" + state}
                  aria-label={`${label}：${value} 笔，查看全部`}
                >
                  <div className="stat-name">
                    {label as string}
                    <Glyph icon={I as typeof Check} />
                  </div>
                  <div className="stat-value">
                    {value as number}
                    <span>笔</span>
                  </div>
                  <div className="stat-foot">
                    {state === "closed_today"
                      ? "已核验结果并完成沟通"
                      : "按当前账户职责筛选"}
                  </div>
                  <span className="stat-action">
                    查看全部 <Arrow />
                  </span>
                </Link>
              ))}
            </div>
            <div className="overview-grid">
              <div>
                <section className="card">
                  <div className="card-head">
                    <div>
                      <h2>我的待办</h2>
                      <p>按处理时限查看当前工单</p>
                    </div>
                    <Link className="text-link" to="/tickets?scope=mine">
                      全部待办
                      <Arrow />
                    </Link>
                  </div>
                  <TicketTable rows={data.recent.slice(0, 6)} />
                  <div className="table-foot">
                    <span>数据更新于 {date(data.updated_at)}</span>
                    <span>实际业务记录</span>
                  </div>
                </section>
                <section className="card weekly">
                  <div className="card-head">
                    <div>
                      <h2>近七日处理趋势</h2>
                      <p>已核验并关闭的工单</p>
                    </div>
                    <span className="legend">已闭环 · 笔</span>
                  </div>
                  <div className="chart-area">
                    <ResponsiveContainer width="100%" height={165}>
                      <AreaChart
                        data={data.trend}
                        margin={{ top: 10, right: 18, bottom: 0, left: -20 }}
                      >
                        <defs>
                          <linearGradient
                            id="chartFill"
                            x1="0"
                            y1="0"
                            x2="0"
                            y2="1"
                          >
                            <stop
                              offset="0"
                              stopColor="#84b799"
                              stopOpacity={0.3}
                            />
                            <stop
                              offset="1"
                              stopColor="#84b799"
                              stopOpacity={0}
                            />
                          </linearGradient>
                        </defs>
                        <CartesianGrid vertical={false} stroke="#edf1eb" />
                        <XAxis
                          dataKey="date"
                          axisLine={false}
                          tickLine={false}
                          tick={{ fontSize: 12, fill: "#66756d" }}
                        />
                        <YAxis
                          allowDecimals={false}
                          axisLine={false}
                          tickLine={false}
                          tick={{ fontSize: 12, fill: "#66756d" }}
                        />
                        <Tooltip
                          formatter={(value) => [value + " 笔", "已闭环"]}
                        />
                        <Area
                          type="monotone"
                          dataKey="closed"
                          stroke="#4e8564"
                          fill="url(#chartFill)"
                          strokeWidth={2}
                        />
                      </AreaChart>
                    </ResponsiveContainer>
                  </div>
                </section>
              </div>
              <aside className="side-stack">
                <section className="assistant-card">
                  <div className="assistant-label">
                    <Glyph icon={Sparkles} />
                    智能调查
                  </div>
                  <div className="assistant-title">
                    关键事实先行，
                    <br />
                    每一步都有依据。
                  </div>
                  <p className="assistant-subtitle">
                    {data.model_enabled
                      ? data.running + " 个模型任务正在排队或运行"
                      : "模型尚未启用，可继续人工业务流程"}
                  </p>
                  <div className="assistant-step">
                    <Glyph icon={Check} />
                    <span>
                      订单、政策与证据分别核查
                      <small>动态加载当前步骤需要的信息</small>
                    </span>
                  </div>
                  <div className="assistant-step current">
                    <Glyph icon={ShieldCheck} />
                    <span>
                      关键动作由授权人员确认
                      <small>批准、执行与核验清晰分开</small>
                    </span>
                  </div>
                  <div className="assistant-divider" />
                  <div className="assistant-bottom">
                    <span>
                      {data.model_enabled ? "已启用模型调查" : "人工流程可用"}
                    </span>
                    <Link to="/runs">
                      调查记录
                      <Arrow />
                    </Link>
                  </div>
                </section>
                <section className="card todo">
                  <h2>我的处理队列</h2>
                  {(supervisor
                    ? [
                        ["waiting_approval", "等待我审批", ShieldCheck, ""],
                        ["evidence_review", "证据待核查", FileText, "blue"],
                        [
                          "waiting_verification",
                          "执行结果待核验",
                          FileCheck,
                          "green",
                        ],
                      ]
                    : [
                        ["new", "等待我调查", Search, ""],
                        ["waiting_evidence", "等待补充材料", FileText, "blue"],
                        [
                          "waiting_execution",
                          "等待我执行登记",
                          FileCheck,
                          "green",
                        ],
                      ]
                  ).map(([state, label, I, tone]) => (
                    <Link
                      className="todo-row"
                      key={state as string}
                      to={"/tickets?scope=mine&state=" + state}
                    >
                      <span className={"todo-icon " + tone}>
                        <Glyph icon={I as typeof Check} />
                      </span>
                      <span>{label as string}</span>
                      <strong>
                        {state === "evidence_review"
                          ? data.evidence_review
                          : data.states[state as string] || 0}
                      </strong>
                      <Arrow />
                    </Link>
                  ))}
                </section>
              </aside>
            </div>
          </>
        )}
      </QueryState>
    </>
  );
}
function useFeishuIntake() {
  const { profile } = useAuth();
  const ui = useUI();
  const navigate = useNavigate();
  const demo = profile.tenant.is_demo;
  return () =>
    ui.open({
      title: demo ? "模拟飞书多维表格填报" : "飞书多维表格自动建单",
      description: demo
        ? "填写一行模拟表格记录，在当前演示空间生成工单。相同记录编号再次提交会返回原工单。"
        : "客服填表后，由飞书自动化提交到循单，生成工单并回填工单号。实际使用前需由管理员配置接入凭证和飞书流程。",
      body: (
        <p className="modal-copy">
          先导入订单，再填写表格。当前支持物流停滞、送达超时和确认丢件三类问题。
          {demo
            ? " 此操作仅使用本地演示数据，尚未连接真实飞书表格。"
            : " 处理状态可通过飞书定时流程查询并回填。"}
        </p>
      ),
      fields: demo
        ? [
            {
              name: "record_id",
              label: "表格记录编号",
              required: true,
              help: "用于防止重复建单；同一个事件始终使用同一个编号。",
            },
            {
              name: "channel",
              label: "订单渠道",
              required: true,
              help: "须与已导入订单的渠道完全一致。",
            },
            { name: "external_id", label: "外部订单号", required: true },
            { name: "title", label: "问题标题", required: true },
            {
              name: "issue",
              label: "问题类型",
              type: "select",
              required: true,
              options: [
                { value: "物流长期未更新", label: "物流长期未更新" },
                { value: "超过承诺送达时效", label: "超过承诺送达时效" },
                { value: "承运商已确认丢件", label: "承运商已确认丢件" },
              ],
            },
            {
              name: "request",
              label: "客户诉求",
              type: "textarea",
              required: true,
            },
          ]
        : [],
      initial: {
        record_id: "rec_demo_" + crypto.randomUUID(),
        channel: "商家导入",
        title: "【模拟飞书填表】物流咨询",
        issue: "物流长期未更新",
        request: "合成客户希望查询包裹进展，请核查物流。",
      },
      submitLabel: demo ? "提交记录并查看工单" : "我知道了",
      onSubmit: demo
        ? async (fd) => {
            const result = await post<{ ticket_id: string; created: boolean }>(
              "/integrations/feishu/simulate",
              {
                ...Object.fromEntries(fd),
                base_id: "demo",
                table_id: "demo",
              },
            );
            ui.toast(
              result.created
                ? "表格记录已生成工单。"
                : "该记录已建单，已打开原工单。",
            );
            navigate("/tickets/" + result.ticket_id);
          }
        : undefined,
    });
}

export function TicketsPage() {
  const { profile } = useAuth();
  const [params, setParams] = useSearchParams();
  const q = params.get("q") || "";
  const state = params.get("state") || "";
  const scope = params.get("scope") || "team";
  const orderId = params.get("order_id") || "";
  const intakeId = params.get("intake_id") || "";
  const filters = { scope, order_id: orderId, intake_id: intakeId };
  const page = Number(params.get("page") || 1);
  const deferred = useDeferredValue(q);
  const query = useData<Page<Ticket>>(
    `/tickets?q=${encodeURIComponent(deferred)}&state=${state}&scope=${scope}&page=${page}&order_id=${encodeURIComponent(orderId)}&intake_id=${encodeURIComponent(intakeId)}`,
  );
  const create = useNewTicket();
  const feishu = useFeishuIntake();
  const titles: Record<string, string> = {
    open: "待处理工单",
    waiting_approval: "等待审批",
    closed_today: "今日已闭环",
    waiting_evidence: "等待补证",
    evidence_review: "待核查证据",
  };
  return (
    <>
      <Header
        title={
          (intakeId
            ? "本批售后 · "
            : orderId
              ? "关联订单 · "
              : scope === "mine"
                ? "我的待办 · "
                : "") + (titles[state] || "售后工单")
        }
        description={
          scope === "mine"
            ? "仅显示当前账户职责范围内的事项，筛选与首页待办保持一致。"
            : state === "open"
              ? "全部尚未关闭的工单，包含待调查、审批、执行和核验等事项。"
              : state === "closed_today"
                ? "按北京时间展示今日已核验并完成沟通的全部闭环工单。"
                : "让每一个客户诉求，都有清晰的下一步。"
        }
      >
        {profile.permissions.includes("tickets.write") && (
          <>
            <button className="button" onClick={feishu}>
              <Glyph icon={FileText} />
              飞书多维表格
            </button>
            <button className="button primary" onClick={create}>
              <Glyph icon={Plus} />
              新建工单
            </button>
          </>
        )}
      </Header>
      <section className="card">
        <div className="list-toolbar">
          <label className="search-field">
            <Glyph icon={Search} />
            <input
              aria-label="搜索工单或订单"
              placeholder="搜索工单号、订单号或问题"
              value={q}
              onChange={(e) =>
                setParams({ q: e.target.value, state, ...filters })
              }
            />
          </label>
          <select
            aria-label="按状态筛选"
            value={state}
            onChange={(e) =>
              setParams({ q, state: e.target.value, ...filters })
            }
          >
            <option value="">全部状态</option>
            <option value="open">待处理 · 全部未关闭</option>
            <option value="closed_today">今日已闭环 · 北京时间</option>
            {profile.permissions.includes("evidence.verify") && (
              <option value="evidence_review">待核查证据</option>
            )}
            {[
              ["new", "待调查"],
              ["investigating", "调查中"],
              ["draft", "待修改"],
              ["waiting_evidence", "待补证"],
              ["waiting_approval", "待审批"],
              ["waiting_execution", "待执行"],
              ["waiting_verification", "待核验"],
              ["reconciling", "对账中"],
              ["resolved", "已解决"],
              ["closed", "已关闭"],
            ].map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
        </div>
        <QueryState query={query}>
          {query.data && (
            <>
              <TicketTable rows={query.data.items} />
              <div className="table-foot">
                <span>
                  共 {query.data.total} 笔 · 第 {page} 页
                </span>
                <div className="pagination">
                  <button
                    className="button"
                    disabled={page <= 1}
                    onClick={() =>
                      setParams({
                        q,
                        state,
                        ...filters,
                        page: String(page - 1),
                      })
                    }
                  >
                    上一页
                  </button>
                  <button
                    className="button"
                    disabled={page * 25 >= query.data.total}
                    onClick={() =>
                      setParams({
                        q,
                        state,
                        ...filters,
                        page: String(page + 1),
                      })
                    }
                  >
                    下一页
                  </button>
                </div>
              </div>
            </>
          )}
        </QueryState>
      </section>
    </>
  );
}
export function OrdersPage() {
  const { profile } = useAuth();
  const ui = useUI();
  const [params, setParams] = useSearchParams();
  const importId = params.get("import_id") || "";
  const [search, setSearch] = useState(params.get("q") || "");
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const deferred = useDeferredValue(search);
  const query = useData<Page<Order>>(
    "/orders?q=" +
      encodeURIComponent(deferred) +
      "&page_size=50&page=" +
      page +
      "&import_id=" +
      encodeURIComponent(importId),
  );
  useEffect(() => setSelected(new Set()), [deferred, page, importId]);
  const can = profile.permissions.includes("orders.write");
  const navigate = useNavigate();
  const verify = (o: Order) =>
    ui.open({
      title: "确认订单快照",
      description:
        "请在外部系统核对当前订单、实付金额与历史退款。该确认记录会用于后续提案校验。",
      fields: [
        {
          name: "confirmed",
          label: "我已核查外部订单与历史退款，当前快照准确",
          type: "checkbox",
          required: true,
        },
      ],
      onSubmit: async () => {
        await post("/orders/" + o.id + "/verify", { version: o.version });
        ui.toast("订单快照核查已记录。");
      },
    });
  return (
    <>
      <Header
        title="订单档案"
        description="保存全部订单与物流资料；选择确有异常的订单，再创建售后工单。"
      >
        {can && (
          <>
            <button className="button" onClick={() => navigate("/orders/new")}>
              <Glyph icon={Plus} />
              录入订单
            </button>
            <Link className="button primary" to="/orders/import">
              <Glyph icon={Upload} />
              批量导入
            </Link>
          </>
        )}
      </Header>
      {importId && (
        <div className="notice">
          当前显示本次导入关联的订单（含重复导入时保留的已有订单）。
          <button
            className="text-link"
            onClick={() => {
              setParams({});
              setSearch("");
              setPage(1);
            }}
          >
            显示全部订单
          </button>
        </div>
      )}
      <section className="card">
        <div className="list-toolbar">
          <label className="search-field">
            <Glyph icon={Search} />
            <input
              aria-label="搜索订单"
              placeholder="搜索订单号或商品"
              maxLength={100}
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
                setPage(1);
              }}
            />
          </label>
          <span className="subtle">平台尚未接入 · 商家自主导入</span>
        </div>
        {profile.permissions.includes("tickets.write") && (
          <div className="selection-toolbar">
            <span>选择本页异常订单，已选 {selected.size} / 50 笔</span>
            <button
              className="button primary"
              disabled={!selected.size || query.loading}
              onClick={() =>
                navigate("/tickets/batch?orders=" + [...selected].join(","))
              }
            >
              逐笔确认并创建售后
            </button>
          </div>
        )}
        <QueryState query={query}>
          {query.data?.items.length ? (
            <div className="table-wrap business-table">
              <table>
                <thead>
                  <tr>
                    {profile.permissions.includes("tickets.write") && (
                      <th scope="col">
                        <input
                          type="checkbox"
                          aria-label="选择本页全部订单"
                          checked={
                            !!query.data.items.length &&
                            query.data.items.every((o) => selected.has(o.id))
                          }
                          onChange={(e) =>
                            setSelected(
                              e.target.checked
                                ? new Set(query.data!.items.map((o) => o.id))
                                : new Set(),
                            )
                          }
                        />
                      </th>
                    )}
                    <th>订单 / 商品</th>
                    <th>数量 / 物流</th>
                    <th>实付金额</th>
                    <th>状态</th>
                    <th>事实核查</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {query.data.items.map((o) => (
                    <tr key={o.id}>
                      {profile.permissions.includes("tickets.write") && (
                        <td>
                          <input
                            type="checkbox"
                            aria-label={"选择异常订单 " + o.external_id}
                            checked={selected.has(o.id)}
                            onChange={(e) =>
                              setSelected((old) => {
                                const next = new Set(old);
                                if (e.target.checked) next.add(o.id);
                                else next.delete(o.id);
                                return next;
                              })
                            }
                          />
                        </td>
                      )}
                      <td>
                        <strong className="issue-title">{o.external_id}</strong>
                        <span className="issue-id">{o.product}</span>
                      </td>
                      <td>
                        {o.quantity} 件
                        <small className="cell-help">
                          {o.carrier_code || "快递公司未录入"}
                        </small>
                        <small className="cell-help">
                          {o.tracking_number || "运单号未录入"}
                        </small>
                      </td>
                      <td>
                        {money(o.paid_cents)}
                        <small className="cell-help">
                          已退 {money(o.refunded_cents)} · 占用{" "}
                          {money(o.reserved_cents)}
                        </small>
                      </td>
                      <td>
                        {
                          (
                            {
                              paid: "已付款",
                              shipped: "已发货",
                              delivered: "已签收",
                              refunded: "已退款",
                              cancelled: "已取消",
                            } as Record<string, string>
                          )[o.status]
                        }
                      </td>
                      <td>
                        {o.verified_at ? (
                          <>
                            <span className="badge green">已人工核查</span>
                            <small className="cell-help">
                              {date(o.verified_at)}
                            </small>
                          </>
                        ) : (
                          <span className="badge amber">待核查</span>
                        )}
                      </td>
                      <td>
                        <div className="order-actions">
                          {profile.permissions.includes("tickets.write") && (
                            <Link
                              className="button"
                              to={"/tickets/new?order_id=" + o.id}
                            >
                              创建售后
                            </Link>
                          )}
                          {!!o.ticket_count && (
                            <Link
                              className="text-link"
                              to={"/tickets?order_id=" + o.id}
                            >
                              查看 {o.ticket_count} 笔售后
                            </Link>
                          )}
                          {can && (
                            <>
                              <button
                                className="button"
                                onClick={() => editOrder(o, ui)}
                              >
                                更新
                              </button>
                              <button
                                className="button"
                                onClick={() => verify(o)}
                              >
                                核查快照
                              </button>
                            </>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty
              title="从第一笔订单开始"
              description="录入或导入订单后，即可关联售后工单。"
              action={
                can && (
                  <Link className="button primary" to="/orders/import">
                    批量导入
                    <Arrow />
                  </Link>
                )
              }
            />
          )}
        </QueryState>
        {query.data && (
          <Pagination page={page} total={query.data.total} onPage={setPage} />
        )}
      </section>
    </>
  );
}
interface ImportJob {
  id: string;
  filename: string;
  state: string;
  inserted: number;
  skipped: number;
  valid_count: number;
  error_count: number;
  created_at: string;
}
interface Preview {
  id: string;
  valid_count: number;
  error_count: number;
  errors: { line: number; message: string; fields: string[] }[];
  sample: Record<string, unknown>[];
}
export function ImportPage() {
  const ui = useUI();
  const { reload } = useContext(Changes);
  const { profile } = useAuth();
  const query = useData<ImportJob[]>("/imports", 3000);
  const [file, setFile] = useState<File | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [mapping, setMapping] = useState("{}");
  if (!profile.permissions.includes("orders.write"))
    return <Empty title="需要订单管理权限" />;
  return (
    <>
      <Link className="back-link" to="/orders">
        返回订单档案
      </Link>
      <Header
        title="导入商家订单"
        description="先预检，再确认入库。全部订单进入档案，完成后再选择异常订单建售后；重复订单保留现有记录。"
      >
        <a className="button" href="/api/v1/imports/template">
          <Glyph icon={ArrowDownToLine} />
          下载 CSV 模板
        </a>
      </Header>
      <div className="import-grid">
        <section className="card form-card">
          <div className="step-label">01 · 上传与预检</div>
          <h2>选择订单文件</h2>
          <p className="modal-copy">
            支持 UTF-8
            CSV，单次10MB、10000行以内。可识别模板中的中文或英文字段名。
          </p>
          <div className="upload-zone">
            <Glyph icon={Upload} />
            <label htmlFor="csv-file">
              {file ? file.name : "选择 CSV 文件"}
            </label>
            <input
              id="csv-file"
              ref={fileInput}
              type="file"
              accept=".csv,text/csv"
              disabled={busy}
              onChange={(e) => {
                setFile(e.target.files?.[0] || null);
                setPreview(null);
              }}
            />
            <small>
              订单号、商品、数量、实付金额、状态、下单时间为必需字段
            </small>
          </div>
          <details className="mapping-details">
            <summary>自定义字段映射</summary>
            <label htmlFor="mapping">源列名到系统字段的 JSON 映射</label>
            <textarea
              id="mapping"
              value={mapping}
              disabled={busy}
              onChange={(e) => {
                setMapping(e.target.value);
                setPreview(null);
              }}
            />
            <small>
              例如：{`{"买家实付":"paid_amount","业务单号":"external_id"}`}
            </small>
          </details>
          {error && (
            <p role="alert" className="form-error">
              {error}
            </p>
          )}
          <button
            className="button primary"
            disabled={!file || busy}
            onClick={async () => {
              setBusy(true);
              setError("");
              setPreview(null);
              try {
                const body = new FormData();
                body.set("file", file!);
                body.set("mapping", mapping);
                setPreview(
                  await api<Preview>("/imports/preview", {
                    method: "POST",
                    body,
                  }),
                );
              } catch (e) {
                setError((e as Error).message);
              } finally {
                setBusy(false);
              }
            }}
          >
            {busy ? "正在预检…" : "开始预检"}
          </button>
        </section>
        <section className="card form-card">
          <div className="step-label">02 · 确认结果</div>
          {preview ? (
            <>
              <h2>{preview.valid_count} 行可导入</h2>
              <p className="modal-copy">
                {preview.error_count}{" "}
                行存在格式问题。确认后仅导入通过预检的记录；已有订单将跳过。
              </p>
              {preview.errors.length > 0 && (
                <ul className="validation-list">
                  {preview.errors.slice(0, 12).map((e, i) => (
                    <li key={i}>
                      第 {e.line} 行：{e.message}
                      {e.fields.length ? "（" + e.fields.join("、") + "）" : ""}
                    </li>
                  ))}
                </ul>
              )}
              <div className="import-preview">
                {preview.sample.map((row, i) => (
                  <div key={i}>
                    <strong>{String(row.external_id)}</strong>
                    <span>{String(row.product)}</span>
                  </div>
                ))}
              </div>
              <button
                className="button primary"
                disabled={!preview.valid_count || busy}
                onClick={async () => {
                  setBusy(true);
                  try {
                    await post("/imports/" + preview.id + "/confirm");
                    setPreview(null);
                    setFile(null);
                    if (fileInput.current) fileInput.current.value = "";
                    reload();
                    ui.toast("导入任务已提交，可在下方查看进度。");
                  } catch (e) {
                    setError((e as Error).message);
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                确认导入
              </button>
            </>
          ) : (
            <Empty
              title="等待文件预检"
              description="这里会显示可导入行、问题行与订单样例。"
            />
          )}
        </section>
      </div>
      <section className="card">
        <div className="card-head">
          <h2>导入记录</h2>
          <span className="subtle">后台持续处理，离开页面后仍保留进度</span>
        </div>
        <QueryState query={query}>
          {query.data?.length ? (
            <div className="table-wrap business-table">
              <table>
                <thead>
                  <tr>
                    <th>文件</th>
                    <th>状态</th>
                    <th>已导入 / 跳过</th>
                    <th>问题行</th>
                    <th>提交时间</th>
                    <th>下一步</th>
                  </tr>
                </thead>
                <tbody>
                  {query.data.map((job) => (
                    <tr key={job.id}>
                      <td>{job.filename}</td>
                      <td>
                        <Badge state={job.state} />
                      </td>
                      <td>
                        {job.inserted} / {job.skipped}
                      </td>
                      <td>{job.error_count}</td>
                      <td>{date(job.created_at)}</td>
                      <td>
                        {job.state === "completed" ? (
                          <Link
                            className="button"
                            to={"/orders?import_id=" + job.id}
                          >
                            选择异常订单建售后
                          </Link>
                        ) : (
                          "完成后可选择异常订单"
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty title="还没有导入记录" />
          )}
        </QueryState>
      </section>
    </>
  );
}

export function useDecision() {
  const ui = useUI();
  return (p: Proposal, decision: "approve" | "reject" | "supplement") =>
    ui.open({
      title:
        decision === "approve"
          ? "批准并转交执行"
          : decision === "reject"
            ? "拒绝当前提案"
            : "要求补充证据",
      description:
        decision === "approve"
          ? `确认批准 ${p.order?.external_id || "当前订单"} 的 ${money(p.amount_cents)} 退款提案。批准只生成执行任务，结果需要另行登记和核验。`
          : "填写具体原因，下一位处理人会据此继续工作。",
      fields:
        decision === "approve"
          ? [
              {
                name: "confirmed",
                label: "我已核对订单、证据与金额，同意转交人工执行",
                type: "checkbox",
                required: true,
              },
            ]
          : [
              {
                name: "note",
                label: decision === "reject" ? "拒绝原因" : "补证要求",
                type: "textarea",
                required: true,
              },
            ],
      submitLabel: decision === "approve" ? "确认批准" : "提交意见",
      onSubmit: async (fd) => {
        await post("/approvals/" + p.id + "/decisions", {
          decision,
          payload_hash: p.payload_hash,
          note: String(fd.get("note") || ""),
        });
        ui.toast(
          decision === "approve"
            ? "审批通过，工单进入待人工执行。"
            : "处理意见已保存。",
        );
      },
    });
}
function ApprovalDetail({ proposal }: { proposal: Proposal }) {
  const query = useData<Ticket>("/tickets/" + proposal.ticket_id);
  const decide = useDecision();
  const { profile } = useAuth();
  const ui = useUI();
  const t = query.data;
  return (
    <section className="card approval-detail">
      <QueryState query={query}>
        {t && (
          <>
            <div className="detail-header">
              <div>
                <div className="detail-title-line">
                  <h2>{t.title}</h2>
                  <Badge state={proposal.state} />
                </div>
                <div className="subtitle">
                  {t.number} · 提交于 {date(proposal.created_at)}
                </div>
              </div>
            </div>
            <div className="summary-strip">
              <div className="summary-item">
                <small>关联订单</small>
                <strong>{t.order.external_id}</strong>
                <p>{t.order.channel}</p>
              </div>
              <div className="summary-item">
                <small>商品</small>
                <strong>{t.order.product}</strong>
              </div>
              <div className="summary-item">
                <small>实付金额</small>
                <strong>{money(t.order.paid_cents)}</strong>
                <p>历史退款 {money(t.order.refunded_cents)}</p>
              </div>
            </div>
            <h3 className="section-title">客户诉求</h3>
            <p className="modal-copy">{t.request}</p>
            <h3 className="section-title">支持当前提案的证据</h3>
            <div className="approval-evidence">
              {t.evidence
                ?.filter((e) => proposal.evidence_ids.includes(e.id))
                .map((e) => (
                  <button
                    key={e.id}
                    onClick={() =>
                      ui.open({
                        title: e.title,
                        body: (
                          <div className="evidence-body">
                            <p>{e.body}</p>
                            <small>
                              来源：{e.source} · {date(e.observed_at)}
                            </small>
                          </div>
                        ),
                      })
                    }
                  >
                    <Glyph icon={FileText} />
                    <strong>{e.title}</strong>
                    <small>{e.verified_at ? "已人工核验" : "尚未核验"}</small>
                  </button>
                ))}
            </div>
            <div className="proposal-box">
              <div className="proposal-head">
                <span>建议原路退款</span>
                <strong>{money(proposal.amount_cents)}</strong>
              </div>
              <p>{proposal.reason}</p>
            </div>
            <div className="approval-bottom">
              <small>
                有效至 {date(proposal.expires_at)}
                。关键事实变化将使当前审批失效。
              </small>
              <div className="header-actions">
                <button
                  className="button"
                  onClick={() =>
                    decide({ ...proposal, order: t.order }, "supplement")
                  }
                >
                  要求补证
                </button>
                <button
                  className="button"
                  onClick={() => decide(proposal, "reject")}
                >
                  拒绝
                </button>
                <button
                  className="button primary"
                  disabled={proposal.created_by === profile.user.id}
                  title={
                    proposal.created_by === profile.user.id
                      ? "提交人不能自批资金提案"
                      : undefined
                  }
                  onClick={() =>
                    decide({ ...proposal, order: t.order }, "approve")
                  }
                >
                  <Glyph icon={Check} />
                  批准并转执行
                </button>
              </div>
            </div>
            <Link
              className="back-link"
              style={{ marginTop: 18, marginBottom: 0 }}
              to={"/tickets/" + t.id}
            >
              查看完整工单
              <Arrow />
            </Link>
          </>
        )}
      </QueryState>
    </section>
  );
}
function ApprovalInbox() {
  const [page, setPage] = useState(1);
  const query = useData<Page<Proposal>>("/approvals?page=" + page);
  const [selected, setSelected] = useState("");
  const chosen =
    query.data?.items.find((p) => p.id === selected) || query.data?.items[0];
  return (
    <QueryState query={query}>
      {query.data?.items.length ? (
        <div className="approval-layout">
          <section className="card approval-queue">
            <div className="queue-heading">
              <h2>待审批工单</h2>
              <span className="subtle">{query.data.total} 笔</span>
            </div>
            {query.data.items.map((p) => (
              <button
                className={
                  "queue-item " + (chosen?.id === p.id ? "selected" : "")
                }
                key={p.id}
                onClick={() => setSelected(p.id)}
              >
                <div className="queue-item-top">
                  <span>{p.ticket?.number.slice(-6)}</span>
                  <span>{date(p.created_at)}</span>
                </div>
                <h3>{p.ticket?.title}</h3>
                <div className="queue-item-bottom">
                  <span>{p.order?.customer || "售后退款"}</span>
                  <strong>{money(p.amount_cents)}</strong>
                </div>
              </button>
            ))}
            <Pagination page={page} total={query.data.total} onPage={setPage} />
          </section>
          {chosen && <ApprovalDetail key={chosen.id} proposal={chosen} />}
        </div>
      ) : (
        <section className="card">
          <Empty
            title="待审批事项已处理完毕"
            description="新的提案提交后会显示在这里。"
          />
        </section>
      )}
    </QueryState>
  );
}
function ExecutionInbox() {
  const [page, setPage] = useState(1);
  const [all, setAll] = useState(false);
  const query = useData<Page<Action>>(
    `/executions?page=${page}&state=${all ? "all" : "open"}`,
  );
  return (
    <section className="card">
      <div className="card-head">
        <h2>执行与核验任务</h2>
        <p>外部操作、结果登记与核验分别记录</p>
        <label className="check-label">
          <input
            type="checkbox"
            checked={all}
            onChange={(e) => {
              setAll(e.target.checked);
              setPage(1);
            }}
          />
          显示已完成和已取消任务
        </label>
      </div>
      <QueryState query={query}>
        {query.data?.items.length ? (
          <div className="table-wrap business-table">
            <table>
              <thead>
                <tr>
                  <th>关联工单</th>
                  <th>批准金额</th>
                  <th>当前状态</th>
                  <th>外部流水</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {query.data.items.map((a) => (
                  <tr key={a.id}>
                    <td>
                      <strong className="issue-title">{a.ticket?.title}</strong>
                      <span className="issue-id">{a.order?.external_id}</span>
                    </td>
                    <td>{money(a.amount_cents)}</td>
                    <td>
                      <Badge state={a.state} />
                    </td>
                    <td>{a.external_ref || "尚未登记"}</td>
                    <td>
                      <Link className="button" to={"/tickets/" + a.ticket_id}>
                        查看并处理
                        <Arrow />
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty
            title="暂无执行任务"
            description="提案批准后，会生成独立的执行任务。"
          />
        )}
      </QueryState>
      {query.data && (
        <Pagination page={page} total={query.data.total} onPage={setPage} />
      )}
    </section>
  );
}
export function ApprovalsPage() {
  const { profile } = useAuth();
  const canApprove = profile.permissions.includes("approvals.decide");
  const canExecute =
    profile.permissions.includes("actions.execute") ||
    profile.permissions.includes("actions.verify");
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") || (canApprove ? "approvals" : "executions");
  return (
    <>
      <Header
        title="审批与执行"
        description="每一次确认，都有清晰的事实与依据。"
        eyebrow="REVIEW WITH CONFIDENCE"
      />
      <div className="list-toolbar outside">
        <div className="tabs">
          {canApprove && (
            <button
              className={tab === "approvals" ? "selected" : ""}
              onClick={() => setParams({ tab: "approvals" })}
            >
              待我审批
            </button>
          )}
          {canExecute && (
            <button
              className={tab === "executions" ? "selected" : ""}
              onClick={() => setParams({ tab: "executions" })}
            >
              执行与核验
            </button>
          )}
        </div>
        <span className="subtle">资金提案禁止自提自批</span>
      </div>
      {tab === "approvals" && canApprove ? (
        <ApprovalInbox />
      ) : canExecute ? (
        <ExecutionInbox />
      ) : (
        <Empty title="当前角色没有审批或执行权限" />
      )}
    </>
  );
}
export function PolicyPage() {
  const query = useData<Policy[]>("/policies");
  const { profile } = useAuth();
  const ui = useUI();
  const can = profile.permissions.includes("policies.manage");
  const create = () =>
    ui.open({
      title: "新建政策版本",
      description:
        "政策正文提供解释，结构化授权规则决定系统允许的资金处置。发布后保留不可编辑的版本。",
      fields: [
        { name: "title", label: "政策名称", required: true },
        { name: "body", label: "政策正文", type: "textarea", required: true },
        {
          name: "channel",
          label: "适用渠道",
          required: true,
          help: "填写 * 代表所有商家渠道",
        },
        {
          name: "effective_from",
          label: "适用订单起始时间",
          type: "datetime-local",
          required: true,
        },
        {
          name: "allow_refund_on_loss",
          label: "允许在确认丢件后提出退款",
          type: "checkbox",
        },
        { name: "max_refund", label: "单次退款上限（元）", required: true },
      ],
      initial: {
        channel: "*",
        effective_from: localNow(),
        max_refund: "1000.00",
      },
      onSubmit: async (fd) => {
        await post("/policies", {
          title: fd.get("title"),
          body: fd.get("body"),
          channel: fd.get("channel"),
          effective_from: new Date(
            String(fd.get("effective_from")),
          ).toISOString(),
          allow_refund_on_loss: fd.get("allow_refund_on_loss") === "true",
          max_refund_cents: cents(fd.get("max_refund")),
        });
        ui.toast("政策草稿已保存，发布后才能用于提案。");
      },
    });
  return (
    <>
      <Header
        title="知识与政策"
        description="内部 RAG 问答与调查共用已发布政策，回答附带版本与原文片段。"
      >
        {can && (
          <button className="button primary" onClick={create}>
            <Glyph icon={Plus} />
            新建政策
          </button>
        )}
      </Header>
      <KnowledgeQuestions />
      <QueryState query={query}>
        {query.data?.length ? (
          <div className="policy-grid">
            {query.data.map((p) => (
              <section
                className="card policy-card"
                id={"policy-" + p.id}
                key={p.id}
              >
                <div className="policy-heading">
                  <span className="file-icon">
                    <Glyph icon={BookOpen} />
                  </span>
                  <div>
                    <h2>{p.title}</h2>
                    <small>
                      V{p.version} ·{" "}
                      {p.channel === "*" ? "所有渠道" : p.channel}
                    </small>
                  </div>
                  <Badge state={p.state} />
                </div>
                <p className="policy-content">{p.body}</p>
                <div className="policy-meta">
                  <span>适用订单：{date(p.effective_from)} 起</span>
                  <span>退款上限 {money(p.max_refund_cents)}</span>
                </div>
                <div className="policy-footer">
                  <span
                    className={
                      "badge " + (p.allow_refund_on_loss ? "green" : "gray")
                    }
                  >
                    {p.allow_refund_on_loss
                      ? "允许丢件退款提案"
                      : "未授权丢件退款"}
                  </span>
                  {can && p.state !== "retired" && (
                    <button
                      className="button"
                      onClick={() =>
                        ui.open({
                          title:
                            p.state === "draft"
                              ? "发布政策版本"
                              : "停用政策版本",
                          description:
                            p.state === "draft"
                              ? "确认正文、适用时间与结构化授权规则后发布。"
                              : "停用后，引用此版本的待审批和未执行动作会在校验时被拦截。",
                          submitLabel:
                            p.state === "draft" ? "确认发布" : "确认停用",
                          onSubmit: async () => {
                            await post(
                              "/policies/" +
                                p.id +
                                (p.state === "draft" ? "/publish" : "/retire"),
                            );
                            ui.toast("政策状态已更新。");
                          },
                        })
                      }
                    >
                      {p.state === "draft" ? "发布版本" : "停用版本"}
                    </button>
                  )}
                </div>
              </section>
            ))}
          </div>
        ) : (
          <section className="card">
            <Empty
              title="先建立商家的售后规则"
              description="发布政策版本后，系统才能校验处置提案。"
            />
          </section>
        )}
      </QueryState>
    </>
  );
}
const auditLabels: Record<string, string> = {
  "ticket.created": "创建工单",
  "ticket.feishu_created": "飞书表格自动建单",
  "proposal.submitted": "提交处置提案",
  "proposal.approve": "批准提案",
  "proposal.reject": "拒绝提案",
  "proposal.supplement": "要求补证",
  "action.claimed": "领取执行任务",
  "action.receipt_recorded": "登记执行结果",
  "action.verified": "确认业务核验",
  "action.reconciliation_required": "转入对账",
  "ticket.closed": "关闭工单",
  "evidence.verified": "核验证据",
  "order.snapshot_verified": "核查订单快照",
  "auth.demo_login": "切换演示角色",
  "auth.login": "账户登录",
  "agent.started": "启动智能调查",
  "import.confirmed": "确认订单导入",
  "policy.published": "发布政策版本",
};
export function AuditPage() {
  const { profile } = useAuth();
  const [page, setPage] = useState(1);
  const query = useData<Page<Audit>>("/audit?page=" + page);
  const members = useData<Member[]>("/members");
  const names = Object.fromEntries(
    (members.data || []).map((m) => [m.id, m.name]),
  );
  if (!profile.permissions.includes("audit.read"))
    return <Empty title="需要审计查看权限" />;
  return (
    <>
      <Header
        title="审计记录"
        description="谁在什么时候，基于什么记录做了什么。"
      />
      <section className="card">
        <QueryState query={query}>
          {query.data?.items.length ? (
            <>
              <div className="table-wrap business-table">
                <table>
                  <thead>
                    <tr>
                      <th>时间</th>
                      <th>操作人</th>
                      <th>事件</th>
                      <th>关联记录</th>
                    </tr>
                  </thead>
                  <tbody>
                    {query.data.items.map((e) => (
                      <tr key={e.id}>
                        <td>{date(e.created_at)}</td>
                        <td>{names[e.actor_id] || "工作空间成员"}</td>
                        <td>
                          <span className="audit-event-dot" />
                          {auditLabels[e.event] || e.event}
                        </td>
                        <td>
                          <code>{e.resource_id.slice(0, 12)}</code>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="table-foot">
                <span>
                  共 {query.data.total} 条 · 第 {page} 页
                </span>
                <div className="pagination">
                  <button
                    className="button"
                    disabled={page <= 1}
                    onClick={() => setPage((v) => v - 1)}
                  >
                    上一页
                  </button>
                  <button
                    className="button"
                    disabled={page * 50 >= query.data.total}
                    onClick={() => setPage((v) => v + 1)}
                  >
                    下一页
                  </button>
                </div>
              </div>
            </>
          ) : (
            <Empty title="暂无审计事件" />
          )}
        </QueryState>
      </section>
    </>
  );
}
export function AnalyticsPage() {
  const query = useData<Overview>("/overview");
  const runs = useData<Run[]>("/runs");
  const { profile } = useAuth();
  return (
    <>
      <Header
        title="业务分析"
        description="使用真实记录观察工作量、处理状态与模型资源消耗。"
      />
      <QueryState query={query}>
        {query.data && (
          <>
            <div className="stats">
              {[
                ["当前未关闭", query.data.open + " 笔"],
                ["今日闭环", query.data.closed_today + " 笔"],
                ["待审批", query.data.waiting_approval + " 笔"],
                [
                  "模型费用",
                  profile.permissions.includes("members.manage")
                    ? "$" + ((query.data.cost_microusd || 0) / 1e6).toFixed(4)
                    : "管理员可见",
                ],
              ].map(([l, v]) => (
                <article className="stat" key={l}>
                  <div className="stat-name">{l}</div>
                  <div className="stat-value medium">{v}</div>
                  <div className="stat-foot">按当前工作空间记录统计</div>
                </article>
              ))}
            </div>
            <section className="card">
              <div className="card-head">
                <h2>工单阶段分布</h2>
                <span className="subtle">实际业务状态</span>
              </div>
              <div className="state-distribution">
                {Object.entries(query.data.states).map(([state, count]) => (
                  <div key={state}>
                    <Badge state={state} />
                    <div className="distribution-track">
                      <span
                        style={{
                          width:
                            Math.max(
                              3,
                              (count /
                                Math.max(
                                  1,
                                  ...Object.values(query.data!.states),
                                )) *
                                100,
                            ) + "%",
                        }}
                      />
                    </div>
                    <strong>{count}</strong>
                  </div>
                ))}
              </div>
            </section>
            <EvaluationSummary />
            <section className="card section-gap">
              <div className="card-head">
                <h2>模型资源说明</h2>
              </div>
              <div className="request-copy">
                {query.data.model_enabled
                  ? "模型调用按实际输入输出Token与配置价格记录。失败调用、结果未知和保留费用占用分别显示。"
                  : "模型尚未配置，当前没有真实模型效果与调用成本数据。人工工单、审批和核验不受影响。"}
                <br />
                业务收益与准确率只在有基线和真实评估数据后展示。
              </div>
            </section>
          </>
        )}
      </QueryState>
    </>
  );
}
export function RunsPage() {
  const { id } = useParams();
  const ui = useUI();
  const navigate = useNavigate();
  const list = useData<Run[]>("/runs", 5000);
  return (
    <>
      <Header
        title="智能调查记录"
        description="查看任务计划、工具结果、恢复点和人工接管原因。"
      />
      <QueryState query={list}>
        {list.data?.length ? (
          <div className="runs-layout">
            <section className="card">
              <div className="table-wrap business-table">
                <table>
                  <thead>
                    <tr>
                      <th>运行时间</th>
                      <th>状态</th>
                      <th>模型调用</th>
                      <th>操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {list.data.map((r) => (
                      <tr
                        key={r.id}
                        className="linked-row"
                        onClick={(e) => {
                          if (!(e.target as HTMLElement).closest("a,button"))
                            navigate("/runs/" + r.id);
                        }}
                      >
                        <td>
                          {date(r.created_at)}
                          <small className="cell-help">{r.model}</small>
                        </td>
                        <td>
                          <Badge state={r.state} />
                        </td>
                        <td>{r.call_count} 次</td>
                        <td>
                          <Link className="button" to={"/runs/" + r.id}>
                            查看链路
                            <Arrow />
                          </Link>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
            {id && <RunDetail id={id} />}
          </div>
        ) : (
          <section className="card">
            <Empty
              title="还没有智能调查记录"
              description="配置模型后，可从工单详情启动有边界的调查。人工业务流程现在即可使用。"
              action={
                <Link className="button" to="/tickets">
                  查看工单
                  <Arrow />
                </Link>
              }
            />
          </section>
        )}
      </QueryState>
    </>
  );
}
function RunDetail({ id }: { id: string }) {
  const query = useData<Run>("/runs/" + id, 5000);
  const ui = useUI();
  const { profile } = useAuth();
  const result = query.data ? investigationResult(query.data) : null;
  return (
    <section className="card form-card">
      <QueryState query={query}>
        {query.data && (
          <>
            <div className="card-head no-padding">
              <h2>{query.data.ticket_id ? "智能调查结果" : "知识问答结果"}</h2>
              <Badge state={query.data.state} />
            </div>
            {result && (
              <div className="investigation-result">
                <p className="investigation-conclusion">{result.outcome}</p>
                <h3>下一步需要做什么</h3>
                <p>{result.nextStep}</p>
                <h3>查询情况</h3>
                {result.queries.length ? (
                  <ul>
                    {result.queries.map((q) => (
                      <li key={q.title}>
                        <b>{q.title}：</b>
                        {q.detail}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="subtle">尚无已完成的查询记录。</p>
                )}
              </div>
            )}
            {query.data.ticket_id && (
              <Link
                className="text-link"
                to={"/tickets/" + query.data.ticket_id}
              >
                查看关联工单 <Arrow />
              </Link>
            )}
            <details className="run-records" key={id}>
              <summary>
                调用与详细记录{" "}
                <span>
                  {query.data.model} · {query.data.call_count} 次调用 ·{" "}
                  {query.data.events?.length || 0} 条记录
                </span>
              </summary>
              {query.data.error && (
                <div className="notice amber">需要处理：{query.data.error}</div>
              )}
              <div className="run-metrics">
                <span>输入 {query.data.input_tokens} Token</span>
                <span>输出 {query.data.output_tokens} Token</span>
                {profile.permissions.includes("members.manage") && (
                  <span>
                    费用 ${(query.data.cost_microusd / 1e6).toFixed(4)} · 占用 $
                    {(query.data.reserved_microusd / 1e6).toFixed(4)}
                  </span>
                )}
              </div>
              <ReviewRun run={query.data} />
              <div className="run-events">
                {query.data.events?.map((e) => (
                  <div key={e.seq}>
                    <time>{date(e.created_at)}</time>
                    <RunEventSummary kind={e.kind} data={e.data} />
                  </div>
                ))}
              </div>
            </details>
            {["waiting_input", "queued", "running"].includes(
              query.data.state,
            ) &&
              profile.permissions.includes("runs.start") && (
                <div className="header-actions">
                  {query.data.state === "waiting_input" && (
                    <button
                      className="button primary"
                      onClick={() =>
                        ui.open({
                          title: "补证后恢复调查",
                          description:
                            "请先补充并核验材料。恢复会重新读取事实，并沿用该运行的调用与费用预算。",
                          onSubmit: async () => {
                            await post("/runs/" + id + "/resume");
                            ui.toast("调查已排队恢复。");
                          },
                        })
                      }
                    >
                      恢复调查
                    </button>
                  )}
                  <button
                    className="button"
                    onClick={() =>
                      ui.open({
                        title: "取消智能调查",
                        description:
                          "停止后续调查步骤；已完成的业务记录与模型费用仍保留。",
                        onSubmit: async () => {
                          await post("/runs/" + id + "/cancel");
                          ui.toast("调查已取消。");
                        },
                      })
                    }
                  >
                    取消调查
                  </button>
                </div>
              )}
          </>
        )}
      </QueryState>
    </section>
  );
}
interface TenantSettings {
  name: string;
  config: {
    sla_hours?: number;
    approval_hours?: number;
    agent_enabled?: boolean;
  };
  model_enabled: boolean;
  model: string | null;
  platform_connected: boolean;
  environment: string;
}
export function SettingsPage() {
  const { profile, refresh } = useAuth();
  const ui = useUI();
  const query = useData<TenantSettings>("/settings");
  const members = useData<Member[]>("/members");
  const [inviteLink, setInviteLink] = useState("");
  if (!profile.permissions.includes("members.manage"))
    return <Empty title="需要商家管理权限" />;
  const invite = () =>
    ui.open({
      title: "邀请商家成员",
      description:
        "生成一次性邀请链接，由你通过可信渠道交付给对应人员。已有账户须使用原密码加入。",
      fields: [
        { name: "email", label: "成员邮箱", type: "email", required: true },
        {
          name: "role",
          label: "角色",
          type: "select",
          required: true,
          options: [
            { value: "staff", label: "客服处理（含执行登记）" },
            { value: "supervisor", label: "主管审批 / 核验" },
          ],
        },
      ],
      initial: { role: "staff" },
      onSubmit: async (fd) => {
        const result = await post<{ url: string }>(
          "/invitations",
          Object.fromEntries(fd),
        );
        setInviteLink(result.url);
        ui.toast("邀请已创建，链接仅显示在当前页面。");
      },
    });
  return (
    <>
      <Header
        title="商家设置"
        description="维护成员、业务边界和工作空间配置。"
      />
      <QueryState query={query}>
        {query.data && (
          <div className="settings-grid">
            <section className="card form-card">
              <h2>工作空间</h2>
              <div className="settings-facts">
                <div>
                  <span>商家名称</span>
                  <strong>{query.data.name}</strong>
                </div>
                <div>
                  <span>工单处理时限</span>
                  <strong>{query.data.config.sla_hours || 24} 小时</strong>
                </div>
                <div>
                  <span>审批有效期</span>
                  <strong>{query.data.config.approval_hours || 24} 小时</strong>
                </div>
                <div>
                  <span>智能调查</span>
                  <strong>
                    {query.data.config.agent_enabled === false
                      ? "已暂停"
                      : "允许启动"}
                  </strong>
                </div>
              </div>
              <button
                className="button"
                onClick={() =>
                  ui.open({
                    title: "编辑商家配置",
                    fields: [
                      { name: "name", label: "商家名称", required: true },
                      {
                        name: "sla_hours",
                        label: "处理时限（小时）",
                        type: "number",
                        required: true,
                      },
                      {
                        name: "approval_hours",
                        label: "审批有效期（小时）",
                        type: "number",
                        required: true,
                      },
                      {
                        name: "agent_enabled",
                        label: "允许启动智能调查",
                        type: "checkbox",
                      },
                    ],
                    initial: {
                      name: query.data!.name,
                      sla_hours: String(query.data!.config.sla_hours || 24),
                      approval_hours: String(
                        query.data!.config.approval_hours || 24,
                      ),
                      agent_enabled: query.data!.config.agent_enabled !== false,
                    },
                    onSubmit: async (fd) => {
                      await api("/settings", {
                        method: "PUT",
                        body: JSON.stringify({
                          name: fd.get("name"),
                          sla_hours: Number(fd.get("sla_hours")),
                          approval_hours: Number(fd.get("approval_hours")),
                          agent_enabled: fd.get("agent_enabled") === "true",
                        }),
                      });
                      await refresh();
                      ui.toast("配置已保存。");
                    },
                  })
                }
              >
                编辑配置
              </button>
            </section>
            <section className="card form-card">
              <h2>服务接入</h2>
              <div className="integration-row">
                <span className="integration-icon">
                  <Glyph icon={Sparkles} />
                </span>
                <div>
                  <strong>模型调查服务</strong>
                  <small>
                    {query.data.model || "由服务端环境配置模型与密钥"}
                  </small>
                </div>
                <span
                  className={
                    "badge " + (query.data.model_enabled ? "green" : "gray")
                  }
                >
                  {query.data.model_enabled ? "已配置" : "未配置"}
                </span>
              </div>
              <div className="integration-row">
                <span className="integration-icon">
                  <Glyph icon={Box} />
                </span>
                <div>
                  <strong>电商平台</strong>
                  <small>首版使用自主导入与人工外部执行</small>
                </div>
                <span className="badge gray">未接入</span>
              </div>
              <p className="modal-copy">
                密钥仅由部署维护人员在服务端配置，工作台不展示或接收明文密钥。模型未启用时，普通业务流程仍可使用。
              </p>
            </section>
          </div>
        )}
      </QueryState>
      <section className="card section-gap">
        <div className="card-head">
          <div>
            <h2>成员与角色</h2>
            <p>资金提案禁止自提自批，执行人与核验人分离</p>
          </div>
          <button className="button primary" onClick={invite}>
            <Glyph icon={Users} />
            邀请成员
          </button>
        </div>
        {inviteLink && (
          <div className="invite-link">
            <strong>一次性邀请链接（7天有效）</strong>
            <input aria-label="新生成的邀请链接" readOnly value={inviteLink} />
            <button
              className="button"
              onClick={() =>
                navigator.clipboard
                  .writeText(inviteLink)
                  .then(() => ui.toast("邀请链接已复制。"))
              }
            >
              复制链接
            </button>
          </div>
        )}
        <QueryState query={members}>
          {members.data && (
            <div className="table-wrap business-table">
              <table>
                <thead>
                  <tr>
                    <th>成员</th>
                    <th>邮箱</th>
                    <th>角色</th>
                    <th>状态</th>
                    <th>管理</th>
                  </tr>
                </thead>
                <tbody>
                  {members.data.map((m) => (
                    <tr key={m.id}>
                      <td>{m.name}</td>
                      <td>{m.email}</td>
                      <td>
                        {
                          (
                            {
                              staff: "客服处理",
                              supervisor: "主管审批 / 核验",
                              executor: "执行人（历史角色）",
                              auditor: "审计员（历史角色）",
                              admin: "空间所有者（兼容）",
                            } as Record<string, string>
                          )[m.role]
                        }
                      </td>
                      <td>
                        <span
                          className={"badge " + (m.enabled ? "green" : "gray")}
                        >
                          {m.enabled ? "已启用" : "已停用"}
                        </span>
                      </td>
                      <td>
                        <button
                          className="button"
                          onClick={() =>
                            ui.open({
                              title: "管理成员权限",
                              description:
                                "变更后该成员当前商家会话将失效，需要重新登录。最后一位管理员不能被停用或降级。",
                              fields: [
                                {
                                  name: "role",
                                  label: "角色",
                                  type: "select",
                                  required: true,
                                  options: [
                                    { value: "staff", label: "客服处理" },
                                    {
                                      value: "supervisor",
                                      label: "主管审批 / 核验",
                                    },
                                    ...(!["staff", "supervisor"].includes(
                                      m.role,
                                    )
                                      ? [
                                          {
                                            value: m.role,
                                            label: "保留原有角色",
                                          },
                                        ]
                                      : []),
                                  ],
                                },
                                {
                                  name: "enabled",
                                  label: "允许访问此商家空间",
                                  type: "checkbox",
                                },
                              ],
                              initial: { role: m.role, enabled: m.enabled },
                              onSubmit: async (fd) => {
                                await api("/members/" + m.id, {
                                  method: "PUT",
                                  body: JSON.stringify({
                                    role: fd.get("role"),
                                    enabled: fd.get("enabled") === "true",
                                  }),
                                });
                                ui.toast("成员权限已更新。");
                                if (m.id === profile.user.id)
                                  window.location.assign("/login");
                              },
                            })
                          }
                        >
                          管理
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </QueryState>
      </section>
    </>
  );
}
