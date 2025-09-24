// A-SWARM UI Type Definitions
// Production-ready types for cybersecurity dashboard

export type Ring = 1 | 2 | 3 | 4 | 5;
export type EventSeverity = "low" | "medium" | "high" | "critical";
export type ConnectionStatus = "connecting" | "connected" | "disconnected" | "error";

export interface SwarmEvent {
  id: string;
  ts: number; // epoch ms
  ring: Ring;
  severity: EventSeverity;
  source: string;
  summary: string;
  details?: string;
  node_name?: string;
  score?: number;
  witness_count?: number;
  containment_action?: string;
}

export interface SystemMetrics {
  mttd_p95_ms: number;
  mttr_p95_s: number;
  cpu_percent: number;
  memory_mb: number;
  packet_rate: number;
  error_rate: number;
  queue_depth: number;
}

export interface NodeStatus {
  id: string;
  name: string;
  status: "healthy" | "degraded" | "offline";
  version: string;
  last_seen: number;
  cpu_percent: number;
  memory_mb: number;
  pod_count: number;
}

export interface RingStatus {
  ring: Ring;
  name: string;
  description: string;
  active: boolean;
  policy_count: number;
  last_action?: string;
  last_action_ts?: number;
}

export interface ActionCertificate {
  id: string;
  ts: string;
  actor: string;
  action: string;
  from_ring?: Ring;
  to_ring?: Ring;
  reason: string;
  hash: string;
  prev_hash?: string;
  signature: string;
  artifacts: Array<{
    name: string;
    hash: string;
    size: number;
  }>;
}

export interface EvidencePack {
  incident_id: string;
  created_ts: string;
  certificates: ActionCertificate[];
  metrics_snapshot: SystemMetrics;
  node_states: NodeStatus[];
  export_url: string;
}

export interface AttackScenario {
  id: string;
  name: string;
  description: string;
  target_ring: Ring;
  duration_s: number;
  intensity: "low" | "medium" | "high";
  enabled: boolean;
}