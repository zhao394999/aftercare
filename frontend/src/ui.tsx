import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
  type ComponentType,
} from "react";
import {
  AlertCircle,
  Check,
  ChevronRight,
  Loader2,
  Search,
  X,
  type LucideProps,
} from "lucide-react";
import { api } from "./api";
import type { Order, Page, Profile } from "./types";

export const Auth = createContext<{
  profile: Profile;
  refresh: () => Promise<void>;
}>({} as never);
export const useAuth = () => useContext(Auth);
export const Changes = createContext({ version: 0, reload: () => {} });
export const Dialogs = createContext({
  open: (_dialog: DialogSpec | null) => {},
  toast: (_text: string) => {},
});
export const useUI = () => useContext(Dialogs);
export type FieldSpec = {
  name: string;
  label: string;
  type?: string;
  required?: boolean;
  options?: { value: string; label: string }[];
  help?: string;
  accept?: string;
};
export type DialogSpec = {
  title: string;
  description?: string;
  body?: ReactNode;
  fields?: FieldSpec[];
  initial?: Record<string, string | boolean>;
  submitLabel?: string;
  onSubmit?: (form: FormData) => Promise<void>;
};
export function Glyph({ icon: I }: { icon: ComponentType<LucideProps> }) {
  return (
    <span className="icon" aria-hidden="true">
      <I size={20} strokeWidth={1.65} />
    </span>
  );
}
export const states: Record<string, [string, string]> = {
  new: ["待调查", "gray"],
  investigating: ["调查中", "blue"],
  waiting_evidence: ["待补证", "gray"],
  draft: ["待修改", "gray"],
  waiting_approval: ["待审批", "amber"],
  waiting_execution: ["待人工执行", "blue"],
  waiting_verification: ["待核验", "green"],
  reconciling: ["对账中", "red"],
  resolved: ["已解决", "green"],
  closed: ["已关闭", "gray"],
  approved: ["已批准", "green"],
  claimed: ["执行已领取", "blue"],
  processing: ["外部处理中", "blue"],
  partially_succeeded: ["已结清部分成功", "amber"],
  succeeded: ["已确认成功", "green"],
  cancelled: ["已取消", "gray"],
  pending: ["待审批", "amber"],
  published: ["已发布", "green"],
  retired: ["已停用", "gray"],
  queued: ["排队中", "blue"],
  running: ["运行中", "blue"],
  completed: ["已完成", "green"],
  waiting_input: ["等待补证", "amber"],
  human_needed: ["需人工接管", "amber"],
  failed: ["失败", "red"],
  preview: ["待确认", "amber"],
  done: ["已完成", "green"],
};
export function Badge({ state }: { state: string }) {
  const [label, tone] = states[state] || [state, "gray"];
  return <span className={"badge " + tone}>{label}</span>;
}
export function Empty({
  title = "暂无记录",
  description = "记录将在完成业务操作后显示。",
  action,
}: {
  title?: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <div className="empty-symbol">
        <Glyph icon={Search} />
      </div>
      <h3>{title}</h3>
      <p>{description}</p>
      {action}
    </div>
  );
}
export function ErrorPanel({
  error,
  retry,
}: {
  error: Error;
  retry: () => void;
}) {
  return (
    <div className="error-panel" role="alert">
      <Glyph icon={AlertCircle} />
      <div>
        <strong>{error.message}</strong>
        <p>操作未完成，已有数据仍被保留。</p>
      </div>
      <button className="button" onClick={retry}>
        重试
      </button>
    </div>
  );
}
export function Loading() {
  return (
    <div className="loading-panel" role="status" aria-label="正在加载">
      <div className="skeleton" />
      <div className="skeleton short" />
      <div className="skeleton" />
    </div>
  );
}
export function useData<T>(path: string, interval = 0) {
  const { version } = useContext(Changes);
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);
  const [local, setLocal] = useState(0);
  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    async function load() {
      try {
        const value = await api<T>(path, { signal: controller.signal });
        if (active) {
          setData(value);
          setError(null);
        }
      } catch (e) {
        if (active && !controller.signal.aborted) setError(e as Error);
      } finally {
        if (active) setLoading(false);
      }
    }
    setLoading(true);
    load();
    const timer = interval ? setInterval(load, interval) : undefined;
    return () => {
      active = false;
      controller.abort();
      if (timer) clearInterval(timer);
    };
  }, [path, version, local, interval]);
  return { data, error, loading, reload: () => setLocal((v) => v + 1) };
}
export function QueryState({
  query,
  children,
}: {
  query: { loading: boolean; error: Error | null; reload: () => void };
  children: ReactNode;
}) {
  if (query.loading) return <Loading />;
  if (query.error)
    return <ErrorPanel error={query.error} retry={query.reload} />;
  return <>{children}</>;
}

