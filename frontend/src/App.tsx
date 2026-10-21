import { useEffect, useState } from "react";
import {
  Link,
  NavLink,
  Navigate,
  Outlet,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from "react-router-dom";
import {
  ArrowUpRight,
  BookOpen,
  Box,
  ChartColumn,
  Check,
  ChevronDown,
  FileClock,
  Grid2X2,
  Leaf,
  LogOut,
  Menu,
  Search,
  Settings,
  ShieldCheck,
  Sparkles,
  TicketCheck,
  Workflow,
} from "lucide-react";
import { api, post, setCsrf } from "./api";
import type { Profile } from "./types";
import {
  Auth,
  Changes,
  Dialogs,
  FormDialog,
  Glyph,
  Loading,
  type DialogSpec,
  useAuth,
} from "./ui";
import {
  OverviewPage,
  OrdersPage,
  TicketsPage,
  ImportPage,
  AnalyticsPage,
  AuditPage,
  RunsPage,
  PolicyPage,
  SettingsPage,
  ApprovalsPage,
} from "./pages";
import TicketPage from "./TicketPage";
import { InvitationPage } from "./management";
import { BatchTicketPage, OrderIntakePage } from "./OrderIntake";

const roleNames: Record<string, string> = {
  staff: "客服处理",
  supervisor: "主管审批",
  executor: "执行人（历史角色）",
  auditor: "审计员（历史角色）",
  admin: "主管（空间所有者）",
};
export function Brand() {
  return (
    <span className="brand">
      <span className="brand-mark">
        <svg viewBox="0 0 32 32" aria-hidden="true">
          <path d="M8 12a9 9 0 1 1 0 10M8 12v7h7" />
          <path d="m13 15 4 4 7-8" />
        </svg>
      </span>
      <span>
        <strong>
          循单<span className="brand-en">Aftercare</span>
        </strong>
        <small>让每一笔售后，有据可循</small>
      </span>
    </span>
  );
}
function Login({
  onLogin,
  demoMode,
  error: systemError,
}: {
  onLogin: (p: Profile) => void;
  demoMode: boolean;
  error: string;
}) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [registering, setRegistering] = useState(false);
  const [name, setName] = useState("");
  const [workspace, setWorkspace] = useState("");
  const navigate = useNavigate();
  const submit = async (role?: string) => {
    setBusy(true);
    setError("");
    try {
      const profile = await post<Profile>(
        role ? "/auth/demo" : registering ? "/auth/register" : "/auth/login",
        role
          ? { role }
          : registering
            ? { email, password, name, workspace }
            : { email, password },
      );
      setCsrf(profile.csrf);
      onLogin(profile);
      navigate("/overview", { replace: true });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="login-page">
      <section className="login-story">
        <Link to="/login">
          <Brand />
        </Link>
        <div className="login-copy">
          <span className="eyebrow">AFTERCARE, WITH CLARITY</span>
          <h1>
            每一笔售后，
            <br />
            都有据可循。
          </h1>
          <p>
            把分散的订单、证据和决策，
            <br />
            连成清晰、可靠的处理流程。
          </p>
          <div className="login-capabilities">
            {[
              [Search, "事实先行", "有来源的调查，有依据的方案"],
              [ShieldCheck, "关键决策由人确认", "审批、执行、核验各有边界"],
              [Workflow, "全过程留痕", "从客户诉求，到最终业务结果"],
            ].map(([I, title, copy]) => (
              <div key={title as string}>
                <Glyph icon={I as typeof Search} />
                <span>
                  <strong>{title as string}</strong>
                  <small>{copy as string}</small>
                </span>
              </div>
            ))}
          </div>
        </div>
        <div className="login-story-footer">
          清晰的流程，让团队专注于更好的服务。
        </div>
      </section>
      <section className="login-form-side">
        <div className="login-box">
          <span className="login-small-logo">循单 · 商家工作台</span>
          <h2>{registering ? "创建商家空间" : "欢迎回来"}</h2>
          <p>
            {registering
              ? "注册主管账户，再邀请客服加入团队。"
              : "登录你的商家空间，继续处理售后。"}
          </p>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              submit();
            }}
          >
            {registering && (
              <>
                <div className="field">
                  <label htmlFor="name">你的姓名</label>
                  <input
                    id="name"
                    required
                    maxLength={60}
                    autoComplete="name"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                  />
                </div>
                <div className="field">
                  <label htmlFor="workspace">商家名称</label>
                  <input
                    id="workspace"
                    required
                    minLength={2}
                    maxLength={100}
                    autoComplete="organization"
                    value={workspace}
                    onChange={(e) => setWorkspace(e.target.value)}
                  />
                </div>
              </>
            )}
            <div className="field">
              <label htmlFor="email">账户邮箱</label>
              <input
                id="email"
                type="email"
                required
                autoComplete="username"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="你的商家账户邮箱"
              />
            </div>
            <div className="field">
              <label htmlFor="password">密码</label>
              <input
                id="password"
                type="password"
                required
                autoComplete={registering ? "new-password" : "current-password"}
                minLength={registering ? 12 : undefined}
                maxLength={128}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder={registering ? "设置至少12位密码" : "输入账户密码"}
              />
            </div>
            {(error || systemError) && (
              <p className="form-error" role="alert">
                {error || systemError}
              </p>
            )}
            <button
              className="button primary wide login-submit"
              disabled={busy}
            >
              {busy
                ? "正在提交…"
                : registering
                  ? "注册并进入工作台"
                  : "登录工作台"}
              <Glyph icon={ArrowUpRight} />
            </button>
          </form>
          <button
            type="button"
            className="text-link auth-switch"
            disabled={busy}
            onClick={() => {
              setRegistering(!registering);
              setError("");
            }}
          >
            {registering ? "已有账户？返回登录" : "还没有账户？注册商家空间"}
          </button>
          {demoMode && (
            <div className="demo-entry">
              <span>体验隔离的演示空间</span>
              <p>使用示例数据查看真实流程，请勿导入客户资料。</p>
              <button
                className="button wide"
                disabled={busy}
                onClick={() => submit("supervisor")}
              >
                以主管身份体验
                <Glyph icon={ArrowUpRight} />
              </button>
            </div>
          )}
          <small className="login-help">
            加入已有团队请向主管索取邀请链接；注册会创建独立的新空间。
          </small>
        </div>
        <div className="login-copyright">循单 Aftercare · 售后协作工作台</div>
      </section>
    </div>
  );
}
function Layout({
  onLogout,
  onRole,
}: {
  onLogout: () => void;
  onRole: (role: string) => Promise<void>;
}) {
  const { profile } = useAuth();
  const can = (p: string) => profile.permissions.includes(p);
  const location = useLocation();
  const navigate = useNavigate();
  const [menu, setMenu] = useState(false);
  const [search, setSearch] = useState("");
  const links = [
    ["/overview", "工作概览", Grid2X2],
    ["/orders", "订单档案", Box],
    ["/tickets", "售后工单", TicketCheck],
    ...(can("approvals.decide") ||
    can("actions.execute") ||
    can("actions.verify")
      ? [["/approvals", "审批与执行", ShieldCheck]]
      : []),
    ["/policies", "知识与政策", BookOpen],
    ["/analytics", "业务分析", ChartColumn],
    ["/runs", "智能调查记录", Sparkles],
    ...(can("audit.read") ? [["/audit", "审计记录", FileClock]] : []),
  ] as const;
  const current = links.find(([path]) =>
    location.pathname.startsWith(path as string),
  );
  useEffect(() => {
    setMenu(false);
    const main = document.getElementById("main");
    main?.focus({ preventScroll: true });
    window.scrollTo({ top: 0, behavior: "instant" });
    document.title = (current?.[1] || "商家设置") + " · 循单 Aftercare";
  }, [location.pathname]);
  return (
    <>
      <a
        className="skip"
        href="#main"
        onClick={(e) => {
          e.preventDefault();
          document.getElementById("main")?.focus();
        }}
      >
        跳到主要内容
      </a>
      <aside
        id="primary-sidebar"
        className={"sidebar " + (menu ? "open" : "")}
        aria-label="主导航"
      >
        <Link to="/overview">
          <Brand />
        </Link>
        <div className="workspace">
          <span className="store-mark">{profile.tenant.name.slice(0, 1)}</span>
          <span>
            <strong>{profile.tenant.name}</strong>
            <small>
              {profile.tenant.is_demo ? "隔离演示空间" : "商家工作空间"}
            </small>
          </span>
        </div>
        <span className="nav-caption">工作空间</span>
        <nav>
          {links.map(([path, label, I]) => (
            <NavLink
              to={path as string}
              key={path as string}
              onClick={() => setMenu(false)}
            >
              <Glyph icon={I as typeof Box} />
              {label as string}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="sidebar-note">
            <Glyph icon={Leaf} />
            <strong>独立商家工作台</strong>
            <p>
              人工执行，凭证核验。
              <br />
              每个动作，都有明确责任。
            </p>
          </div>
          {can("members.manage") && (
            <NavLink className="settings-link" to="/settings">
              <Glyph icon={Settings} />
              商家设置
            </NavLink>
          )}
          <div className="sidebar-foot">
            <span className="tiny-dot" />
            Aftercare<span>V0.1</span>
          </div>
        </div>
      </aside>
      <div className="app-shell">
        <header className="topbar">
          <button
            className="icon-button menu-toggle"
            aria-label={menu ? "收起导航" : "打开导航"}
            aria-expanded={menu}
            aria-controls="primary-sidebar"
            onClick={() => setMenu((v) => !v)}
          >
            <Glyph icon={Menu} />
          </button>
          <div className="breadcrumb">
            工作空间<span>/</span>
            <strong>{(current?.[1] as string) || "商家设置"}</strong>
          </div>
          <div className="topbar-right">
            <form
              className="global-search"
              onSubmit={(e) => {
                e.preventDefault();
                navigate("/tickets?q=" + encodeURIComponent(search));
              }}
            >
              <Glyph icon={Search} />
              <input
                aria-label="全局搜索工单"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="搜索工单或订单"
              />
            </form>
            {profile.tenant.is_demo && (
              <span className="sample-label">演示空间 · 示例数据</span>
            )}
            <span className="topbar-divider" />
            <div className="profile">
              <span className="avatar">{profile.user.name.slice(-1)}</span>
              <span>
                {profile.user.name}
                <small>{roleNames[profile.role]}</small>
              </span>
              {profile.tenant.is_demo && (
                <select
                  className="role-select"
                  aria-label="切换演示角色"
                  value={profile.role}
                  onChange={(e) => onRole(e.target.value)}
                >
                  {Object.entries(roleNames)
                    .filter(([key]) =>
                      ["staff", "supervisor", profile.role].includes(key),
                    )
                    .map(([key, name]) => (
                      <option key={key} value={key}>
                        {name}
                      </option>
                    ))}
                </select>
              )}
            </div>
            <button
              className="icon-button"
              aria-label="退出登录"
              onClick={onLogout}
            >
              <Glyph icon={LogOut} />
            </button>
          </div>
        </header>
        <main id="main" tabIndex={-1}>
          <Outlet />
        </main>
        <footer className="page-footer">
          <span>循单 Aftercare · 每一步，都有迹可循</span>
          <span>
            {profile.tenant.is_demo
              ? "隔离示例数据 · 不执行真实资金操作"
              : "独立工作台 · 业务结果以登记与核验为准"}
          </span>
        </footer>
      </div>
    </>
  );
}
export default function App() {
  const location = useLocation();
  const [profile, setProfile] = useState<Profile | null>(null);
  const [loading, setLoading] = useState(true);
  const [system, setSystem] = useState({ demo_mode: false });
  const [systemError, setSystemError] = useState("");
  const [version, setVersion] = useState(0);
  const [dialog, setDialog] = useState<DialogSpec | null>(null);
  const [toast, setToast] = useState("");
  useEffect(() => {
    let active = true;
    const onSessionExpired = () => {
      setCsrf("");
      setProfile(null);
      setDialog(null);
      setToast("会话已失效，请重新登录。");
    };
    window.addEventListener("aftercare:session-expired", onSessionExpired);
    api<typeof system>("/system")
      .then((value) => {
        if (active) setSystem(value);
      })
      .catch(() => {
        if (active)
          setSystemError("暂时无法连接工作台服务，请确认服务已启动。");
      });
    api<Profile>("/auth/me")
      .then((p) => {
        if (active) {
          setCsrf(p.csrf);
          setProfile(p);
        }
      })
      .catch(() => {})
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
      window.removeEventListener("aftercare:session-expired", onSessionExpired);
    };
  }, []);
  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(""), 4000);
    return () => clearTimeout(timer);
  }, [toast]);
  const refresh = async () => {
    const p = await api<Profile>("/auth/me");
    setCsrf(p.csrf);
    setProfile(p);
  };
  if (loading)
    return (
      <div className="startup-loading">
        <Brand />
        <Loading />
      </div>
    );
  return (
    <Changes.Provider
      value={{ version, reload: () => setVersion((v) => v + 1) }}
    >
      <Dialogs.Provider value={{ open: setDialog, toast: setToast }}>
        {location.pathname.startsWith("/invite/") ? (
          <InvitationPage onLogin={setProfile} />
        ) : profile ? (
          <Auth.Provider value={{ profile, refresh }}>
            <Routes>
              <Route
                element={
                  <Layout
                    onLogout={async () => {
                      try {
                        await post("/auth/logout");
                        setProfile(null);
                        setCsrf("");
                        setDialog(null);
                      } catch (e) {
                        setToast((e as Error).message);
                      }
                    }}
                    onRole={async (role) => {
                      try {
                        const p = await post<Profile>("/auth/demo", { role });
                        setCsrf(p.csrf);
                        setProfile(p);
                        setDialog(null);
                        setVersion((v) => v + 1);
                        setToast("已切换到" + roleNames[role] + "视角");
                      } catch (e) {
                        setToast((e as Error).message);
                      }
                    }}
                  />
                }
              >
                <Route path="/overview" element={<OverviewPage />} />
                <Route path="/orders" element={<OrdersPage />} />
                <Route
                  path="/orders/new"
                  element={<OrderIntakePage key="order" />}
                />
                <Route path="/orders/import" element={<ImportPage />} />
                <Route path="/tickets" element={<TicketsPage />} />
                <Route
                  path="/tickets/new"
                  element={<OrderIntakePage key="ticket" />}
                />
                <Route path="/tickets/batch" element={<BatchTicketPage />} />
                <Route path="/tickets/:id" element={<TicketPage />} />
                <Route path="/approvals" element={<ApprovalsPage />} />
                <Route path="/policies" element={<PolicyPage />} />
                <Route path="/analytics" element={<AnalyticsPage />} />
                <Route path="/audit" element={<AuditPage />} />
                <Route path="/runs" element={<RunsPage />} />
                <Route path="/runs/:id" element={<RunsPage />} />
                <Route path="/settings" element={<SettingsPage />} />
                <Route path="*" element={<Navigate to="/overview" replace />} />
              </Route>
            </Routes>
          </Auth.Provider>
        ) : (
          <Login
            onLogin={setProfile}
            demoMode={system.demo_mode}
            error={systemError}
          />
        )}{" "}
        {dialog && (
          <FormDialog
            key={dialog.title}
            spec={dialog}
            onClose={() => setDialog(null)}
            onDone={() => setVersion((v) => v + 1)}
          />
        )}
        <div
          className={"toast " + (toast ? "visible" : "")}
          role="status"
          aria-live="polite"
        >
          {toast}
        </div>
      </Dialogs.Provider>
    </Changes.Provider>
  );
}
