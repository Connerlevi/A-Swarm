# A-SWARM Autonomy Guardrails & Safety — Rules of Engagement
(See final one-pager shared; included here for repo control.)

- **Rings:** R0 Observe; R1 Micro-act (pre-auth, reversible); R2 Major (human-in-loop); RS Safety (never touched).
- **Twin-first:** All red/blue in the digital twin only.
- **Kill-switches:** Site-local halt, global suspend; floor/ceiling limits enforced locally.
- **Determinism:** Bounded resource ceilings; finite-state policy engine; time-sync for replay.
- **Security:** Signed/attested builds; PQC transport; HSM key custody; forward-secure logs.
- **Audit:** Signed Action Certificates for every Ring-1/2 act.
- **Compliance:** IEC 62443 / NIST 800-82 mappings; no hackback.
