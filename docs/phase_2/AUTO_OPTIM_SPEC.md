Below is a **complete, concrete design** for RayCode’s optimization and RL stack: what to measure, how to scalarize it, where to source signals (benchmarks + live runs), how to integrate with your existing v3 tool‑calling stack and A/B harness, and how to run black‑box HPO vs. prompt‑evolution/RL loops.

I’ve aligned the plan to the artifacts you already have (execution policy, EnhancedDialectManager, telemetry DB, A/B testing, diff formats, LSP loop, etc.) and I cite your spec where those pieces live so you can wire this in directly.   

---

## 0) Executive summary

**Goal.** For each popular model, automatically converge on a *model‑specific* configuration (system prompt, format choices, execution policy knobs, tool schemas, retry rules) that maximizes end‑to‑end coding performance **and** operational reliability/cost.

**Method.**

* **Signals:** Rich, shaped rewards collected per turn and per task across four phases of the agentic loop: **tool‑call validity → diff application → code health (LSP) → functional outcome (tests)**, with efficiency penalties (tokens, latency) and policy adherence bonuses (e.g., “one‑bash‑per‑turn”). Your v3 spec already logs the critical fields and enforces the needed policies; we’ll reuse that. 
* **Benchmarks:** Combine *public* suites for **code editing/bug‑fix** and **tool‑calling** with *internal* micro‑benches for diff brittleness and laziness. (SWE‑bench / SWE‑bench‑Live, Defects4J, BugsInPy, QuixBugs, BigCodeBench, LiveCodeBench; BFCL/ToolScan for tool‑use.) ([arXiv][1], [GitHub][2], [J Koppel][3], [Gorilla][4])
* **Optimization loops:**

  * **Black‑box HPO** (your Weights & Biases Bayesian Hyperband) over prompts + format selection + execution policy.
  * **Prompt optimization / RL:** A *prompt‑evolver* (small Qwen3, as you proposed) that edits candidate system prompts using reflective mutations. Recent work (GEPA) shows reflective prompt evolution can outperform GRPO‑style RL in sample efficiency—perfect for expensive agent episodes. ([arXiv][5], [Hugging Face][6])

---

## 1) The signals: a **unified reward model** for agentic coding

Design each turn as a short “episode step” with dense shaping, and each task as an “episode” whose return aggregates those steps. The reward is **modular** so you can use it in both HPO and RL.

### 1.1 Phase‑wise metrics (emitted per turn by the execution layer)

**Phase A — Tool‑calling correctness (format‑agnostic).**

* **Schema/format validity score (SVS):** 1.0 if provider‑native tool call passes schema validation (OpenAI Structured Outputs / Anthropic tool\_use), else \[0,1] based on JSON/XML repair cost. (You already route native tools and prompt formats via EnhancedDialectManager; log validation success + repair count.)
* **Call args correctness (ACS):** % of required args present & type‑correct (strict schemas recommended in your OpenAI adapter).
* **Call plan compliance (CPS):** 1.0 if call ordering follows your validator (file ops before bash; ≤1 bash), else 0 with penalty. Your Sprint‑8 tasks include adding the “one‑bash‑per‑turn” rule and enforced ordering.

**Phase B — Diff/patch application (edit reliability).**

* **Patch apply success (PAS):** 1.0 if patch applies cleanly, 0 if not.
* **Hunk match ratio (HMR):** matched\_hunks / total\_hunks (for partial credit) + **fuzzy salvage bonus** if your AST‑anchored fallback salvages failed hunks (you recommended AST anchoring as a pragmatic fallback).
* **Add‑file correctness (AFC):** correct use of `write_file` or `opencode_patch` for creation events; penalize misuse of SEARCH/REPLACE for new files. (You’ve specified the add‑file strategy.)

**Phase C — Code health / static feedback.**

* **LSP error delta (LED):** normalized reduction in `Error` diagnostics after edit (pre vs post). Positive if errors drop, negative if errors increase; bounded to \[-1, 1]. (You already route diagnostics after writes in Sprint‑8.)
* **Syntax/build status (SBS):** quick parsers/linters/build exit code mapped to \[0,1].

