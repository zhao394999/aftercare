import assert from "node:assert/strict";
import { test } from "node:test";
import { investigationResult, runSummary } from "../src/runSummary.ts";

test("a proposal is described as pending approval, never as a completed refund", () => {
  const summary = runSummary("tool.completed", {
    tool: "draft_refund",
    result: { amount_cents: 12900 },
  });
  assert.match(summary.detail, /129.00.*等待主管审批.*尚未执行退款/);
});

test("a cached logistics result does not claim a fresh external call", () => {
  assert.match(
    runSummary("tool.completed", {
      tool: "query_logistics",
      result: { cached: true },
    }).detail,
    /复用近期查询缓存/,
  );
});

test("historical events do not invent per-call usage", () => {
  assert.match(
    runSummary("model.finished", { state: "succeeded", duration_ms: 123 })
      .detail,
    /历史事件未记录/,
  );
});

const event = (tool, result) => ({
  kind: "tool.completed",
  data: { tool, result },
});

test("the result keeps the latest query result and the required evidence", () => {
  const result = investigationResult({
    ticket_id: "ticket-1",
    state: "waiting_input",
    events: [
      event("query_logistics", { message: "旧查询" }),
      event("query_logistics", { message: "暂无物流轨迹", cached: true }),
      event("request_evidence", { missing_items: ["承运商确认", "发货凭证"] }),
    ],
  });
  assert.equal(result.queries.length, 1);
  assert.match(result.queries[0].detail, /暂无物流轨迹.*缓存/);
  assert.match(result.nextStep, /承运商确认、发货凭证/);
  assert.match(result.outcome, /暂停等待补证/);
});

test("cancelled investigations retain facts without presenting an active evidence task", () => {
  const result = investigationResult({
    ticket_id: "ticket-1",
    state: "cancelled",
    events: [
      event("query_logistics", { message: "暂无物流轨迹" }),
      event("request_evidence", { missing_items: ["旧补证要求"] }),
    ],
  });
  assert.match(result.outcome, /已取消/);
  assert.match(result.nextStep, /重新发起/);
  assert.doesNotMatch(result.nextStep, /旧补证要求/);
  assert.equal(result.queries.length, 1);
});

test("a completed investigation with a proposal still requires supervisor approval", () => {
  const result = investigationResult({
    ticket_id: "ticket-1",
    state: "completed",
    events: [event("draft_refund", { amount_cents: 12900 })],
  });
  assert.match(result.outcome, /尚未执行退款/);
  assert.match(result.nextStep, /主管审核/);
});

test("a queued investigation never invents query results", () => {
  const result = investigationResult({
    ticket_id: "ticket-1",
    state: "queued",
    events: [],
  });
  assert.deepEqual(result.queries, []);
  assert.match(result.outcome, /等待开始/);
});

test("knowledge tasks never give ticket investigation or refund instructions", () => {
  for (const state of [
    "queued",
    "running",
    "completed",
    "cancelled",
    "human_needed",
  ]) {
    const result = investigationResult({ ticket_id: null, state, events: [] });
    assert.match(result.outcome, /知识/);
    assert.doesNotMatch(result.outcome + result.nextStep, /工单|退款|调查/);
    assert.deepEqual(result.queries, []);
  }
});
