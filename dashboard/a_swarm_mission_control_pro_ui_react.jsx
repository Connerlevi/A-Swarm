import React, { useEffect, useMemo, useRef, useState } from "react";
import { motion } from "framer-motion";
import {
  AlertTriangle,
  Activity,
  Cpu,
  Flame,
  Gauge,
  Network,
  Power,
  RefreshCw,
  SatelliteDish,
  ShieldAlert,
  Siren,
  Zap,
  Clock,
  WifiOff,
  Wifi,
  Search,
  ChevronRight,
  ChevronDown,
  Target,
  ClipboardList,
} from "lucide-react";

// shadcn/ui
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Progress } from "@/components/ui/progress";
import { Separator } from "@/components/ui/separator";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { ToastProvider, useToast } from "@/components/ui/use-toast";

// Recharts
import { Line, LineChart, ResponsiveContainer, Tooltip as RTooltip, XAxis, YAxis } from "recharts";

// Tailwind design tokens
// Brand: neutral slate + electric green accents used sparingly
// Note: This single-file component assumes tailwind + shadcn already configured in the host app.

/*
  Environment configuration
  -------------------------
  Supports Vite/Node env, window globals, and a .env file.
  Example .env:
    VITE_ASWARM_WS_URL=wss://your-server/ws
    VITE_ASWARM_TOKEN=eyJhbGciOiJI...
    VITE_ASWARM_AUTH_MODE=protocol # or "query" to append ?access_token=
*/
const env: any = (import.meta as any)?.env || ({} as any);

const WS_BASE: string =
  env?.VITE_ASWARM_WS_URL ||
  (typeof process !== "undefined" ? (process as any)?.env?.VITE_ASWARM_WS_URL : undefined) ||
  (typeof window !== "undefined" ? (window as any).__ASWARM_WS_URL : undefined) ||
  "ws://localhost:8000/ws";

const AUTH_TOKEN: string =
  env?.VITE_ASWARM_TOKEN ||
  (typeof process !== "undefined" ? (process as any)?.env?.VITE_ASWARM_TOKEN : undefined) ||
  (typeof localStorage !== "undefined" ? (localStorage.getItem("ASWARM_TOKEN") as string) : undefined) ||
  (typeof window !== "undefined" ? (window as any).__ASWARM_TOKEN : undefined) ||
  "";

const AUTH_MODE = (env?.VITE_ASWARM_AUTH_MODE ||
  (typeof window !== "undefined" ? (window as any).__ASWARM_AUTH_MODE : "protocol")) as
  | "protocol"
  | "query";

function buildWSUrl(base: string, token: string, mode: "protocol" | "query") {
  if (!token || mode !== "query") return base;
  try {
    const u = new URL(base);
    u.searchParams.set("access_token", token);
    return u.toString();
  } catch {
    return base + (base.includes("?") ? "&" : "?") + "access_token=" + encodeURIComponent(token);
  }
}

const WS_URL: string = buildWSUrl(WS_BASE, AUTH_TOKEN, AUTH_MODE);

// ---- Types ----
interface Episode {
  id: string
  attacklet_name: string
  status: "detected" | "running" | "failed" | "contained" | "resolved"
  ttd_seconds?: number | null
  score?: number
  technique?: string
  started_at?: string
}

interface FleetNode {
  name: string
  status: "healthy" | "degraded" | "failed"
  cpu: number
  memory: number
}

interface FleetStatus {
  total: number
  healthy: number
  avg_cpu: number
  avg_memory: number
  nodes: FleetNode[]
}

interface RuleItem {
  id?: string
  name: string
  severity: "low" | "medium" | "high" | "critical"
  technique?: string
  threshold?: number
  enabled: boolean
  version?: string
}

// ---- Utilities ----
const sevColor: Record<RuleItem["severity"], string> = {
  low: "bg-emerald-500/15 text-emerald-400 border-emerald-500/30",
  medium: "bg-amber-500/15 text-amber-400 border-amber-500/30",
  high: "bg-orange-500/15 text-orange-400 border-orange-500/30",
  critical: "bg-red-500/15 text-red-400 border-red-500/30",
};

