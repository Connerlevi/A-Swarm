# A-SWARM — Comprehensive Context Document (No‑Nonsense Edition)

*Last updated: 2025‑08‑25*

---

## 0) Executive Summary

**A‑SWARM** is an **autonomic defense layer** for AI datacenters (GPU clusters / colos) that detects **coordinated anomalies** and applies **guardrailed micro‑containment** fast, while producing **verifiable Action Certificates** for every actuation.

**Pilot beachhead:** AI datacenters (GPU compute clusters, colocation racks/pods).

**Pilot KPIs (target at P95):**

* **MTTD** (coordinated anomaly) **< 200 ms**
* **MTTR** (to micro‑containment effective) **< 5 s**
* **False‑positive rate at Ring‑1** **< 0.5%**; **Ring‑2 FPs = 0**

**What it is not:** We do **not** modify safety PLCs/SIS, we do **not** perform offensive actions, and we do **not** take irreversible actions without human approval.

---

## 1) Problem & Why Now

* **Cascade risk in AI DCs:** High‑fanout east‑west traffic, shared identity/storage fabrics, and GPU job churn amplify benign faults and malicious spread.
* **Detection is not containment:** SIEM/SOAR/XDR alert humans but rarely bound blast radius **within seconds** using pre‑authorized, reversible controls.
* **Operational audit gap:** Operators, insurers, and regulators want **provable evidence** of safe, bounded intervention with minimal privileges.

**Timing:** GPU build‑out + rising insider/API key abuse + tougher audit/insurance expectations.

---

## 2) System Overview

A‑SWARM is a **distributed control plane** built from:

* **Sentinel** — lightweight host/gateway agents emitting bounded telemetry (packet sketches, process graphs) and executing **Ring‑1** micro‑acts.
* **Pheromone** — low‑bandwidth gossip/quorum that elevates coordinated anomalies with context (witness count, window, scenario, counts).
* **Policy Compiler** — transforms a catalog of pre‑authorized micro‑acts into deterministic automata with TTL, revert plans, and proofs.
* **Actuation Bus** — executes Ring‑1 actions (e.g., K8s `NetworkPolicy` quarantine, token revoke, rate limit) with **auto‑revert TTL** and kill‑switches.
* **TwinLab** — deterministic replay, scenario injection, and canary/rollback rehearsal **before** production.
* **Action Certificates** — signed JSON artifacts linking context → decision → actuation → outcome with policy/version hashes.

**Rings of engagement:**

* **R0 Observe** — sensing only.
* **R1 Micro‑act** — pre‑authorized, reversible; enforced TTL; probe‑verified effectiveness.
* **R2 Major** — human‑in‑loop required.
* **RS Safety** — SIS/PLC domain; **explicitly out‑of‑scope**.

---

## 3) Architecture (at a glance)

```
Hosts/Gateways:  [Sentinel]───[Local Policy]
                     │
             (bounded deltas)
                     ▼
             [Pheromone Mesh]─────►(Quorum/Elevate)────►[Policy Compiler]
                                                        │
                                                        ▼
                                                [Actuation Bus (R1)]
                                                        │
                                                        ▼
                                                    [Sentinel]

TwinLab:   Deterministic Replay ──► Red/Blue Sandbox ──► Policy Compile
Evidence:  Action Certificates (signed), SIEM export
```

**Key properties:** bounded CPU/RAM/egress for agents; deterministic policy state machines; time‑sync for replay; rate‑limited gossip; partition‑tolerant defaults; explicit kill‑switches.

---

## 4) Data & Control Flows (detailed)

1. **Sense (R0):** Sentinels emit **health deltas** (no payloads):

   * Packet sketch (e.g., per‑bucket counts, ports/features)
   * Process graph changes (PID lineage deltas)
   * Resource ceilings: CPU <1%, RSS <50 MB, egress <5 kbps
2. **Elevate:** Pheromone aggregates weak signals into a structured **elevation** when quorum/thresholds are met (fields: `count`, `window_s`, `witnesses`, `scenario`).
3. **Decide:** Policy Kernel validates Ring‑1 eligibility (catalog, guardrails, change window) and compiles a **micro‑act** with TTL.
4. **Act (R1):** Actuation Bus applies a bounded control (e.g., network quarantine label → `NetworkPolicy` isolate; token revoke; per‑host rate limit).
5. **Verify:** Effectiveness is proved via **active probes** (e.g., connection attempts blocked) — not sleeps.
6. **Certify:** Action Certificate written and signed (demo HMAC now; HSM roadmap). Export to SIEM.
7. **Revert:** TTL controller removes labels/limits automatically; canary rollback on violations.

