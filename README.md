# A-SWARM — Autonomic Defense for AI Datacenters

**Version:** 2025-08-19

> **For AI assistants and new contributors:** See [CONTEXT.md](CONTEXT.md) for comprehensive project context and technical details.

A-SWARM provides distributed sentinels, a low-bandwidth *pheromone* consensus mesh, a digital-twin-first red/blue lab, a policy compiler with formal guardrails, and cryptographically signed **Action Certificates** for audit.

## Monorepo Layout
- `sentinel/` — ultra-light host/gateway agents (bounded CPU/RAM/egress)
- `pheromone/` — gossip/quorum, PQC transport, rate-limited signaling
- `twinlab/` — deterministic replay, injectors, sandboxed red/blue
- `policy_compiler/` — YAML → deterministic policy automata (micro-acts)
- `docs/` — guardrails, acceptance criteria, architecture, KPIs/SLOs
- `configs/` — example site + policy catalogs
- `schemas/` — Action Certificate JSON schema
- `docker/` — container builds for each service

## Quickstart (Sprint Zero Targets)
```bash
# (1) Create & activate a venv (Python 3.11+)
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# (2) Run unit tests (should pass/skipped in sprint zero)
pytest -q

# (3) Run components (dev mode)
python -m sentinel.cli --help
python -m pheromone.cli --help
python -m twinlab.cli --help
python -m policy_compiler.compiler --help
```

## Acceptance Criteria (Sprint Zero)
See `docs/ACCEPTANCE_CRITERIA.md`. These include:
- Sentinel v0.1 resource ceilings, packet/process sketching, health deltas
- Pheromone v0.1 gossip with PQC keys, DoS-safe rate limiting, quorum
- TwinLab v0.1 deterministic replay of 3 canned incidents, Action Certificate emission
- Policy Compiler v0.1 generating reversible micro-acts and proof placeholders

## License
MIT (see `LICENSE`).