const statusDot = (status: "ok" | "warn" | "crit" | "unknown" = "unknown") => {
  const map: Record<string, string> = {
    ok: "bg-emerald-500 shadow-[0_0_8px] shadow-emerald-500/70",
    warn: "bg-yellow-400 shadow-[0_0_8px] shadow-yellow-400/70",
    crit: "bg-red-500 shadow-[0_0_8px] shadow-red-500/70",
    unknown: "bg-zinc-500",
  };
  return <span className={`inline-block h-2.5 w-2.5 rounded-full ${map[status]}`} />;
};

// ---- Demo data generator (fallback when WS is unreachable) ----
function useDemoFeed(enabled: boolean, push: (msg: any) => void) {
  useEffect(() => {
    if (!enabled) return;
    const tid = setInterval(() => {
      const now = new Date().toISOString();
      // Random event
      const levels = ["info", "warning", "error"] as const;
      const level = levels[Math.floor(Math.random() * levels.length)];
      push({ type: "event", level, message: `${level.toUpperCase()}: heartbeat at ${now}` });
      // Random episode update
      if (Math.random() < 0.4) {
        const status = ["detected", "running", "contained", "resolved"][Math.floor(Math.random() * 4)] as Episode["status"];
        push({ type: "episode", id: `ep-${Math.floor(Math.random() * 5) + 1}`, attacklet_name: "data-exfiltration-v2", status, ttd_seconds: Math.random() * 0.3, score: Math.floor(Math.random() * 100) });
      }
      // Random fleet
      if (Math.random() < 0.5) {
        const nodes: FleetNode[] = Array.from({ length: 12 }).map((_, i) => {
          const r = Math.random();
          return {
            name: `node-${i + 1}`,
            status: r > 0.92 ? "failed" : r > 0.75 ? "degraded" : "healthy",
            cpu: +(0.5 + Math.random() * 2.5).toFixed(1),
            memory: Math.floor(14 + Math.random() * 12),
          };
        });
        push({ type: "fleet_status", total: nodes.length, healthy: nodes.filter(n => n.status === "healthy").length, avg_cpu: +(nodes.reduce((a, n) => a + n.cpu, 0) / nodes.length).toFixed(1), avg_memory: Math.floor(nodes.reduce((a, n) => a + n.memory, 0) / nodes.length), nodes });
      }
    }, 1500);
    return () => clearInterval(tid);
  }, [enabled, push]);
}

// ---- WebSocket hook ----
function useASwarmWS(onMsg: (data: any) => void) {
  const [connected, setConnected] = useState<boolean>(false);
  const [attempts, setAttempts] = useState<number>(0);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let alive = true;

    function connect() {
      try {
        const protocols = AUTH_MODE === "protocol" && AUTH_TOKEN ? (["bearer", AUTH_TOKEN] as any) : undefined;
        ws = new WebSocket(WS_URL, protocols);
        ws.onopen = () => { setConnected(true); setAttempts(0); };
        ws.onclose = () => { setConnected(false); if (alive) setTimeout(connect, 1500 * Math.min(5, attempts + 1)); setAttempts(a => a + 1); };
        ws.onerror = () => { /* noop; onclose will retry */ };
        ws.onmessage = (ev) => { try { onMsg(JSON.parse(ev.data)); } catch {} };
      } catch {
        setConnected(false);
      }
    }

    connect();
    return () => { alive = false; try { ws?.close(); } catch {} };
  }, [onMsg]);

  return connected;
}

