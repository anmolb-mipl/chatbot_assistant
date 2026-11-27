export type Msg = {
  id: number;
  sender: "customer" | "bot" | "agent" | "system";
  text: string;
  auto: boolean;
  created_at: string;
};

export type Conv = {
  id: string;
  status: "bot" | "waiting" | "live" | "closed";
  summary: string;
  agent_id: number | null;
  agent_name: string | null;
  updated_at: string;
  mine?: boolean;
  contact?: string | null;
  rating?: number | null;
  rating_comment?: string | null;
  fallback_sent?: boolean;
  closed_by?: string | null;
  channel?: "chat" | "call";
};

export type ChatEvent =
  | { type: "message.created"; conversation_id: string; message: Msg }
  | { type: "queue.waiting"; conversation_id: string; summary: string; channel?: "chat" | "call" }
  | { type: "queue.taken"; conversation_id: string; agent_id: number; agent_name: string }
  | { type: "queue.closed"; conversation_id: string }
  | { type: "queue.timeout"; conversation_id: string }
  | { type: "queue.contact"; conversation_id: string }
  | { type: "typing"; conversation_id: string; from: "customer" | "agent" }
  | { type: "call.signal"; conversation_id: string; from: "customer" | "agent"; kind: string; data: any };