---

## 5) Action Certificate (schema excerpt)

```json
{
  "certificate_id": "2025-08-25T12:34:56Z",
  "site_id": "aswarm",
  "asset_id": "pod/anomaly-xyz",
  "timestamps": {
    "anomaly_start": "...",
    "detect_elevated": "...",
    "actuation_start": "...",
    "actuation_effective": "..."
  },
  "elevation": { "count": 37, "window_s": 10, "witnesses": 4, "scenario": "portscan-fanout" },
  "policy": {
    "policy_id": "aswarm-quarantine",
    "version_hash": "sha256:...",
    "ttl_seconds": 120
  },
  "action": {
    "ring": 1,
    "kind": "networkpolicy_isolate",
    "params": { "selector": "app=anomaly" }
  },
  "outcome": { "status": "contained", "proof": "probe denied at t2" },
  "signatures": [ "hmac-sha256:..." ]
}
```

---

## 6) Measurement Methodology (KPIs)

* **MTTD** = `t1_detect` − `t0_anomaly` (ms); derived from anomaly start stamp and elevation stamp.
* **MTTR** = `t2_effective` − `t1_detect` (s); `t2_effective` requires **probe‑verified** containment.
* **Percentiles:** P50/P95/P99 over repeated drills (batch runner).
* **False positives:** Ring‑1 FP counted when an actuation triggers without subsequent validation or contradicts ground truth in twin.
* **Blast radius (roadmap metric):** distinct destinations per fixed window; target ≥80% reduction during drills.

---

## 7) Safety & Guardrails

* **Ring‑1 only** without human approval; **TTL auto‑revert** and canary/rollback.
* **Explicit kill‑switches:** site‑local halt and global suspend.
* **No SIS/PLC modifications**; RS boundary contract.
* **Twin‑first** for all new policies; change windows honored.

---

## 8) Security Model

* **Supply chain:** signed builds, CI provenance, SBOM (roadmap).
* **Transport:** PQC‑ready channels (Kyber/Dilithium stubs today; HSM custody later).
* **Logging:** forward‑secure sealing (roadmap) with external anchoring.
* **Identity:** scoped tokens; catalog signature and versioning for Ring‑1 actions.

---

## 9) What Sets A‑SWARM Apart (substantive)

1. **Actuation with proof:** Not just alerts — **pre‑authorized micro‑acts** with **TTL** and **probe‑verified effectiveness**.
2. **Distributed elevation:** Low‑bandwidth quorum/gossip that is robust to partitions and tuned to reduce false positives.
3. **Twin‑first discipline:** Policy compilation and rehearsal before production.
4. **Insurance‑grade audit:** Signed, SIEM‑ready **Action Certificates** with policy/version hashes and timing evidence.
5. **Safety boundaries baked‑in:** Clear Rings; SIS untouched; reversible by design.

---

## 10) Current Status (honest)

**Code & CI:**

* Monorepo scaffold with Sentinel, Pheromone, Policy Compiler, TwinLab stubs; tests green; branch protection enabled.
* Prototype v1: Kubernetes testbed with 10–100 pods; anomaly fan‑out; NetworkPolicy quarantine; **probe‑verified** MTTR; **signed** certificates; **TTL auto‑revert**; batch runner.
* Dashboard (Streamlit) for MTTD/MTTR percentiles; Helm chart; kind‑based e2e CI available as add‑ons.

**Gaps:** Sentinel still uses stubs (eBPF/conntrack in progress); blast‑radius metric not yet visualized; adapters (IdP/ToR/OT) early.

---

## 11) Near‑Term Roadmap (0–90 days)

**0–30 days**

* Sentinel: add conntrack/eBPF packet sketch + process lineage with bounded CPU.
* KPI board: MTTD/MTTR P50/P95/P99; signature verification; CSV export.
* Helm chart and prototype‑e2e CI merged to main; artifacts uploaded automatically.
* Pilot LOIs with 1–2 design partners; finalize Ring‑1 catalog.

**31–60 days**

