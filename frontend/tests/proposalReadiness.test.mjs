import assert from "node:assert/strict";
import { test } from "node:test";
import { proposalReadiness } from "../src/proposalReadiness.ts";

const now = Date.parse("2026-10-20T08:00:00Z");
const policy = {
  state: "published",
  channel: "shop",
  allow_refund_on_loss: true,
  max_refund_cents: 5000,
  effective_from: "2026-01-01T00:00:00Z",
  effective_until: null,
};
const evidence = (kind, extra = {}) => ({
  kind,
  verified_at: "2026-10-20T07:00:00Z",
  expires_at: null,
  ...extra,
});
const ticket = {
  order: {
    channel: "shop",
    placed_at: "2026-10-19T00:00:00Z",
    verified_at: "2026-10-20T07:00:00Z",
    status: "shipped",
    paid_cents: 5000,
    refunded_cents: 0,
    reserved_cents: 0,
  },
  evidence: [evidence("carrier_confirmation"), evidence("customer_request")],
};

test("ready proposals keep only matching, authorized and published policy versions", () => {
  const result = proposalReadiness(
    ticket,
    [
      policy,
      ...[
        { state: "draft" },
        { channel: "other" },
        { allow_refund_on_loss: false },
        { max_refund_cents: 0 },
        { effective_from: "2026-10-20T00:00:00Z" },
        { effective_until: ticket.order.placed_at },
      ].map((p) => ({ ...policy, ...p })),
    ],
    now,
  );
  assert.deepEqual(result.policies, [policy]);
  assert.deepEqual(result.missing, []);
});

test("missing, pending verification and expired evidence have different reachable actions", () => {
  const missing = proposalReadiness({ ...ticket, evidence: [] }, [policy], now);
  assert.deepEqual(
    missing.missing.map((m) => m.key),
    ["carrier_confirmation", "customer_request"],
  );
  const pending = proposalReadiness(
    {
      ...ticket,
      evidence: [
        evidence("carrier_confirmation", { verified_at: null }),
        evidence("customer_request", {
          expires_at: new Date(now).toISOString(),
        }),
      ],
    },
    [policy],
    now,
  );
  assert.deepEqual(
    pending.missing.map((m) => m.key),
    ["verify-carrier_confirmation", "customer_request"],
  );
  assert.match(pending.missing[1].description, /过期/);
  assert.deepEqual(pending.loss, []);
  assert.deepEqual(pending.customer, []);
});

test("stale snapshots, order state, reserved funds and unavailable policies stay blocking", () => {
  const result = proposalReadiness(
    {
      ...ticket,
      order: {
        ...ticket.order,
        verified_at: new Date(now - 86400000).toISOString(),
        status: "delivered",
        reserved_cents: 5000,
      },
    },
    [{ ...policy, channel: "other" }],
    now,
  );
  assert.deepEqual(
    result.missing.map((m) => m.key),
    ["snapshot", "status", "balance", "policy"],
  );
});
