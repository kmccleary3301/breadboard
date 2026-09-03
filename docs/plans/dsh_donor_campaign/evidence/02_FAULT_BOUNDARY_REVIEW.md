# Independent review: fault-boundary evidence

Date: 2026-09-02
Ticket: `bb-inj5.3`
Verdict: **APPROVED**
Reviewed artifact SHA-256: `842a97206811b0d12d8519b81dfccb07c1d7feb9acafb9c7644ff9bbc4ab5536`
ENGINE head: `b3cacc7356244253305f8a6f84308a993485bfe2`
TUI head: `73d6e6f55a238fc9ff0486bbcc9ecffe85705715`
Reviewer: independent `reviewer` agent `FaultBoundaryReviewer`

## Raw-first reconstruction

- Permission: session start, input admission, `run_shell` call, `approval.requested(permission_1)`, `approval.resolved(reject)`, no `run_shell` result, one owned turn terminally cancelled for `user_requested`; clean client/engine shutdown.
- Replacement: authenticated idle PID 80208 killed; PID 80499 appeared with changed boot, instance, launch, and process-start identities; first unchanged recovery prompt produced `HTTP request failed (0)`; explicit retry completed; final state contained two terminal turns and two owned submissions, while the client transcript retained three user texts. No admission conflict or third durable admission appeared. The selected canonical event journal contained only the pre-crash accepted input, not the recovered turn.

## Coverage verdict

- C1: bounded gate cards, oracles, retry and stop rules present.
- C2: identity and cleanup supported by provenance, status, authority, and journey stores.
- C3: permission request, reject, terminal cancellation, no gated result, and cleanup reconstructed.
- C4: old/new identity, HTTP failure, unchanged explicit retry, durable final state, and journal discontinuity reconstructed; installed retry identity correctly remains `UNVERIFIED`.
- C5: only two discriminating cells executed; remaining S2 cells remain planned.
- C6: classifications supported; first-tranche consequence remains evidence-only.
- Gate G-C: passed.

No P0, P1, or P2 finding.

## Final exact-artifact review binding

Reviewed candidate SHA-256: `d875bfe875647ad9b67572642460312416006249e5b91b4a9ed607830f16392b`.

Independent verdict: **APPROVED**. Gate G-C passes. P0/P1/P2/P3 = 0/0/0/0. Confidence: 0.99.