// ---- Main component ----
export default function MissionControl(): JSX.Element {
  const { toast } = useToast();
  const [events, setEvents] = useState<{ ts: string; level: "info" | "warning" | "error"; message: string }[]>([]);
  const [episodes, setEpisodes] = useState<Record<string, Episode>>({});
  const [fleet, setFleet] = useState<FleetStatus>({ total: 0, healthy: 0, avg_cpu: 0, avg_memory: 0, nodes: [] });
  const [rules, setRules] = useState<RuleItem[]>([
    { id: "r1", name: "Privilege Escalation", severity: "critical", technique: "T1068", threshold: 0.95, enabled: true, version: "1.2.0" },
    { id: "r2", name: "Data Exfiltration", severity: "high", technique: "T1041", threshold: 0.9, enabled: true, version: "2.0.1" },
    { id: "r3", name: "Lateral Movement", severity: "high", technique: "T1021", threshold: 0.85, enabled: true, version: "1.7.3" },
    { id: "r4", name: "Command & Control", severity: "critical", technique: "T1071", threshold: 0.92, enabled: true, version: "3.0.0" },
    { id: "r5", name: "Persistence", severity: "medium", technique: "T1136", threshold: 0.8, enabled: false, version: "0.9.5" },
  ]);
  const [searchRules, setSearchRules] = useState("");
  const [epHistory, setEpHistory] = useState<Record<string, { ts: string; status: Episode["status"]; note?: string }[]>>({});
  const [timelineEp, setTimelineEp] = useState<string | null>(null);
  const [timelineOpen, setTimelineOpen] = useState(false);

  // Kill switch
  const [killEngaged, setKillEngaged] = useState(false);
  const [approval1, setApproval1] = useState(false);
  const [approval2, setApproval2] = useState(false);
  const [ttl, setTtl] = useState(300); // seconds
  const ttlRef = useRef<number>(ttl);
  ttlRef.current = ttl;

  // Derived KPIs (demo values if none)
  const kpis = useMemo(() => {
    const mttd = 0.08; // ms target demonstration
    const mttr = 1.3; // s target demonstration
    const detectionRate = 1.0;
    return { mttd, mttr, detectionRate };
  }, []);

  const handleMsg = (data: any) => {
    switch (data.type) {
      case "event": {
        const ts = new Date().toISOString().split("T")[1].split(".")[0];
        const level = (data.level ?? "info") as "info" | "warning" | "error";
        setEvents(prev => [{ ts, level, message: data.message }, ...prev].slice(0, 300));
        break;
      }
      case "episode": {
        const e: Episode = {
          id: data.id,
          attacklet_name: data.attacklet_name,
          status: data.status,
          ttd_seconds: data.ttd_seconds ?? null,
          score: data.score ?? 0,
          technique: data.technique,
          started_at: data.started_at,
        };
        setEpisodes(prev => ({ ...prev, [e.id]: e }));
        setEpHistory(prev => {
          const ts = new Date().toISOString();
          const arr = prev[e.id] ? [...prev[e.id]] : [];
          arr.push({ ts, status: e.status });
          return { ...prev, [e.id]: arr };
        });
        break;
      }
      case "fleet_status": {
        setFleet({
          total: data.total ?? 0,
          healthy: data.healthy ?? 0,
          avg_cpu: data.avg_cpu ?? 0,
          avg_memory: data.avg_memory ?? 0,
          nodes: data.nodes ?? [],
        });
        break;
      }
      case "rules_update": {
        setRules(data.rules ?? []);
        toast({ title: "Rules reloaded", description: new Date().toLocaleTimeString() });
        break;
      }
      case "kill_switch": {
        setKillEngaged(!!data.engaged);
        setApproval1(!!data.approvals?.[0]);
        setApproval2(!!data.approvals?.[1]);
        break;
      }
    }
  };

  const connected = useASwarmWS(handleMsg);
  useDemoFeed(!connected, handleMsg);

  // TTL countdown when engaged
  useEffect(() => {
    if (!killEngaged) return;
    const start = Date.now();
    const startTTL = ttlRef.current;
    const id = setInterval(() => {
      const elapsed = Math.floor((Date.now() - start) / 1000);
      const left = Math.max(0, startTTL - elapsed);
      setTtl(left);
      if (left === 0) {
        setKillEngaged(false);
        setApproval1(false);
        setApproval2(false);
        toast({ title: "Kill switch auto-reverted", description: "TTL expired, operations restored." });
        clearInterval(id);
      }
    }, 1000);
    return () => clearInterval(id);
  }, [killEngaged, toast]);

  const canEngage = approval1 && approval2 && !killEngaged;

  // ---- UI helpers ----
  function MetricCard({ icon: Icon, label, value, sub }: { icon: any; label: string; value: string; sub?: string }) {
    return (
      <Card className="bg-gradient-to-b from-slate-900 to-slate-950 border-slate-800">
        <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
          <CardTitle className="text-sm font-medium text-slate-200">{label}</CardTitle>
          <Icon className="h-4 w-4 text-emerald-400" />
        </CardHeader>
        <CardContent>
          <div className="text-2xl font-semibold tracking-tight text-white">{value}</div>
          {sub && <p className="text-xs text-slate-400 mt-1">{sub}</p>}
        </CardContent>
      </Card>
    );
  }

  function HeaderBar() {
    return (
      <div className="flex items-center justify-between px-4 py-3 border-b border-slate-800 bg-slate-950/60 backdrop-blur supports-[backdrop-filter]:bg-slate-950/40">
        <div className="flex items-center gap-3">
          <div className="h-8 w-8 rounded-xl bg-emerald-500/20 grid place-items-center"><ShieldAlert className="h-4 w-4 text-emerald-400" /></div>
          <div>
            <div className="text-sm uppercase tracking-[0.2em] text-slate-400">Protocol V4 • Zero‑Compromise</div>
            <h1 className="text-xl font-semibold text-white">A‑SWARM Mission Control</h1>
          </div>
        </div>
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2 text-xs text-slate-400">
            {statusDot(connected ? "ok" : "warn")}<span>{connected ? "Connected" : "Demo Mode"}</span>
            {connected ? <Wifi className="h-4 w-4 text-emerald-400" /> : <WifiOff className="h-4 w-4 text-yellow-400" />}
          </div>
          <Separator orientation="vertical" className="h-6 bg-slate-800" />
          <div className="text-xs text-slate-400 flex items-center gap-2"><Clock className="h-4 w-4" />{new Date().toLocaleString()}</div>
        </div>
      </div>
    );
  }

  function KillSwitchCard() {
    return (
      <Card className="border-slate-800 bg-slate-950">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-white"><Siren className="h-5 w-5 text-red-400" /> Kill Switch Governance</CardTitle>
          <CardDescription>Two‑person approval required. TTL auto‑revert ensures safety.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className={`w-full rounded-md border p-3 text-center ${killEngaged ? "border-red-500 text-red-400 animate-pulse" : "border-emerald-500 text-emerald-400"}`}>
            {killEngaged ? "KILL SWITCH ENGAGED" : "SYSTEM ARMED"}
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className={`rounded-md border p-3 ${approval1 ? "border-emerald-500 bg-emerald-500/5" : "border-slate-800"}`}>
              <div className="text-xs text-slate-400 mb-1">Operator 1</div>
              <Switch checked={approval1} onCheckedChange={setApproval1} />
            </div>
            <div className={`rounded-md border p-3 ${approval2 ? "border-emerald-500 bg-emerald-500/5" : "border-slate-800"}`}>
              <div className="text-xs text-slate-400 mb-1">Operator 2</div>
              <Switch checked={approval2} onCheckedChange={setApproval2} />
            </div>
          </div>
          <div className="flex items-center justify-between gap-3">
            <div>
              <div className="text-xs text-slate-400">TTL AUTO‑REVERT</div>
              <div className="text-lg font-semibold text-cyan-300">{ttl}s</div>
            </div>
            <div className="flex items-center gap-2">
              <Button variant="outline" className="border-slate-700" onClick={() => toast({ title: "Approval requested" })}>Request Approval</Button>
              <Button variant="destructive" disabled={!canEngage} onClick={() => { setKillEngaged(true); toast({ title: "Kill switch engaged", description: "Operations halted. TTL countdown started." }); }}>Engage</Button>
            </div>
          </div>
        </CardContent>
      </Card>
    );
  }

  function EpisodesPanel() {
    const list = Object.values(episodes).sort((a, b) => (b.started_at || "").localeCompare(a.started_at || ""));
    const statusStyle: Record<Episode["status"], string> = {
      detected: "border-emerald-500/40",
      running: "border-amber-400/40",
      contained: "border-sky-400/40",
      resolved: "border-slate-700",
      failed: "border-red-500/40",
    };
    return (
      <Card className="border-slate-800 bg-slate-950">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-white"><Target className="h-5 w-5 text-emerald-400" /> Red / Blue Episodes</CardTitle>
          <CardDescription>Live incidents and training episodes with swarm responses.</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-3 gap-3 mb-4">
            <MetricCard icon={Zap} label="MTTD" value={`${kpis.mttd} ms`} sub="P95, lab target ≤ 200 ms" />
            <MetricCard icon={Gauge} label="MTTR" value={`${kpis.mttr} s`} sub="P95, Ring‑1 actions" />
            <MetricCard icon={Activity} label="Detection Rate" value={`${(kpis.detectionRate * 100).toFixed(0)}%`} sub="Synthetic lab scenarios" />
          </div>
          <ScrollArea className="h-[260px] pr-2">
            <div className="space-y-2">
              {list.length === 0 && <div className="text-xs text-slate-500">No episodes yet.</div>}
              {list.map((e) => (
                <div key={e.id} className={`rounded-md border p-2 text-sm text-slate-200 ${statusStyle[e.status]}`}>
                  <div className="flex items-center justify-between">
                    <div className="font-medium">{e.attacklet_name}</div>
                    <div className="flex items-center gap-2">
                      <Badge className="bg-slate-800 text-slate-300 border border-slate-700">{(e.technique ?? "").toString()}</Badge>
                      <Badge variant="outline" className="capitalize">{e.status}</Badge>
                      <Button
                        size="sm"
                        variant="outline"
                        className="h-7 px-2 border-slate-700"
                        onClick={() => { setTimelineEp(e.id); setTimelineOpen(true); }}
                      >
                        Timeline
                      </Button>
                    </div>
                  </div>
                  <div className="text-xs text-slate-400 mt-1">TTD: {e.ttd_seconds ?? "-"}s • Score: {e.score ?? 0}</div>
                </div>
              ))}
            </div>
          </ScrollArea>
        </CardContent>
        <Dialog open={timelineOpen} onOpenChange={setTimelineOpen}>
          <DialogContent className="max-w-lg bg-slate-950 border-slate-800">
            <DialogHeader>
              <DialogTitle>Episode Timeline</DialogTitle>
              <DialogDescription>Chronology of status transitions.</DialogDescription>
            </DialogHeader>
            <div className="max-h-[50vh] overflow-auto pr-2">
              {timelineEp && (epHistory[timelineEp]?.length ?? 0) > 0 ? (
                <div className="relative pl-4">
                  <div className="absolute left-1 top-0 bottom-0 w-px bg-slate-800" />
                  {epHistory[timelineEp]!.map((h, i) => (
                    <div key={i} className="mb-3 flex items-start gap-3">
                      <div className="mt-1 h-2 w-2 rounded-full bg-emerald-400" />
                      <div>
                        <div className="text-xs text-slate-400">{new Date(h.ts).toLocaleString()}</div>
                        <div className="text-sm capitalize">{h.status}</div>
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="text-xs text-slate-500">No history yet.</div>
              )}
            </div>
          </DialogContent>
        </Dialog>
      </Card>
    );
  }

  function FleetPanel() {
    return (
      <Card className="border-slate-800 bg-slate-950">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-white"><Network className="h-5 w-5 text-emerald-400" /> Fleet Status</CardTitle>
          <CardDescription>Sentinels health and resource footprint.</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap items-center gap-4 mb-3 text-sm">
            <div>Sentinels: <span className="text-cyan-300 font-medium">{fleet.total}</span></div>
            <div>Healthy: <span className="text-emerald-400 font-medium">{fleet.healthy}</span></div>
            <div>CPU: <span className="text-cyan-300 font-medium">{fleet.avg_cpu}%</span></div>
            <div>Memory: <span className="text-cyan-300 font-medium">{fleet.avg_memory}MB</span></div>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-2">
            {fleet.nodes.map(n => (
              <div key={n.name} className={`rounded border p-2 text-xs ${n.status === "healthy" ? "border-emerald-500/30" : n.status === "degraded" ? "border-amber-400/40" : "border-red-500/40"}`}>
                <div className="flex items-center justify-between">
                  <div className="font-medium text-slate-200">{n.name}</div>
                  {statusDot(n.status === "healthy" ? "ok" : n.status === "degraded" ? "warn" : "crit")}
                </div>
                <div className="text-slate-400 mt-1">{n.cpu}% • {n.memory}MB</div>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>
    );
  }

  function RulesPanel() {
    const filtered = rules.filter(r => r.name.toLowerCase().includes(searchRules.toLowerCase()) || (r.technique ?? "").toLowerCase().includes(searchRules.toLowerCase()));
    return (
      <Card className="border-slate-800 bg-slate-950">
        <CardHeader className="flex gap-2">
          <div className="flex-1">
            <CardTitle className="flex items-center gap-2 text-white"><ClipboardList className="h-5 w-5 text-cyan-300" /> Detection Rules</CardTitle>
            <CardDescription>Signed content packs with hot‑reload and rollback on verify fail.</CardDescription>
          </div>
          <div className="flex items-end gap-2">
            <Button variant="outline" className="border-slate-700" onClick={() => handleMsg({ type: "rules_update", rules })}><RefreshCw className="h-4 w-4 mr-2" />Reload</Button>
          </div>
        </CardHeader>
        <CardContent>
          <div className="mb-3 flex items-center gap-2">
            <div className="relative flex-1">
              <Search className="absolute left-2 top-2.5 h-4 w-4 text-slate-500" />
              <Input value={searchRules} onChange={(e) => setSearchRules(e.target.value)} placeholder="Search by name or ATT&CK technique" className="pl-8 bg-slate-900 border-slate-800" />
            </div>
          </div>
          <ScrollArea className="h-[260px] pr-2">
            <div className="space-y-2">
              {filtered.map(rule => (
                <div key={rule.id ?? rule.name} className={`flex items-center justify-between rounded-md border p-2 ${sevColor[rule.severity]}`}>
                  <div className="flex items-center gap-3">
                    <Badge variant="outline" className="capitalize">{rule.severity}</Badge>
                    <div>
                      <div className="text-sm text-slate-200 font-medium">{rule.name}</div>
                      <div className="text-[11px] text-slate-400">{rule.technique ?? "Unknown"} • v{rule.version ?? "-"}</div>
                    </div>
                  </div>
                  <div className="flex items-center gap-3">
                    <div className="text-xs text-slate-400">thr {rule.threshold ?? "-"}</div>
                    <Switch checked={rule.enabled} onCheckedChange={(v) => setRules(rs => rs.map(r => r === rule ? { ...r, enabled: v } : r))} />
                  </div>
                </div>
              ))}
              {filtered.length === 0 && <div className="text-xs text-slate-500">No matching rules.</div>}
            </div>
          </ScrollArea>
        </CardContent>
      </Card>
    );
  }

  function EventStream() {
    const [filter, setFilter] = useState<'all' | 'info' | 'warning' | 'error'>("all");
    const list = events.filter(e => filter === 'all' ? true : e.level === filter);
    return (
      <Card className="border-slate-800 bg-slate-950">
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle className="flex items-center gap-2 text-white"><SatelliteDish className="h-5 w-5 text-cyan-300" /> Event Stream</CardTitle>
              <CardDescription>Live telemetry with level filters.</CardDescription>
            </div>
            <div className="flex items-center gap-2 text-xs">
              <Button variant={filter === 'all' ? 'default' : 'outline'} className="h-7 px-2" onClick={() => setFilter('all')}>All</Button>
              <Button variant={filter === 'info' ? 'default' : 'outline'} className="h-7 px-2" onClick={() => setFilter('info')}>Info</Button>
              <Button variant={filter === 'warning' ? 'default' : 'outline'} className="h-7 px-2" onClick={() => setFilter('warning')}>Warn</Button>
              <Button variant={filter === 'error' ? 'default' : 'outline'} className="h-7 px-2" onClick={() => setFilter('error')}>Error</Button>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          <ScrollArea className="h-40 pr-2">
            <div className="space-y-1 text-xs">
              {list.map((e, i) => (
                <div key={i} className={`border-b border-slate-900 pb-1 ${e.level === 'error' ? 'text-red-400' : e.level === 'warning' ? 'text-yellow-300' : 'text-emerald-300'}`}>
                  [{e.ts}] {e.message}
                </div>
              ))}
              {list.length === 0 && <div className="text-slate-500">No events.</div>}
            </div>
          </ScrollArea>
        </CardContent>
      </Card>
    );
  }

  // Simple sparkline for MTTD trend (demo)
  function KpiSparkline() {
    const data = useMemo(() => Array.from({ length: 24 }).map((_, i) => ({ t: i, v: +(0.05 + Math.random() * 0.12).toFixed(3) })), []);
    return (
      <div className="h-16">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ left: 0, right: 0, top: 10, bottom: 0 }}>
            <XAxis dataKey="t" hide />
            <YAxis hide domain={[0, 0.2]} />
            <RTooltip content={({ active, payload }) => active && payload?.[0] ? <div className="bg-slate-900/90 border border-slate-700 text-xs px-2 py-1 rounded">MTTD {payload[0].value} ms</div> : null} />
            <Line type="monotone" dataKey="v" strokeWidth={2} dot={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    );
  }

  return (
    <ToastProvider>
      <div className="min-h-screen bg-gradient-to-b from-slate-950 via-slate-950 to-slate-900 text-slate-200">
        <HeaderBar />

        <div className="max-w-7xl mx-auto p-4 grid grid-cols-1 lg:grid-cols-2 gap-4">
          {/* Left column */}
          <div className="space-y-4">
            <KillSwitchCard />
            <EpisodesPanel />
          </div>

          {/* Right column */}
          <div className="space-y-4">
            <FleetPanel />
            <RulesPanel />
          </div>

          {/* Full width bottom */}
          <div className="lg:col-span-2">
            <EventStream />
          </div>

          {/* KPI sparkline */}
          <div className="lg:col-span-2">
            <Card className="border-slate-800 bg-slate-950">
              <CardHeader>
                <CardTitle className="flex items-center gap-2"><Activity className="h-5 w-5 text-emerald-400" /> Performance Trend (MTTD)</CardTitle>
                <CardDescription>Short-term P95 MTTD trend (demo data)</CardDescription>
              </CardHeader>
              <CardContent>
                <KpiSparkline />
              </CardContent>
            </Card>
          </div>
        </div>
      </div>
    </ToastProvider>
  );
}

function TargetIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" className="text-emerald-400">
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="1.5"/>
      <circle cx="12" cy="12" r="4" stroke="currentColor" strokeWidth="1.5"/>
      <path d="M12 3v3M21 12h-3M12 21v-3M3 12h3" stroke="currentColor" strokeWidth="1.5"/>
    </svg>
  )
}

function ClipboardIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" className="text-cyan-300">
      <path d="M9 4h6a2 2 0 0 1 2 2v1h1a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V9a2 2 0 0 1 2-2h1V6a2 2 0 0 1 2-2Z" stroke="currentColor" strokeWidth="1.5"/>
      <path d="M9 6h6" stroke="currentColor" strokeWidth="1.5"/>
    </svg>
  )
}
