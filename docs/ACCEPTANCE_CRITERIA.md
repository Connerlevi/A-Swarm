# Sprint Zero Acceptance Criteria (DoD)

## Sentinel v0.1
- Enforces resource ceilings: CPU < 1% (idle), RSS < 50 MB, egress < 5 kB/s avg
- Emits compact health deltas: packet/process sketches; no raw payloads
- Local finite-state anomaly rules executable deterministically
- CLI: start/stop/status; structured logs

## Pheromone v0.1
- Gossip with fan-out control; per-node rate limiting to prevent storms
- PQC key handshake (interface stub); message auth & replay guards
- Quorum elevation for anomalies; partition-safe defaults
- CLI: peer add/list; metrics endpoint

## TwinLab v0.1
- Deterministic replay for 3 canned incidents with time-synced logs
- Sandbox harness for red/blue; no offensive ops on production
- Emits signed **Action Certificates** for simulated Ring-1 acts

## Policy Compiler v0.1
- YAML policy → deterministic automata → micro-act commands (iptables/VLAN/token revoke stubs)
- One-click simulate in TwinLab; revert TTL; proof artifact placeholder (.tla/.md)

## Integration v0.1
- Read-only adapters: Modbus/TCP, OPC UA (interface stubs)
- SIEM export: JSONL Action Certificates to file/HTTP sink

## Evidence
- PTP/NTP normalized timestamps for replay
- CI runs pytest suite (unit tests and schema validation)
