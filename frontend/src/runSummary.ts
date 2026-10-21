import type { Run } from "./types";

const tools: Record<string, [string, string]> = {
  read_order: ["核对订单", "内部订单查询工具 · 商家导入/人工核查数据"],
  read_evidence: ["核查证据", "内部证据查询工具"],
  search_policies: ["检索适用政策", "内部知识库检索工具"],
  query_logistics: ["查询物流", "受控物流查询工具 · 快递100"],
  request_evidence: ["创建补证待办", "内部工单工具"],
  draft_refund: ["提交退款提案", "内部提案工具 · 等待人工审批"],
  finish: ["转人工处理", "内部调查结束工具"],
  answer_knowledge: ["生成知识回答", "只读知识问答"],
};

export function investigationResult(
  run: Pick<Run, "ticket_id" | "state" | "events">,
) {
  if (run.ticket_id === null) {
    const outcomes: Record<string, [string, string]> = {
      queued: [
        "知识问答已排队，等待开始。",
        "等待回答生成，可在下方查看任务状态与调用记录。",
      ],
      running: [
        "知识问答正在生成，尚未形成最终回答。",
        "等待回答生成，可在下方查看任务状态与调用记录。",
      ],
      completed: [
        "知识回答已生成。",
        "请核对回答引用的政策版本，回答仅供内部参考。",
      ],
      cancelled: [
        "本次知识问答已取消。",
        "如仍需答案，请前往知识与政策重新提问。",
      ],
    };
    const [outcome, nextStep] = outcomes[run.state] || [
      "本次知识问答未完成，需要人工核实。",
      "请查看任务停止原因，人工核实政策后再提问。",
    ];
    return { outcome, nextStep, queries: [] };
  }
  const completed = [...(run.events || [])]
    .reverse()
    .filter((e) => e.kind === "tool.completed");
  const action = completed.find((e) =>
    ["request_evidence", "draft_refund", "finish"].includes(
      String(e.data.tool),
    ),
  );
  const actionDetail = action
    ? runSummary(action.kind, action.data).detail
    : "";
  const queries = [
    "read_order",
    "query_logistics",
    "read_evidence",
    "search_policies",
  ]
    .map((tool) => completed.find((e) => e.data.tool === tool))
    .filter((e) => e !== undefined)
    .map((e) => ({
      title: tools[String(e.data.tool)][0],
      detail: runSummary(e.kind, e.data).detail,
    }));
  const outcomes: Record<string, [string, string]> = {
    queued: [
      "调查已排队，等待开始。",
      "等待调查结果；也可进入工单继续人工核查。",
    ],
    running: [
      "正在调查，尚未形成最终结论。",
      "等待调查完成，已有查询情况见下方。",
    ],
    waiting_input: [
      "关键材料不足，调查暂停等待补证。",
      actionDetail || "请在关联工单补充并核验材料，再恢复调查。",
    ],
    completed: [
      actionDetail || "本次智能调查已结束，业务结果以关联工单为准。",
      action?.data.tool === "draft_refund"
        ? "请主管审核提案，审批通过后再登记执行结果。"
        : "查看关联工单，确认后续处理事项。",
    ],
    human_needed: [
      actionDetail || "自动调查未完成，需要人工接管。",
      "请核对已查询的事实与工单材料，再由人工继续处理。",
    ],
    cancelled: [
      "本次调查已取消，已完成的查询记录保留。",
      "如需继续处理，请从关联工单重新发起调查。",
    ],
  };
  const [outcome, nextStep] = outcomes[run.state] || [
    "本次调查未完成。",
    "请查看关联工单，人工核对后继续处理。",
  ];
  return { outcome, nextStep, queries };
}

export function runSummary(kind: string, data: Record<string, unknown>) {
  const tool = tools[String(data.tool)] || ["处理调查步骤", "内部受控工具"];
  const result = (data.result || {}) as Record<string, unknown>;
  if (kind === "plan.step")
    return {
      title: `模型选择：${tool[0]}`,
      detail: String(data.rationale || "未记录依据摘要"),
      interface: tool[1],
    };
  if (kind === "tool.completed") {
    let detail = "步骤已完成。";
    if (data.tool === "read_order")
      detail = `订单 ${result.external_id || ""}；可退金额 ¥${(Number(result.available_cents || 0) / 100).toFixed(2)}。数据来源：${result.source || "商家订单"}。`;
    if (data.tool === "read_evidence")
      detail = `读取 ${Array.isArray(result.items) ? result.items.length : 0} 条证据摘要，保留核验状态。`;
    if (data.tool === "search_policies")
      detail = `找到 ${Array.isArray(result.items) ? result.items.length : 0} 个政策片段。${result.retrieval || "按适用范围检索"}`;
    if (data.tool === "query_logistics")
      detail = `${result.message || result.state_label || "已记录查询结果"}；${result.cached ? "复用近期查询缓存" : result.simulated ? "模拟结果" : "查询状态：" + (result.status || "未记录")}。`;
    if (data.tool === "request_evidence")
      detail = `等待补充：${Array.isArray(result.missing_items) ? result.missing_items.join("、") : "请查看工单"}。`;
    if (data.tool === "draft_refund")
      detail = `已提交 ¥${(Number(result.amount_cents || 0) / 100).toFixed(2)} 退款提案，等待主管审批，尚未执行退款。`;
    if (data.tool === "finish")
      detail = String(result.reason || "交由人工继续处理。");
    return { title: tool[0] + " · 工具结果", detail, interface: tool[1] };
  }
  const labels: Record<string, [string, string]> = {
    "run.queued": ["调查已排队", "等待工作进程执行。"],
    "run.resumed": ["恢复调查", "补证后重新核查事实。"],
    "run.cancelled": ["调查已取消", "停止后续步骤，已发生的调用仍保留。"],
    "run.human_needed": [
      "需要人工接管",
      "自动调查未完成，请查看工单和技术诊断。",
    ],
    "model.started": [
      "发起大模型调用",
      `${data.model || "运行所列模型"} · 第 ${data.call_number || ""} 次调用。`,
    ],
    "model.finished": [
      data.state === "succeeded" ? "模型返回成功" : "模型调用异常",
      `耗时 ${data.duration_ms || 0} 毫秒；${data.input_tokens === undefined ? "历史事件未记录分次 Token" : `输入 ${data.input_tokens} / 输出 ${data.output_tokens} Token`}。`,
    ],
    "model.response_lost": ["模型响应丢失", "用量与费用待核对，保留费用占用。"],
    "tool.rejected": [
      "操作被校验拦截",
      `${tool[0]}未完成，系统未将其视为执行成功。原因代码：${data.code || "未记录"}`,
    ],
    "knowledge.queued": [
      "知识问答已排队",
      `检索到 ${data.source_count || 0} 个适用片段。`,
    ],
    "knowledge.answered": [
      "知识回答已生成",
      `附有 ${data.source_count || 0} 个版本来源，仅供内部参考。`,
    ],
  };
  const [title, detail] = labels[kind] || [
    "系统记录",
    "该历史事件暂未提供可读摘要，可展开技术诊断。",
  ];
  return {
    title,
    detail,
    interface:
      kind === "model.started"
        ? String(data.interface || "POST /chat/completions（兼容接口）")
        : "",
  };
}