* Pheromone v0.2: distributed quorum, rate‑limit guards, partition‑tolerant defaults.
* Adapters v0.1: IdP token revoke, storage throttle, ToR ACL (read‑only config path).
* Certificates v0.2: richer elevation context + forward‑secure log references.

**61–90 days**

* TwinLab v0.2: deterministic replay harness + scenario library; canary/rollback flows.
* Compliance pack v0.1: IEC 62443 / NIST 800‑82 mappings with evidence generators.
* Pilot report template (SLO attainment, blast‑radius reduction, ROI).

---

## 12) Longer‑Term Roadmap (6–24 months)

* Sentinel hardened path: kernel‑level sensors, deterministic ML with bounded compute, gateway‑only mode.
* Pheromone federation: cross‑site anonymized antibody sharing; stability proofs.
* Policy correctness: formal methods (TLA+/Coq) for critical paths; vendor co‑certified gateways.
* Actuation hardware: SmartNIC/DPU and TCAM enforcement; multi‑cloud primitives.
* TwinLab+: synthetic data generators; replay packs for SIEM/XDR vendors.
* Security maturity: HSM‑backed keys; PQC by default; full forward‑secure logging.
* Productization: enterprise console, APIs, SKUs (per host/MW, site license, premium support).

---

## 13) Deployment & Integration

* Start on a **single rack/pod** in observe → micro‑act phases.
* Kubernetes first; VM/bare‑metal agents supported for non‑K8s workloads.
* Adapters (read‑only) for OT protocols (Modbus/TCP, OPC UA), IdP, storage, and network devices.
* Evidence flows to SIEM/SOAR; weekly KPI reports; end‑of‑pilot ROI analysis.

---

## 14) Compliance & Safety Mapping (pilot scope)

* **IEC 62443:** SR 5.1 (restrict data flow) via micro‑segmentation; SR 7.x (monitoring).
* **NIST 800‑82:** continuous monitoring and segmentation; explicit non‑interaction with SIS.
* **Evidence pack:** certificates, catalog snapshots, twin drill logs, CI provenance.

---

## 15) Risk Register (top items)

* **False positives / over‑containment:** quorum tuning, twin rehearsal, Ring‑1 reversibility.
* **Integration friction:** read‑only first; minimal privileges; vendor co‑cert on gateways.
* **SIS scope creep:** written ROE; RS boundary contract; certificate audits.
* **System security:** signed builds, minimal RBAC, forward‑secure logging, separate kill‑switch paths.

---

## 16) Interfaces (I/O)

* **Inputs:** bounded Sentinel deltas; selected logs/flows; adapter telemetry (read‑only).
* **Policy catalog:** YAML/JSON with act types, params, TTL, revert; signed and versioned.
* **Actuation:** K8s `NetworkPolicy`, labels/annotations; token revoke; rate limiters; VLAN/ACL hooks (roadmap).
* **Outputs:** Action Certificates (JSON), metrics (MTTD/MTTR), SIEM events, dashboards.

---

## 17) Pre‑Flight & Ops

* Verify Kubernetes context; NetworkPolicy support; time‑sync; SIEM sink.
* Confirm Ring‑1 catalog and maintenance windows; test kill‑switch.
* Drill cadence: 2–3×/week; short post‑drill adjudication; update catalog.

---

## 18) What "Good" Looks Like (8–12 week pilot)

* P95 **MTTD < 200 ms**, **MTTR < 5 s**; Ring‑1 FPR < 0.5%.
* **≥80% blast‑radius reduction** in ≥2 drills.
* Zero SIS changes; zero Ring‑2 without approvals.
* Signed certificate for every actuation; evidence pack delivered; production recommendation.

---

## 19) Glossary

* **MTTD:** Mean time to detect coordinated anomaly.
* **MTTR:** Mean time to (micro‑)containment effective.
* **Ring‑1:** Pre‑authorized, reversible micro‑actuation with TTL.
* **Action Certificate:** Signed audit record of context → decision → actuation → outcome.
* **Pheromone:** Low‑bandwidth distributed elevation signal with quorum thresholds.
* **Twin‑first:** Rehearsal/validation in a digital twin before production changes.

---

## 20) Next Steps / Ask

* Select a **pilot site** (rack/pod). Schedule a 2‑hour discovery to confirm catalog and SLOs.
* Enable weekly KPI reporting; align on evidence pack format.
* Target an **8–12 week** pilot with clear go/no‑go criteria.