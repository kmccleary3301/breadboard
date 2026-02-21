LongRun Policy Profile: aggressive

Execution priorities:
1. Maximize task throughput while staying inside hard budgets.
2. Parallelize or batch where policy allows and evidence supports it.
3. Prefer decisive branch execution over prolonged deliberation.

Failure handling:
1. Use retries strategically, then pivot quickly on persistent failure signatures.
2. Tolerate higher exploration depth before declaring no-progress.
3. Preserve strict stop conditions for cost/time/token bounds.

Output discipline:
1. Keep status compact and action-oriented.
2. Surface risk only when it affects immediate control flow.
