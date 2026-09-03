# W3.2 / GEN-INTERNAL

## Result

Implemented at existing Session/Lock and SessionService owners; no generic Generation Runtime, public schema, SDK, DIT, or DIT-GENERATION surface was added. A Session now pins its effective Lock hash by default, records the ordered Lock sequence, and derives immutable trajectory segment identities from the durable `session.started`/`session.reconfigured` order. Explicit adoption is quiescent-only at the runner owner and returns typed `GenerationAdoptionError` refusals (`non_quiescent` or `incompatible`). Candidate reconfiguration is published only after the owner accepts the Lock-compatible candidate. Stop closes per-session turn admission before cancellation/teardown; the lifecycle task is joined before disposal completes. Release remains owned by SessionRunner/SessionService and registry owners.

## Focused proof surface

`tests/product/runtime/test_session_kernel.py::test_generation_identity_is_pinned_and_reconstructed_from_durable_order` checks pinned identity, ordered Lock sequence, collision-safe durable segment identity, adoption reason/effective sequence, and restart reconstruction. Focused service tests cover atomic non-quiescent refusal with rollback, candidate-publication rollback, typed incompatible Lock rejection, admission closure before teardown, terminalization races, and role-command routing. Verification: 245 focused cases passed.

## E1.3 disposition

`followup` / `steer` / `inject` delivery modes remain HYPOTHESIS with no code. FT-02 names no delivery-mode consumer, and W7 does not name one; the criterion is unreachable in this tranche without a named consumer and PROC-AMEND/DIT route. W10.2 should record `hypothesis-resolved W3.2: no consumer; closed`.

## Module card

Module:        Session generation pin/adoption · Workstream/node: W3.2 / GEN-INTERNAL
Callers:       `SessionService.execute_command` → `SessionRunner.transition_product_session`; `Session.restore` reconstruction
Deleted:       Replaced direct `Session.reconfigure` publication in `SessionRunner.transition_product_session` with the typed adoption gate; removed duplicate terminal-transition branch after the cutover; added the focused generation/restart test at the Session owner.
Test surface:  `Session.start`, `Session.adopt_generation`, `Session.restore`, and runner transition/admission locks.
Deletion test: Without this owner logic, mid-turn reconfiguration is accepted, Lock identities are not tied to durable trajectory segments, and stop can race new turn admission.
Laws touched:  E1.3, E1.10, E1.11, E2.10, E4.4, E4.5, E4.7, E4.8, E4.9 · Public impact: none
