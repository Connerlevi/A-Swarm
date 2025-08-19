# Architecture (High Level)

```mermaid
flowchart LR
  subgraph Hosts & Gateways
    S1[Sentinel Agents] --- S2[Local Policy Engine]
  end
  S1 --> P[Pheromone Mesh]
  P -->|Quorum/Elevate| C[Cognition & Policy Kernel]
  C --> PC[Policy Compiler]
  PC --> AB[Actuation Bus (Ring-1 only)]
  AB --> S1
  subgraph TwinLab
    T1[Replay Engine] --> T2[Red/Blue Sandbox]
    T2 --> PC
  end
  S1 -. read-only .-> OT[PLC-adjacent Gateway]
```
