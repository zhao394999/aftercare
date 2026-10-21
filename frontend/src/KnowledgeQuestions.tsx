import { useState } from "react";
import { post } from "./api";
import { Badge, QueryState, useAuth, useData } from "./ui";
import type { Run } from "./types";

export function KnowledgeQuestions() {
  const { profile } = useAuth();
  const [question, setQuestion] = useState("");
  const [channel, setChannel] = useState("*");
  const [orderDate, setOrderDate] = useState("");
  const [run, setRun] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  return (
    <section className="card form-card knowledge-questions">
      <h2>内部知识问答</h2>
      <p className="subtle">
        先检索政策片段，再由模型组织回答。无匹配资料时转人工；不执行退款或自动发送给客户。
      </p>
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          setError("");
          setRun("");
          try {
            const result = await post<Run>("/knowledge/ask", {
              question,
              channel,
              order_date: orderDate
                ? new Date(orderDate + "T00:00:00+08:00").toISOString()
                : null,
            });
            setRun(result.id);
          } catch (e) {
            setError((e as Error).message);
          } finally {
            setBusy(false);
          }
        }}
      >
        <div className="field">
          <label htmlFor="knowledge-question">问题</label>
          <textarea
            id="knowledge-question"
            required
            minLength={2}
            maxLength={500}
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder="例如：承运商确认丢件后，申请退款需要哪些证据？"
          />
        </div>
        <div className="form-grid">
          <div className="field">
            <label htmlFor="knowledge-channel">订单渠道</label>
            <input
              id="knowledge-channel"
              required
              maxLength={50}
              value={channel}
              onChange={(e) => setChannel(e.target.value)}
            />
            <small>* 仅检索通用政策；填写具体渠道可同时检索渠道政策。</small>
          </div>
          <div className="field">
            <label htmlFor="knowledge-date">订单日期（可选）</label>
            <input
              type="date"
              id="knowledge-date"
              value={orderDate}
              onChange={(e) => setOrderDate(e.target.value)}
            />
            <small>不填则按当前时间匹配政策。</small>
          </div>
        </div>
        {error && (
          <p role="alert" className="form-error">
            {error}
          </p>
        )}
        <button
          className="button primary"
          disabled={
            busy ||
            !profile.model_enabled ||
            !profile.permissions.includes("runs.start")
          }
        >
          {busy ? "正在提交…" : "检索并生成回答"}
        </button>
        {!profile.model_enabled && (
          <p className="subtle">模型尚未配置，可先维护并发布政策。</p>
        )}
      </form>
      {run && <KnowledgeResult key={run} id={run} />}
    </section>
  );
}

function KnowledgeResult({ id }: { id: string }) {
  const query = useData<Run>("/runs/" + id, 3000);
  const run = query.data;
  const answer = run?.checkpoint.knowledge_answer;
  return (
    <div className="knowledge-result" aria-live="polite">
      <QueryState query={query}>
        {run && (
          <>
            <p>
              <Badge state={run.state} /> 模型：{run.model} · 调用{" "}
              {run.call_count} 次 · {run.input_tokens + run.output_tokens} Token
            </p>
            {answer ? (
              <>
                <p className="knowledge-answer">{answer.answer}</p>
                {answer.sources.map((source, i) => (
                  <blockquote className="source-quote" key={source.citation}>
                    <a className="text-link" href={"#policy-" + source.id}>
                      [{i + 1}] {source.title} · V{source.version}
                    </a>
                    <p>{source.body}</p>
                  </blockquote>
                ))}
              </>
            ) : (
              <p>
                {["queued", "running"].includes(run.state)
                  ? "正在检索并组织回答，可稍候查看结果。"
                  : "回答未完成，请转人工核实或重新提问。"}
              </p>
            )}
            {run.error && (
              <p className="form-error">任务停止原因：{run.error}</p>
            )}
          </>
        )}
      </QueryState>
    </div>
  );
}
