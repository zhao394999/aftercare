import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { api, ApiError, post, setCsrf } from "../src/api.ts";

afterEach(() => {
  setCsrf("");
  delete globalThis.window;
});

for (const [status, code, loggedIn, expires] of [
  [401, "unauthenticated", true, true],
  [403, "account_disabled", true, true],
  [403, "forbidden", true, false],
  [401, "invalid_credentials", false, false],
  [401, "unauthenticated", false, false],
]) {
  test(`${code}, loggedIn=${loggedIn}: session reset=${expires}`, async (t) => {
    const browser = new EventTarget();
    globalThis.window = browser;
    let resets = 0;
    browser.addEventListener("aftercare:session-expired", () => resets++);
    setCsrf(loggedIn ? "current-session" : "");
    t.mock.method(globalThis, "fetch", async () =>
      Response.json({ code, message: "request rejected" }, { status }),
    );
    await assert.rejects(
      api("/overview"),
      (error) => error instanceof ApiError && error.code === code,
    );
    assert.equal(resets, Number(expires));
  });
}

test("an expired request does not clear a newer login", async (t) => {
  const browser = new EventTarget();
  globalThis.window = browser;
  let resets = 0;
  browser.addEventListener("aftercare:session-expired", () => resets++);
  setCsrf("old-session");
  let respond;
  t.mock.method(
    globalThis,
    "fetch",
    () =>
      new Promise((resolve) => {
        respond = resolve;
      }),
  );
  const pending = api("/overview");
  setCsrf("new-session");
  respond(Response.json({ code: "unauthenticated" }, { status: 401 }));
  await assert.rejects(pending, ApiError);
  assert.equal(resets, 0);
});

test("network failure keeps the session and remains an error", async (t) => {
  const browser = new EventTarget();
  globalThis.window = browser;
  let resets = 0;
  browser.addEventListener("aftercare:session-expired", () => resets++);
  setCsrf("current-session");
  t.mock.method(globalThis, "fetch", async () => {
    throw new TypeError("network unavailable");
  });
  await assert.rejects(api("/overview"), /network unavailable/);
  assert.equal(resets, 0);
  t.mock.method(globalThis, "fetch", async (_url, options) => {
    assert.equal(options.headers.get("X-CSRF-Token"), "current-session");
    return Response.json({ ok: true });
  });
  assert.deepEqual(await post("/auth/logout"), { ok: true });
});

test("expired sessions clear the token and notify only once", async (t) => {
  const browser = new EventTarget();
  globalThis.window = browser;
  let resets = 0;
  browser.addEventListener("aftercare:session-expired", () => resets++);
  setCsrf("current-session");
  t.mock.method(globalThis, "fetch", async () =>
    Response.json({ code: "unauthenticated" }, { status: 401 }),
  );
  await assert.rejects(api("/overview"), ApiError);
  await assert.rejects(api("/tickets"), ApiError);
  assert.equal(resets, 1);
  t.mock.method(globalThis, "fetch", async (_url, options) => {
    assert.equal(options.headers.get("X-CSRF-Token"), "");
    return Response.json({ ok: true });
  });
  await post("/auth/login");
});
