export interface Profile {
  user: { id: string; name: string; email: string };
  tenant: {
    id: string;
    name: string;
    is_demo: boolean;
    config: Record<string, unknown>;
  };
  role: string;
  permissions: string[];
  csrf: string;
  model_enabled: boolean;
}
export interface LogisticsSnapshot {
  source?: string;
  status?: string;
  message?: string;
  queried_at?: string;
  state?: string;
  state_label?: string;
  cached?: boolean;
  simulated?: boolean;
  events?: { time: string; description: string; state_code: string }[];
}
export interface Order {
  ticket_count?: number;
  id: string;
  external_id: string;
  product: string;
  quantity: number;
  paid_cents: number;
  refunded_cents: number;
  reserved_cents: number;
  customer: string;
  carrier_code: string;
  tracking_number: string;
  tracking_phone_configured: boolean;
  logistics_snapshot: LogisticsSnapshot;
  logistics_queried_at: string | null;
  channel: string;
  status: string;
  placed_at: string;
  shipped_at: string | null;
  promised_at: string | null;
  version: number;
  verified_at: string | null;
}
export interface Evidence {
  id: string;
  ticket_id: string;
  kind: string;
  title: string;
  body: string;
  source: string;
  observed_at: string;
  expires_at: string | null;
  verified_at: string | null;
  verified_by: string | null;
  file_key: string | null;
  filename: string | null;
}
export interface Proposal {
  id: string;
  ticket_id: string;
  created_by: string;
  amount_cents: number;
  reason: string;
  policy_id: string;
  policy_hash: string;
  evidence_ids: string[];
  payload_hash: string;
  state: string;
  expires_at: string;
  created_at: string;
  decision_note: string;
  ticket?: Ticket;
  order?: Order;
}
export interface Action {
  id: string;
  ticket_id: string;
  order_id: string;
  proposal_id: string;
  amount_cents: number;
  actual_cents: number | null;
  state: string;
  claimed_by: string | null;
  external_ref: string | null;
  external_state: string | null;
  receipt_evidence_id: string | null;
  executed_by: string | null;
  note: string;
  ticket?: Ticket;
  order?: Order;
}
export interface Policy {
  id: string;
  title: string;
  version: number;
  state: string;
  body: string;
  channel: string;
  effective_from: string;
  effective_until: string | null;
  allow_refund_on_loss: boolean;
  max_refund_cents: number;
}
export interface Run {
  ticket_id: string | null;
  id: string;
  state: string;
  model: string;
  call_count: number;
  input_tokens: number;
  output_tokens: number;
  cost_microusd: number;
  reserved_microusd: number;
  budget_microusd: number;
  created_at: string;
  error: string;
  checkpoint: {
    knowledge_answer?: {
      answer: string;
      sources: {
        citation: string;
        id: string;
        title: string;
        version: number;
        body: string;
      }[];
    };
    observations?: {
      tool: string;
      result?: Record<string, unknown>;
      validation_error?: string;
      message?: string;
    }[];
  };
  events?: {
    seq: number;
    kind: string;
    created_at: string;
    data: Record<string, unknown>;
  }[];
}
export interface Audit {
  id: string;
  created_at: string;
  actor_id: string;
  event: string;
  resource_id: string;
  details: Record<string, unknown>;
}
export interface Communication {
  id: string;
  channel: string;
  content: string;
  created_at: string;
  created_by: string;
}
export interface Ticket {
  investigation?: { message: string; state: string };
  source_ref: string | null;
  id: string;
  number: string;
  title: string;
  request: string;
  issue: string;
  state: string;
  owner_id: string;
  created_at: string;
  due_at: string;
  version: number;
  evidence_version: number;
  order: Order;
  evidence?: Evidence[];
  proposals?: Proposal[];
  actions?: Action[];
  communications?: Communication[];
  runs?: Run[];
  history?: Audit[];
}
export interface Overview {
  evidence_review: number;
  open: number;
  waiting_approval: number;
  closed_today: number;
  states: Record<string, number>;
  recent: Ticket[];
  trend: { date: string; closed: number }[];
  running: number;
  cost_microusd: number | null;
  updated_at: string;
  model_enabled: boolean;
}
export interface Member {
  id: string;
  name: string;
  email: string | null;
  role: string;
  enabled: boolean;
}
export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size?: number;
}
