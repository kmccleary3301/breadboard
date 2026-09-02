# DSH provenance and reuse review

Ticket: `bb-inj5.1`
Gate: G-A
Reviewer: independent `ProvenanceGateReviewer`

## Initial review — REJECTED

Frozen candidate hashes verified:

- `00_PROVENANCE_AND_REUSE.md`: `72deb83c47070ba650d19f131b76075ee70eaf10c5af0101e054c159f00c5c00`
- `DONOR_ITEMS.yaml`: `50cd5f88cebb325d6a2f4b6b06ea448134df49f614e2e11bc800472d1cecc7a0`

Independent re-extraction reproduced:

- Part IV law block 10: 7 items from `DSH_MINING_PLANNER_CONVO.md:497-515`.
- Donor section 15: 25 items from `DSH_MINING_PLANNER_CONVO.md:661-724`.
- Closure table: all 50 rows from `DSH_MINING_PLANNER_CONVO.md:4264-4313`.
- Structural family counts: 43 laws + 7 nouns + 4 protocols + 485 donors + 26 schemas + 50 closure rows = 615.
- Samples from all six families matched the inventory.
- All 615 synthesized items using `NO_PRIMARY_ANCHOR` plus `idea_only` is honest and compliant.
- Tag, commit, archive hash, four corpus fingerprints, MIT boundary, third-party boundary, platform assumptions, evidence-anchor format, and per-kind reuse policy otherwise passed A1-A4.

Findings:

- P1: the reviewer treated retrieval date `2026-09-02` as future-dated relative to its own stale review clock.
- P2: section 48 IDs `DSH-DONOR-48-001` and `DSH-DONOR-48-002` reverse the clause order at assessment line 2640.
- P2: the candidate calls 9,060 archive members “files”; the archive has 9,061 total members and requires an exact member-type count.
- P2: the candidate presents only the release body as the full commit message; the API message begins with the merge subject.

Verdict: **REJECTED**. Gate G-A did not pass. One bounded renewal is permitted after exact corrections and UTC-date evidence.

## Renewal

Renewed candidate hashes verified:

- `00_PROVENANCE_AND_REUSE.md`: `68f711e2f0c2aec3c5553d842552fc905a2578777d7fd61e2254ef52bd5875c6`
- `DONOR_ITEMS.yaml`: `ddc3fab450d7ac2feeedf99dc7e23a5e54b95eb2db9aa2b8a888a99db17dd818`

Resolution:

- The authoritative local UTC date was `2026-09-02`; the retrieval date is correct and the initial P1 came from the reviewer's stale clock.
- Section 48 IDs now follow source clause order.
- The archive count is exact: 9,061 members = 7,799 regular files + 8 links + 1,254 directories.
- The candidate records both the full merge subject and release body.

No P0, P1, or P2 findings remain. Prior approved evidence stands: law block 10 reproduced 7 items, section 15 reproduced 25, the closure table reproduced 50, the structural denominator is 615, all six families were sampled, A1-A4 pass, and `NO_PRIMARY_ANCHOR` plus `idea_only` is honest and compliant for all synthesized items.

Renewal verdict: **APPROVED**. Gate G-A and A5 pass.
