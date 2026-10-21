let csrf = "";
export function setCsrf(value: string) {
  csrf = value;
}
export class ApiError extends Error {
  code: string;
  requestId?: string;
  constructor(message: string, code: string, requestId?: string) {
    super(message);
    this.code = code;
    this.requestId = requestId;
  }
}
export async function api<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const sessionCsrf = csrf;
  const headers = new Headers(options.headers);
  if (options.body && !(options.body instanceof FormData))
    headers.set("Content-Type", "application/json");
  if (options.method && !["GET", "HEAD"].includes(options.method))
    headers.set("X-CSRF-Token", csrf);
  const response = await fetch("/api/v1" + path, {
    ...options,
    headers,
    credentials: "same-origin",
  });
  const payload = await response
    .json()
    .catch(() => ({ message: "服务未返回有效结果" }));
  if (!response.ok) {
    if (
      sessionCsrf &&
      sessionCsrf === csrf &&
      ["unauthenticated", "account_disabled"].includes(payload.code)
    ) {
      csrf = "";
      window.dispatchEvent(new Event("aftercare:session-expired"));
    }
    throw new ApiError(
      payload.message || "请求未完成",
      payload.code || "request_failed",
      payload.request_id,
    );
  }
  return payload as T;
}
export const post = <T = unknown>(path: string, body: unknown = {}) =>
  api<T>(path, { method: "POST", body: JSON.stringify(body) });
export const money = (cents: number) =>
  new Intl.NumberFormat("zh-CN", { style: "currency", currency: "CNY" }).format(
    cents / 100,
  );
export function cents(value: FormDataEntryValue | null, allowZero = false) {
  const s = String(value || "");
  if (!/^\d+(\.\d{1,2})?$/.test(s))
    throw new Error("金额需要为正数且最多两位小数");
  const [a, b = ""] = s.split(".");
  const n = Number(a) * 100 + Number(b.padEnd(2, "0"));
  if (!Number.isSafeInteger(n) || (allowZero ? n < 0 : n <= 0))
    throw new Error("金额格式不正确");
  return n;
}
export const date = (value: string | null | undefined) =>
  value
    ? new Date(value).toLocaleString("zh-CN", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      })
    : "尚未确认";
export const localNow = () => {
  const d = new Date();
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 16);
};