export function Pagination({
  page,
  total,
  size = 50,
  onPage,
}: {
  page: number;
  total: number;
  size?: number;
  onPage: (page: number) => void;
}) {
  const pages = Math.max(1, Math.ceil(total / size));
  return (
    <div className="table-foot">
      <span className="subtle">
        共 {total} 条 · 第 {page} / {pages} 页
      </span>
      <nav className="pagination" aria-label="记录分页">
        <button
          type="button"
          className="button"
          disabled={page <= 1}
          onClick={() => onPage(page - 1)}
        >
          上一页
        </button>
        <button
          type="button"
          className="button"
          disabled={page >= pages}
          onClick={() => onPage(page + 1)}
        >
          下一页
        </button>
      </nav>
    </div>
  );
}

export function OrderPicker({
  value,
  onChange,
  selected,
}: {
  value: string;
  onChange: (value: string) => void;
  selected?: Order;
}) {
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const query = useData<Page<Order>>(
    `/orders?q=${encodeURIComponent(search)}&page=${page}&page_size=20`,
  );
  return (
    <>
      <input
        type="search"
        aria-label="搜索关联订单"
        placeholder="输入订单号或商品名搜索全部订单"
        value={search}
        maxLength={100}
        onChange={(e) => {
          setSearch(e.target.value);
          setPage(1);
          onChange("");
        }}
      />
      {query.error && (
        <p className="form-error" role="alert">
          {query.error.message}
          <button type="button" className="button" onClick={query.reload}>
            重试
          </button>
        </p>
      )}
      <select
        id="field-order_id"
        required
        value={value}
        disabled={query.loading || !!query.error}
        onChange={(e) => onChange(e.target.value)}
      >
        <option value="">
          {query.loading ? "正在查询订单…" : "请选择关联订单"}
        </option>
        {selected?.id === value &&
          !query.data?.items.some((o) => o.id === value) && (
            <option value={selected.id}>
              {selected.external_id} · {selected.product}
            </option>
          )}
        {query.data?.items.map((o) => (
          <option key={o.id} value={o.id}>
            {o.external_id} · {o.product}
          </option>
        ))}
      </select>
      {!query.loading && !query.error && !query.data?.items.length && (
        <p role="status">暂无匹配订单，请调整搜索条件或先导入订单。</p>
      )}
      {query.data && (
        <Pagination
          page={page}
          total={query.data.total}
          size={20}
          onPage={(p) => {
            setPage(p);
            onChange("");
          }}
        />
      )}
    </>
  );
}
export function FormDialog({
  spec,
  onClose,
  onDone,
}: {
  spec: DialogSpec;
  onClose: () => void;
  onDone: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [values, setValues] = useState<Record<string, string | boolean>>(
    spec.initial || {},
  );
  const [files, setFiles] = useState<Record<string, File>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    dialog.current?.showModal();
    return () => dialog.current?.close();
  }, []);
  return (
    <dialog
      ref={dialog}
      className="app-dialog"
      onCancel={(e) => {
        if (busy) e.preventDefault();
        else onClose();
      }}
      aria-labelledby="dialog-title"
    >
      <div className="modal-header">
        <h2 id="dialog-title">{spec.title}</h2>
        <button
          className="icon-button"
          onClick={onClose}
          disabled={busy}
          aria-label="关闭弹窗"
        >
          <Glyph icon={X} />
        </button>
      </div>
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          if (!spec.onSubmit) {
            onClose();
            return;
          }
          setBusy(true);
          setError("");
          const fd = new FormData();
          for (const field of spec.fields || []) {
            if (field.type === "file") {
              if (files[field.name]) fd.set(field.name, files[field.name]);
            } else fd.set(field.name, String(values[field.name] ?? ""));
          }
          try {
            await spec.onSubmit(fd);
            onDone();
            onClose();
          } catch (e) {
            setError((e as Error).message);
          } finally {
            setBusy(false);
          }
        }}
      >
        {spec.description && <p className="modal-copy">{spec.description}</p>}
        {spec.body}
        {spec.fields?.map((field) => (
          <div className="field" key={field.name}>
            {field.type === "checkbox" ? (
              <label className="check-label">
                <input
                  type="checkbox"
                  required={field.required}
                  checked={values[field.name] === true}
                  onChange={(e) =>
                    setValues((v) => ({ ...v, [field.name]: e.target.checked }))
                  }
                />
                {field.label}
              </label>
            ) : (
              <>
                <label htmlFor={"field-" + field.name}>
                  {field.label}
                  {field.required && <span className="required"> *</span>}
                </label>
                {field.type === "order" ? (
                  <OrderPicker
                    value={String(values[field.name] ?? "")}
                    onChange={(value) =>
                      setValues((v) => ({ ...v, [field.name]: value }))
                    }
                  />
                ) : field.type === "select" ? (
                  <select
                    id={"field-" + field.name}
                    required={field.required}
                    value={String(values[field.name] ?? "")}
                    onChange={(e) =>
                      setValues((v) => ({ ...v, [field.name]: e.target.value }))
                    }
                  >
                    <option value="">请选择</option>
                    {field.options?.map((o) => (
                      <option key={o.value} value={o.value}>
                        {o.label}
                      </option>
                    ))}
                  </select>
                ) : field.type === "textarea" ? (
                  <textarea
                    id={"field-" + field.name}
                    required={field.required}
                    value={String(values[field.name] ?? "")}
                    onChange={(e) =>
                      setValues((v) => ({ ...v, [field.name]: e.target.value }))
                    }
                  />
                ) : field.type === "file" ? (
                  <input
                    id={"field-" + field.name}
                    type="file"
                    required={field.required}
                    accept={field.accept}
                    onChange={(e) => {
                      if (e.target.files?.[0])
                        setFiles((v) => ({
                          ...v,
                          [field.name]: e.target.files![0],
                        }));
                    }}
                  />
                ) : (
                  <input
                    id={"field-" + field.name}
                    type={field.type || "text"}
                    required={field.required}
                    value={String(values[field.name] ?? "")}
                    onChange={(e) =>
                      setValues((v) => ({ ...v, [field.name]: e.target.value }))
                    }
                    autoComplete={
                      field.type === "password" ? "current-password" : undefined
                    }
                  />
                )}
              </>
            )}
            {field.help && <small>{field.help}</small>}
          </div>
        ))}
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
        <div className="modal-actions">
          <button
            type="button"
            className="button"
            onClick={onClose}
            disabled={busy}
          >
            {spec.onSubmit ? "取消" : "关闭"}
          </button>
          {spec.onSubmit && (
            <button type="submit" className="button primary" disabled={busy}>
              {busy ? (
                <>
                  <Glyph icon={Loader2} />
                  处理中
                </>
              ) : (
                spec.submitLabel || "确认提交"
              )}
            </button>
          )}
        </div>
      </form>
    </dialog>
  );
}
export function Arrow() {
  return <Glyph icon={ChevronRight} />;
}