**Phase D — Functional outcome.**

* **Test pass fraction (TPF):** (# tests passed)/(# total) for the task’s unit/integration tests. Grants fine‑grained feedback beyond binary success.
* **Regression guard (RG):** if the task begins from a passing baseline, reward no regression (1.0) and penalize failures introduced.

**Phase E — Efficiency & quality of interaction.**

* **Token efficiency (TE):** 1 – min(1, total\_tokens / budget\_tokens).
* **Latency efficiency (LE):** 1 – min(1, latency\_ms / p95\_baseline\_ms). Your telemetry schema already includes tokens + latency.
* **Tool economy (TOE):** decreasing function of tool\_calls\_used; encourage fewer, higher‑quality calls.
* **Safety & permissions adherence (SPA):** +1 per turn when risky tools obey permission policy; –1 on violation (you codified ask/allow/deny).

**Phase F — Format/model fit (for adaptive selection).**
Record **per‑format success rates** by model (e.g., `udiff` vs `aider_diff`). Your telemetry & A/B design already anticipate these dashboards. 

> **Why these phases?** They map one‑to‑one onto your v3 stack’s control points (dialect selection, validator, executor, LSP diagnostics, testing) and to industry benchmarks that isolate tool use vs. editing vs. outcome.

---

### 1.2 Scalarization: from many metrics to one reward

For each metric $m$, compute a normalized score $\hat m \in [0,1]$ (or $[-1,1]$ for deltas). Maintain **rolling baselines** per model+project to normalize latency and token costs.

**Per‑turn shaped reward**

$$
R_{\text{turn}} = 
w_S \cdot \mathrm{SVS} + 
w_A \cdot \mathrm{ACS} + 
w_C \cdot \mathrm{CPS} + 
w_P \cdot (0.7\cdot \mathrm{PAS} + 0.3\cdot \mathrm{HMR}) + 
w_L \cdot \mathrm{LED} +
w_B \cdot \mathrm{SBS} + 
w_T \cdot \mathrm{TPF}_{\Delta} + 
w_E \cdot (0.5\cdot \mathrm{TE} + 0.5\cdot \mathrm{LE}) +
w_O \cdot \mathrm{TOE} +
w_X \cdot \mathrm{SPA}
$$

* $\mathrm{TPF}_{\Delta}$ is the *incremental* change in pass fraction this turn (dense shaping toward the goal).
* Start with weights: $w_P = w_T = 2.0$; $w_S=w_A=0.5$; $w_C=0.5$; $w_L=1.0$; $w_B=0.5$; $w_E=0.25$; $w_O=0.25$; $w_X=0.25$. Tune via HPO.

**Per‑task terminal reward**

$$
R_{\text{task}} = \mathbb{1}[\mathrm{all\ tests\ pass}] \cdot 5 \;+\; \mathrm{TPF}_{\mathrm{final}} \cdot 2 \;+\; \text{win\_rate\_vs\_baseline} \cdot 1 \;-\; \text{normalized\_cost} \cdot 1
$$

Then **episode return** is $\sum R_{\text{turn}} + R_{\text{task}}$.
This gives abundant signal even when tests don’t fully pass (LSP deltas, partial diff success, etc.).

---

## 2) Benchmarks & adapters (plug directly into RayCode)

You want *both* skill‑isolated eval (tool use, small edits) and end‑to‑end software dev. Bind them behind a single **RayCodeBenchmarkAdapter** interface that reports the Phase metrics above.

### 2.1 Public suites (recommended core set)

* **SWE‑bench + SWE‑bench‑Live** for real‑repo bug fixing with executable tests (agent + model). Use `TPF`, `PAS/HMR`, `LED`, cost/latency. SWE‑bench‑Live reduces contamination and keeps tasks fresh. ([swebench.com][7], [arXiv][1], [Microsoft][8])
* **BigCodeBench** for richer, library‑heavy function tasks with strong test suites (fine‑grained `TPF`). ([arXiv][9])
* **LiveCodeBench** for contamination‑resistant function‑level coding + self‑repair signals. ([arXiv][10], [sky.cs.berkeley.edu][11])
* **HumanEval+ / MBPP+ (EvalPlus)** for cheap, high‑volume *dense* rewards via many tests per task. Track pass\@k and *per‑turn TPFΔ*. ([GitHub][12], [evalplus.github.io][13])
* **BFCL (Berkeley Function‑Calling Leaderboard)** for tool‑calling reliability across single/multi‑turn/parallel calls; use **SVS/ACS/CPS** as core metrics. ([Gorilla][4])
* **ToolScan / SpecTool** to categorize tool‑use failure types and score *error‑aware* improvements (e.g., wrong arguments, extraneous calls) for **ACS/CPS** shaping. ([arXiv][14])

### 2.2 Classic bug‑fix corpora (great for LSP + diff shaping)

* **Defects4J** (Java), **BugsInPy** (Python), **QuixBugs** (both). Provide many failing‑test setups, perfect for LED & TPF shaping and for measuring diff brittleness on non‑toy repos. ([GitHub][2], [arXiv][15], [J Koppel][3])

### 2.3 Internal micro‑benches (fast daily checks)

* **Aider “laziness” refactor set** to stress **diff format selection**; track diff‑induced success with GPT‑4‑o‑like vs Claude‑like models. (Unified diff substantially reduced lazy coding in GPT‑4 Turbo.) ([Aider][16], [GitHub][17])
* **Diff brittleness suite:** synthetic edits with line offsets, context drift, file adds/deletes, mixed encodings; score with **PAS/HMR** and **AST salvage** rate. (Matches your recommendations.)

> Your blueprint already lists **what to A/B** (udiff vs aider\_diff, one‑bash‑per‑turn, micro‑plan, etc.) and where to record it (performance DB, dashboards). We’ll wire the adapters to emit those fields to the same DB.  

---

## 3) From signals to **decisions**: HPO → Adaptive selection → RL/prompt evolution

### 3.1 Black‑box HPO (W\&B Bayesian Hyperband)

**Search space (examples):**

* **Prompts:** system prompt template choice; presence/wording of “one bash per turn”; diff authoring rules per dialect; micro‑planning on/off + trigger threshold. (All supported by your prompt compiler.)
* **Dialects & policies:** default diff format per model; fallback order; retry counts; read‑before‑edit strictness; `max_tools_per_turn`; bash timeout.
* **Provider params:** temperature, parallel tool calls on/off, *strict* schemas (OpenAI), XML prefill (Anthropic).

**Objective:** maximize **episode return** (above) on a sampled benchmark slice *under a cost cap*. Report **win‑rate vs. baseline** to reduce variance across seeds and tasks.

**A/B integration:** Keep your “sticky user → variant” policy for live traffic; promote winners after reaching power. (You already wrote this design.)

### 3.2 Adaptive selection at runtime (per‑model, per‑tool)

Your EnhancedDialectManager already caches format success by model/provider. Move to **sliding time‑window** estimates and pick the arg‑max format with **bandit exploration** (e.g., Thompson/UCB on per‑format PAS×LED×TPF). Include **fallback chains** on failure.

### 3.3 Optimizing the **system prompt**: reflective evolution > RL when samples are expensive

Use a *small Qwen3 prompt‑evolver* that takes: (current prompt, recent failure traces: tool‑call error types, failed hunks, LSP diagnostics, test diffs, cost/latency) → proposes *edits* to the system prompt.

* **Search algorithm:**

  * **GEPA‑style reflective prompt evolution**—optimize text via reflection and edit operators (insert, delete, re‑order rules; strengthen constraints; add provider/model‑specific diff guidance). Recent results show reflective prompt evolution can outperform GRPO with far fewer rollouts on held‑out tasks. ([arXiv][5])
  * Maintain a **population** (μ+λ). Keep Pareto‑front w\.r.t. (return, cost). Periodically **distill** the top prompt into your `SystemPromptCompiler` canonical template (cached by hash).
* **When to use RL:** If you later fine‑tune a *policy model* (not just a prompt), use **GRPO/GRMT** with our shaped rewards on *short tasks* (HumanEval+/MBPP+) to learn better tool sequencing. For prompts only, prefer black‑box search/evolution for sample efficiency.

---

## 4) Exact signals for **each benchmark family**

| Family                              | Primary metrics → shaping                                                                   | Notes                                                                                                                     |
| ----------------------------------- | ------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| **BFCL (tool‑use)**                 | SVS, ACS, CPS; Response‑format penalties; Latency/Token efficiency                          | Covers single/multi‑turn, parallel calls; great for **tool planner** quality. ([Gorilla][4])                              |
| **ToolScan/SpecTool**               | Error‑aware ACS/CPS shaping by error taxonomy (wrong func, extra func, arg mismatch, order) | Use per‑category confusion matrix as diagnostics; training/eval split. ([arXiv][14])                                      |
| **Aider “laziness”**                | PAS, HMR, TPF on refactor tests                                                             | Use it to validate **udiff vs aider\_diff** per model; your doc notes big gains for GPT‑4 Turbo with udiff. ([Aider][16]) |
| **SWE‑bench(‑Live)**                | PAS/HMR, LED, SBS, TPF; cost/latency                                                        | Use *Live* for contamination‑resistant tracking; include **win‑rate vs last release**. ([arXiv][1], [Microsoft][8])       |
| **BigCodeBench / LiveCodeBench**    | Dense **TPFΔ** shaping (many tests); TE/LE                                                  | Good for lower‑variance prompt/HPO loops. ([arXiv][9])                                                                    |
| **Defects4J / BugsInPy / QuixBugs** | LED, SBS, TPF; plus diff brittleness                                                        | Probes compiler/linter/LSP feedback loop quality. ([GitHub][2], [arXiv][15], [J Koppel][3])                               |

---

## 5) Wiring it into **RayCode v3** (drop‑in)

**Where to log:** Use your `performance_monitoring.database_path` (SQLite by default) to store all per‑turn metrics above; add columns for PAS, HMR, LED, SBS, TPF, TE/LE, TOE, SPA. You already store selected format, model id, error type, latency, tokens and support by‑format/by‑model reports.

**Where to enforce:**

* Execution policy (file ops → bash, ≤1 bash, blocking diffs) is already in spec + Sprint‑8 tasks; emit CPS and SPA directly from the validator/executor.
* Diff formats: you already support `unified_diff`, `aider_search_replace`, and `opencode_patch` + add‑file tactic. Emit PAS/HMR/AFC in the patcher. 
* LSP loop: on write, record LED, attach diagnostics to turn results (called out in Sprint‑8).

**Dashboards:** Your blueprint already outlines the exact widgets (success by dialect, error taxonomy, p95 latency, token cost). Add *new* widgets for **HMR**, **LED**, **TPFΔ**.

---

## 6) Default **reward config** (editable YAML)

```yaml
reward_v1:
  weights:
    SVS: 0.5
    ACS: 0.5
    CPS: 0.5
    PAS: 1.4
    HMR: 0.6
    LED: 1.0
    SBS: 0.5
    TPF_DELTA: 2.0
    TE: 0.125
    LE: 0.125
    TOE: 0.25
    SPA: 0.25
  terminal:
    pass_all_bonus: 5.0
    final_tpf_weight: 2.0
    winrate_weight: 1.0
    normalized_cost_weight: -1.0
  normalization:
    latency_p95_window_days: 7
    token_budget_by_task_type:
      refactor: 12_000
      bugfix: 18_000
      function: 6_000
  penalties:
    invalid_diff_block: -0.5
    add_file_via_diff: -1.0
    multi_bash_violation: -1.0
```

This lives next to your config and is consumed by both the **HPO harness** and the **RL/evolver** loop.

---

## 7) HPO: Weights & hyper‑params to sweep (Bayesian Hyperband)

Example knobs with good ROI:

* **System prompt toggles:** include/exclude micro‑plan; vary the precise wording of your diff guidelines (your §2.5 has excellent udiff/aider prompts to mutate).
* **Dialect policy:** default format per model (per Table 2.1), fallback chains, retry counts. (Use A/B and the time‑window cache you spec’d.) 
* **Validator strictness:** `read_before_edit=warn|error`, `one_bash_per_turn=true`, reordering on/off.
* **Provider settings:** enable parallel tool calls (OpenAI), XML prefill (Anthropic), `strict: true` schemas.

**Objective in W\&B:** `maximize mean(R_task + sum R_turn)` with constraints: `avg_cost_per_task <= cap` and `latency_p95 <= target`.

---

## 8) Prompt evolution / RL: the concrete loop

**Controller:** `PromptEvolver(Qwen3)`.
**State:** last K runs for a model (top failures, error taxonomy from ToolScan categories, failed hunk contexts, LSP messages, partial test diffs, efficiency stats).
**Action:** propose *edits* (not whole prompt) to your cached prompt template (your compiler supports persistent system prompts by content hash).
**Mutation ops:**

* Insert/remove/reword a *rule* (e.g., “Do **not** include line numbers in udiff hunks; provide 3–5 lines of context”).
* Flip a *policy* hint (“one bash per turn”, “file ops before shell”).
* Dialect‑specific guardrails (aider SEARCH/REPLACE exact‑match emphasis vs. udiff high‑level hunks).

**Selection:** μ+λ with **bandit‑style allocation** of episodes to promising prompts; archive Pareto‑front (return vs. cost).
**Why:** GEPA shows reflective prompt optimizers can learn faster than GRPO at comparable final quality—crucial when each evaluation is an expensive agent run. ([arXiv][5], [Cool Papers][18])

> If/when you train a small policy model for tool sequencing, wrap our shaped rewards into GRPO/GRMT; start on HumanEval+/MBPP+ for sample efficiency. ([GitHub][12], [evalplus.github.io][13])

---

## 9) Acceptance thresholds & guardrails (make regressions obvious)

Borrow Sprint‑8 success criteria and extend with reward deltas:

* **End‑to‑end task:** “create fibonacci module + tests” passes under default config. (You already use this.)
* **Regression gates:** Require *no drop* in **PAS**, **LED**, **TPF** vs previous release on a canary subset before promoting a new prompt/dialect default.
* **Live A/B:** Start 90/10, then 50/50; compute **power** and **p‑values** as in your A/B section, then flip the default when significant.

---

## 10) Implementation map (drop into your tree)

1. **Metrics layer**

   * Add a `scorers/` module that computes SVS/ACS/CPS/PAS/HMR/LED/SBS/TPF/TE/LE/TOE/SPA from executor+LSP+test outputs; write to `enhanced_tool_calling_performance.db`. (Fields already listed in §10).
2. **Benchmark adapters**

   * `adapters/swe_bench.py`, `adapters/bfcl.py`, `adapters/aider_lazy.py`, `adapters/defects4j.py`, etc. Each returns per‑turn metrics and final TPF.
3. **Reward API**

   * `reward/aggregator.py` loads YAML weights, returns per‑turn and per‑task scalar rewards + a breakdown for W\&B logging.
4. **HPO harness glue**

   * A single entrypoint `raycode_hpo.py` that samples a config, runs N tasks from a mixed pool (budgeted), and logs aggregate reward, win‑rate vs controlled baseline, cost.
5. **Prompt evolver**

   * `prompt_evolver/reflective.py` (population manager + Qwen3 editor); persists candidate prompts in your prompt cache keyed by content hash (your compiler already supports caching).
6. **Adaptive DialectManager**

   * Implement sliding time‑window success stats + Thompson sampling over formats per (provider, model, tool). (Matches your §7.3 recommendation.)

---

## 11) Model‑specific defaults you can ship *today* (then let the system adapt)

Based on your research summary and external evidence:

* **OpenAI GPT‑4 Turbo/‑o family:** default **Unified Diff**, explicit high‑level hunks, no line numbers; native Structured Outputs for tools. (Unified diff **reduces lazy coding** markedly.)  ([Aider][16])
* **Anthropic Claude 3/4 Opus:** default **Aider SEARCH/REPLACE**, whole‑file as last resort; Anthropic XML `tool_use`.
* **Gemini 1.5/2.0:** **`diff-fenced`** variant; treat fencing strictly; use companion `write_file` for add‑file.

Let the **bandit manager** continuously confirm/override these with live telemetry.

---

## 12) Why these choices are *state‑of‑the‑art‑aligned*

* **Tool‑use**: BFCL has become the de‑facto reference for function‑calling; v3/v4 expand to multi‑turn, memory, and format sensitivity, which match your goals. ([Gorilla][4])
* **Editing**: The Aider benchmark demonstrates concrete, large effects of **diff format** on GPT‑4 Turbo laziness; your design embraces per‑model diff selection. ([Aider][16])
* **Bug‑fix realism**: SWE‑bench‑Live and SWE‑rebench address contamination; use them to keep evaluation honest. ([arXiv][1])
* **Prompt optimization at scale**: GEPA indicates **reflective prompt evolution** can beat GRPO with far fewer rollouts—ideal for agentic eval where each episode is costly. ([arXiv][5], [Hugging Face][6])

---

## 13) Risks & mitigations

* **Overfitting to static benchmarks** → Always include **SWE‑bench‑Live** plus weekly rotating “fresh” tasks; require **win‑rate vs. baseline** on a hold‑out slice before adoption. ([arXiv][1])
* **Prompt churn / non‑stationarity** → Use **time‑window caches** (7–14 days) and slow‑roll A/B.
* **High variance across repos** → Normalize by baseline; use **paired evaluation** per task (control vs candidate) and report *delta reward*.
* **Latency/cost creep** → Hard budget in reward; penalize token/latency via TE/LE; upgrade winners only if **cost‑normalized** reward improves.

---

## 14) What you’ll see in practice

* **Tuning diffs by model** raises edit success and reduces retries (PAS/HMR ↑) and LSP errors (LED ↑). Your Sprint‑8 work on validator + LSP loop makes those wins stick. 
* **Prompt evolver** tends to: strengthen exact‑match language for Aider blocks (Claude), add context lines & avoid line numbers for udiff (GPT‑4‑o), insert explicit “add‑file → `write_file`” guidance (all). These are exactly the rules you documented. 

---

### Appendix A — Minimal Python interface sketch (drop‑in)

```python
# reward/aggregator.py
@dataclass
class TurnSignals:
    svs: float; acs: float; cps: float
    pas: float; hmr: float
    led: float; sbs: float
    tpf_delta: float
    te: float; le: float; toe: float; spa: float

def turn_reward(sig: TurnSignals, w: Dict[str, float]) -> float:
    return (
        w["SVS"]*sig.svs + w["ACS"]*sig.acs + w["CPS"]*sig.cps +
        w["PAS"]*sig.pas*0.7 + w["HMR"]*sig.hmr*0.3 +
        w["LED"]*sig.led + w["SBS"]*sig.sbs +
        w["TPF_DELTA"]*sig.tpf_delta +
        w["TE"]*sig.te + w["LE"]*sig.le + w["TOE"]*sig.toe + w["SPA"]*sig.spa
    )

def terminal_reward(pass_all: bool, final_tpf: float, winrate: float, norm_cost: float, tw):
    return (tw["pass_all_bonus"] if pass_all else 0.0) + \
           tw["final_tpf_weight"]*final_tpf + \
           tw["winrate_weight"]*winrate + \
           tw["normalized_cost_weight"]*norm_cost
```

---

### Appendix B — Example W\&B sweep keys

* `prompt.micro_plan: [true,false]`
* `prompt.udiff_context_lines: [3,4,5,6]`
* `diff.default_format_by_model.gpt4o: ["udiff","aider_diff"]`
* `validator.read_before_edit: ["warn","error"]`
* `executor.bash_timeout: [30,45,60]`
* `openai.tools.strict: [true,false]`
* `weights.TPF_DELTA: [1.0,1.5,2.0,2.5]` (reward shaping)

---

If you want, I can turn this into:

* a ready‑to‑run **`reward_v1.yaml`**,
* a **RayCodeBenchmarkAdapter** stub for SWE‑bench/BFCL/Aider‑lazy,
* and a **W\&B sweep** file that matches your harness’ parameter space.

But even without that, you can implement the scorers and aggregator exactly where your spec and Sprint‑8 plan already expect them (validator, executor, dialect manager, telemetry DB, A/B), then flip on HPO and the prompt‑evolver to start learning *per‑model* defaults automatically. 

[1]: https://arxiv.org/abs/2505.23419?utm_source=chatgpt.com "SWE-bench Goes Live!"
[2]: https://github.com/rjust/defects4j?utm_source=chatgpt.com "GitHub - rjust/defects4j: A Database of Real Faults and an Experimental ..."
[3]: https://jkoppel.github.io/QuixBugs/quixbugs.pdf?utm_source=chatgpt.com "QuixBugs: A Multi-Lingual Program Repair Benchmark Set Based on the ..."
[4]: https://gorilla.cs.berkeley.edu/leaderboard.html?utm_source=chatgpt.com "Berkeley Function-Calling Leaderboard"
[5]: https://arxiv.org/abs/2507.19457?utm_source=chatgpt.com "[2507.19457] GEPA: Reflective Prompt Evolution Can Outperform ..."
[6]: https://huggingface.co/papers/2507.19457?utm_source=chatgpt.com "Paper page - GEPA: Reflective Prompt Evolution Can Outperform ..."
[7]: https://www.swebench.com/SWE-bench/?utm_source=chatgpt.com "Overview - SWE-bench documentation"
[8]: https://www.microsoft.com/en-us/research/publication/swe-bench-goes-live/?utm_source=chatgpt.com "SWE-bench Goes Live! - Microsoft Research"
[9]: https://arxiv.org/abs/2406.15877?utm_source=chatgpt.com "BigCodeBench: Benchmarking Code Generation with Diverse Function Calls and Complex Instructions"
[10]: https://arxiv.org/abs/2403.07974?utm_source=chatgpt.com "LiveCodeBench: Holistic and Contamination Free Evaluation of Large Language Models for Code"
[11]: https://sky.cs.berkeley.edu/project/livecodebench/?utm_source=chatgpt.com "LiveCodeBench – UC Berkeley Sky Computing Lab"
[12]: https://github.com/evalplus/humanevalplus_release/releases?utm_source=chatgpt.com "Releases · evalplus/humanevalplus_release - GitHub"
[13]: https://evalplus.github.io/?utm_source=chatgpt.com "Benchmarks by EvalPlus Team"
[14]: https://arxiv.org/abs/2411.13547?utm_source=chatgpt.com "SpecTool: A Benchmark for Characterizing Errors in Tool-Use LLMs"
[15]: https://arxiv.org/pdf/2401.15481?utm_source=chatgpt.com "BugsInPy: A Database of Existing Bugs in Python Programs to Enable ..."
[16]: https://aider.chat/docs/unified-diffs.html?utm_source=chatgpt.com "Unified diffs make GPT-4 Turbo 3X less lazy - aider"
[17]: https://github.com/Aider-AI/refactor-benchmark?utm_source=chatgpt.com "GitHub - Aider-AI/refactor-benchmark: Aider's refactoring benchmark ..."
[18]: https://papers.cool/arxiv/2507.19457?utm_source=chatgpt.com "GEPA: Reflective Prompt Evolution Can Outperform Reinforcement Learning ..."
