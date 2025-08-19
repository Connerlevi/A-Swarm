# Sprint Zero Plan (2 Weeks)

## Team
- Systems/Agent Eng (owner: S1)
- NetSec/Crypto Eng (owner: N1)
- Data/Replay Eng (owner: D1)
- PM/Compliance (owner: PM)
- (Optional) UI/Docs (owner: U1)

## Goals
- Sentinel v0.1, Pheromone v0.1, TwinLab v0.1, Policy Compiler v0.1 operational
- CI green; evidence artifacts for acceptance criteria

## Week 1 — Sprint 1
- S1: Sentinel agent skeleton with resource ceilings, health deltas (packet sketch/process graph)
- N1: Pheromone gossip with rate limiting; quorum elevation stub; PQC key interfaces
- D1: TwinLab replay of 3 canned incidents; Action Certificate emission
- PM: Guardrails finalized; KPIs & SLOs baselined; Pilot LOI template finalized

**Demos/DoD**
- Run `python -m sentinel.cli run --sample 3` prints health deltas
- `python -m pheromone.cli metrics --events 10` shows rate_ok true; quorum CLI works
- `python -m twinlab.cli replay incident-a incident-b` returns deterministic outputs
- `python -m twinlab.cli cert` emits Action Certificate (JSON)

## Week 2 — Sprint 2
- S1: Local finite-state anomaly rules; structured logs; config loader
- N1: Gossip peer mgmt; metrics endpoint; partition-safety defaults
- D1: Policy Compiler translating YAML → micro-acts; twin simulate & revert TTL
- PM: Compliance pack v0 (IEC 62443 mapping); Pilot report template

**Demos/DoD**
- `policy_compiler` emits commands with max TTL aggregation
- Canary policy push simulation in twin; rollback on violation
- CI green; evidence pack generated

## Risks & Mitigations
- Integration drift → lock example configs; freeze interfaces in sprint zero
- Over-engineering → ship stubs with acceptance tests; iterate
- Security scope creep → enforce guardrails; Ring-1 only
