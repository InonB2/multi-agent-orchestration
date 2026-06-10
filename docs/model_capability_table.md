# Model Capability Table — Multi-Model Orchestration Stack

> Purpose: Reference for the orchestrator and all agents when deciding which AI model to use for a given task.  
> Stack: Claude Code (primary orchestrator) · OpenAI Codex CLI (fallback coder) · Google Antigravity CLI / agy (fallback researcher / planner / UI agent)

> **Note:** Model names in this document represent capability tiers and are illustrative examples — substitute the actual model IDs available in your environment. Benchmark figures are approximate and based on internal evaluations; verify against official benchmarks for your specific use case.

---

## 1. Primary Capability Table

**Rating key:**  
✅ Best — leading choice for this capability  
⚠️ Good — capable, acceptable quality  
🔸 Mediocre — works but not optimal; prefer another model  
❌ Poor — avoid for this capability

| Capability | Claude Code (Sonnet 4.6 / Opus 4.8) | OpenAI Codex CLI (GPT-5.3-Codex / GPT-5.5) | Google Antigravity (Gemini 3.5 Flash / 3.1 Pro) |
|---|---|---|---|
| **Multi-file reasoning & refactoring** | ✅ Best — SWE-bench Pro 64.3%; Agent Teams with subagents designed for this; best at holding large codebases in context simultaneously | ⚠️ Good — 58.6% SWE-bench Pro; cloud sandbox excels at isolated refactors but struggles on cross-cutting concerns | 🔸 Mediocre — 55.1% SWE-bench Pro; parallel agents help surface breadth but raw reasoning quality is lowest of three |
| **Single-file isolated code generation** | ⚠️ Good — high quality output but tends to over-engineer simple tasks; Sonnet is faster tier for this | ✅ Best — leads Terminal-Bench 2.0 at 82.7%; Rust CLI eliminates Node overhead; uses 3–4x fewer tokens per task; fastest for contained coding work | ⚠️ Good — Gemini 3.5 Flash very fast (289 tok/s) but quality gap vs Claude on nuanced single-file logic |
| **PR / adversarial code review** | ✅ Best — built-in `/security-review` skill; multi-stage verification; Opus 4.8 is 4x less likely than its predecessor to let flaws pass unflagged; adversarial-review skill on MCP Market | ✅ Best — "strongest security foundation" in independent tests; signed OAuth state protection, schema validation; adversarial review mode native to cloud sandbox | 🔸 Mediocre — weakest TypeScript coverage in head-to-head tests; review artifacts generated but depth of analysis is lower |
| **Unit test generation** | ✅ Best — high-quality tests with correct edge cases; integrates well with existing test framework conventions in multi-file context | ⚠️ Good — generates tests but more template-driven; less reasoning about edge cases in complex domain logic | ⚠️ Good — parallel agents can generate broad coverage quickly but individual test quality is lower |
| **Security code audit** | ✅ Best — dedicated `/security-review` command; checks SQLi, XSS, auth issues across full codebase; best for long cross-file attack surface analysis | ⚠️ Good — checks for common CWEs during PR generation; strong on OAuth/auth patterns; less comprehensive for full codebase audit | 🔸 Mediocre — no dedicated security audit tooling; Ghost Runtime can run scanners but no native deep security analysis mode |
| **Complex orchestration / coordination** | ✅ Best — Agent Teams, background subagents, hooks, MCP scoping, session checkpointing; designed as orchestrator; `SessionStore` for state | ⚠️ Good — async cloud tasks, worktrees, MCP orchestration, `codex exec` for non-interactive; strong but less feature-complete than CC | ✅ Best — Ghost Runtimes with up to 93 isolated parallel subagents demonstrated; Agent Manager with visual state; async subagents native |
| **Judgment calls (strategy, architecture, product decisions)** | ✅ Best — Opus 4.8 is the top-tier reasoning model; designed for hard problems; flags uncertainty; less likely to overengineer | ⚠️ Good — GPT-5.5 capable of planning but Codex CLI is optimized for execution, not strategic deliberation | 🔸 Mediocre — oriented toward fast parallel execution and building, not strategic analysis or architectural debate |
| **Writing quality (CVs, docs, posts, SOPs)** | ✅ Best — Claude consistently top-rated for prose quality, instruction following, and nuanced language; Hebrew supported | 🔸 Mediocre — code-focused model in a code-focused CLI; GPT not tuned for long-form writing in this context | ⚠️ Good — Gemini 3.5 Flash decent for structured writing; `agy` not purpose-built for writing workflows |
| **Long-document synthesis (100K+ tokens of input)** | ✅ Best — 1M token context window (Sonnet 4.6+ beta); designed for full-codebase analysis in one prompt; strong synthesis of disparate sources | 🔸 Mediocre — 200K context limit on GPT-5.3-Codex; hits ceiling on very large document sets; model-dependent improvements expected | ✅ Best — 1M input / 64K output tokens; dynamic subagents each get isolated windows preventing degradation; best for massive context tasks |
| **Research from multiple web sources** | ⚠️ Good — web search available via MCP; can synthesize but research is not the primary design goal of CC | 🔸 Mediocre — no integrated web research; code-first tool; relies on user providing context | ✅ Best — Chrome / Web MCP built in; `agy` CLI opts into browser use by default; search indexing in Ghost Runtime; designed for research-heavy agentic loops |
| **Browser / UI automation & E2E testing** | ❌ Poor — no integrated browser; requires external MCP server or Playwright tool to be explicitly added; significant setup overhead | 🔸 Mediocre — screenshot-to-code input supported; some multimodal; but no live browser agent for automation loops | ✅ Best — integrated Chromium instance in Ghost Runtime; visual verification before commit; dedicated browser subagent mode; the clear winner here |
| **Database schema + migrations** | ✅ Best — `database-migration-helper` skill; strong reasoning about schema changes, naming conventions, dry-run SQL; references existing patterns across codebase | ⚠️ Good — can scaffold migration files and run SQL; less systematic about cross-file convention checking | ⚠️ Good — virtual DB schemas available inside Ghost Runtime; Gemini 3.1 Pro handles DB logic well but less opinionated about conventions |
| **Visual / image analysis** | ✅ Best — Opus 4.8 has top-tier multimodal reasoning; strong at interpreting screenshots, diagrams, design mocks | ⚠️ Good — screenshot-to-code supported; multimodal input accepted; GPT-5.5 handles visuals competently | ⚠️ Good — Gemini 3.5 Flash multimodal capable (CharXiv Reasoning 84.2%); visual verification in IDE via Ghost Runtime; roughly comparable to Codex |
| **Speed (latency for small tasks)** | 🔸 Mediocre — Sonnet 4.6 faster than Opus but still ~67 tok/s; interactive CLI adds perceived latency; not optimized for quick sub-second turns | ⚠️ Good — async cloud execution; background daemon mode removes blocking; Rust CLI startup fast; 3–4x token efficiency keeps responses lean | ✅ Best — Gemini 3.5 Flash at ~289 tok/s (4x faster than other frontier models); fastest TTFT for small tasks in this comparison |
| **Cost efficiency (relative)** | 🔸 Mediocre — Sonnet 4.6: $3/$15 per MTok; Opus 4.8 higher; Max plan $100–200/month; becomes expensive under heavy agentic load | ⚠️ Good — GPT-5.3-Codex uses 3–4x fewer tokens per task reducing effective cost; Plus $20 / Pro $100; pay-as-you-go API available | ✅ Best — Gemini 3.5 Flash: $1.50/$9 per MTok; free in public preview (as of 2026-06-09); best $/task for high-volume work |
| **Context window size** | ⚠️ Good — 200K standard, 1M beta on Sonnet 4.6+; 200K confirmed on Microsoft Foundry for Opus 4.8 | 🔸 Mediocre — 200K on GPT-5.3-Codex; GPT-5.4 reaches 1M but not default in Codex CLI config | ✅ Best — 1M input / 64K output standard on Gemini 3.5 Flash; isolated per-subagent windows prevent degradation |
| **Mid-task checkpointing / resume support** | ✅ Best — native checkpointing; `claude --resume` reloads last transcript; `SessionStore` for external state; worktree refs preserved | ⚠️ Good — `--output-last-message` flag; `threadId` / worktree refs allow continuation; less seamless than CC | ⚠️ Good — `agy /resume` picks up from conversation history with workspace files intact; session identifier printed on exit; conversation export available |
| **Multi-file diff / patch application** | ✅ Best — coordinated multi-file patches in single session; strong at understanding diff context and applying cleanly; Agent Teams can parallelize | ✅ Best — cloud sandbox creates clean, structured PRs from diffs; worktrees native to workflow; GitHub PR integration documented | ⚠️ Good — Ghost Runtime can apply diffs and commit; PR generation less mature; best used when patches are artifacts of a broader build workflow |

### Benchmark reference table (2026-06-09)

| Benchmark | Claude Code (Opus 4.6) | Codex CLI (GPT-5.3-Codex / newer) | Antigravity (Gemini 3.5 Flash) |
|---|---|---|---|
| SWE-bench Verified | 80.8% | 88.7% (newer model) | 81.0% |
| SWE-bench Pro | 64.3% | 58.6% (GPT-5.3-Codex) | 55.1% |
| Terminal-Bench 2.0 / 2.1 | 65.4% | 82.7% | 76.2% |
| MCP Atlas | — | — | 83.6% |
| GPQA Diamond | 91.3% | — | — |
| Context window (tokens) | 200K / 1M beta | 200K | 1M / 64K out |
| Output speed (tok/s, approx) | ~67 | ~100–120 | ~289 |
| Cost per 1M in/out tokens | $3 / $15 (Sonnet 4.6) | API-based (model-dependent) | $1.50 / $9 (Flash) |

Sources: [morphllm.com/claude-benchmarks](https://www.morphllm.com/claude-benchmarks), [smartscope.blog/codex-vs-claude-code-2026-benchmark](https://smartscope.blog/en/generative-ai/chatgpt/codex-vs-claude-code-2026-benchmark/), [llm-stats.com/gemini-3.5-flash](https://llm-stats.com/blog/research/gemini-3.5-flash-launch), [kommunicate.io/blog/claude-code-vs-codex-vs-antigravity](https://www.kommunicate.io/blog/claude-code-vs-codex-vs-antigravity/), [thenewstack.io/claude-code-vs-cursor-vs-codex-vs-antigravity-2026](https://thenewstack.io/claude-code-vs-cursor-vs-codex-vs-antigravity-2026/)

---

## 2. Recommended Routing Rules

These rules are designed to be actionable by a Python router script. Each rule has a `condition`, a `target`, and a `reason`. When multiple rules match, apply the first matching rule in order.

**Rule 1 — Browser / UI / E2E testing**  
`IF task_type IN ["browser_automation", "e2e_test", "visual_verification", "screenshot_test"]`  
`THEN route_to = "antigravity"`  
Reason: Antigravity is the only tool with an integrated Chromium instance and Ghost Runtime. Claude Code requires external MCP setup and Codex has no live browser loop.

**Rule 2 — Architecture / strategy / judgment call**  
`IF task_type IN ["architecture_decision", "strategy", "product_decision", "system_design"] OR task_complexity == "high" AND task_type == "refactor"`  
`THEN route_to = "claude_code" AND model = "opus"`  
Reason: Opus 4.8 is the highest-reasoning model in this stack; 4x less likely to let flaws pass unflagged; designed for long-horizon hard problems.

**Rule 3 — Writing / documentation / CVs / posts**  
`IF task_type IN ["writing", "cv", "documentation", "blog_post", "sop", "linkedin_post"]`  
`THEN route_to = "claude_code"`  
Reason: Claude consistently leads on prose quality, instruction following, and multilingual output. Codex and Antigravity are code-first tools.

**Rule 4 — Single-file code generation or scripting**  
`IF task_type IN ["single_file_code", "script", "terminal_command", "automation_script"] AND task_complexity IN ["low", "medium"]`  
`THEN route_to = "codex"`  
Reason: Codex leads Terminal-Bench 2.0 at 82.7%; uses 3–4x fewer tokens; Rust CLI eliminates overhead. Best cost-performance for contained coding tasks.

**Rule 5 — Web research / multi-source synthesis**  
`IF task_type IN ["research", "web_research", "fact_finding", "competitive_analysis", "synthesis"]`  
`THEN route_to = "antigravity"`  
Reason: Antigravity has built-in Chrome and Web MCP; `agy` opts into browser by default; only tool in this stack designed for autonomous web research loops.

**Rule 6 — High-parallelism tasks (>5 concurrent subtasks)**  
`IF subtask_count >= 5 OR task_type == "parallel_build"`  
`THEN route_to = "antigravity"`  
Reason: Ghost Runtimes support isolated parallel subagents; demo showed 93 concurrent subagents; each gets its own context window, preventing degradation.

**Rule 7 — Rate limit fallback (primary agent throttled)**  
`IF primary_agent_status == "rate_limited" OR http_status == 429`  
`THEN:`  
`  IF last_task_type IN ["coding", "refactor", "script"] → route_to = "codex"`  
`  ELSE → route_to = "antigravity"`  
`  PRESERVE handoff_bundle (task_spec + repo_ref + relevant_files + acceptance_criteria)`  
Reason: Both tools can continue from a canonical handoff bundle; do not attempt raw transcript migration.

**Rule 8 — Cost-constrained / high-volume work**  
`IF budget_mode == "economy" OR daily_spend >= threshold`  
`THEN route_to = "antigravity" AND model = "gemini-3.5-flash"`  
Reason: Gemini 3.5 Flash at $1.50/$9 per MTok is 2–10x cheaper than Claude; free preview tier available as of 2026-06-09; 4x faster than other frontiers.

---

## 3. Update Protocol

### Who updates this table and when

**After every QA sign-off:**  
When a QA agent signs off on a completed task, they must add a one-line note to `/scratchpad/model_quality_log.md` in this format:  
`[DATE] TASK-ID | model=claude_code/codex/antigravity | expected=high/medium | actual=high/medium/low | note=<brief finding>`  
If `actual != expected`, flag to the orchestrator immediately — this is a candidate table revision.

**Trigger for immediate table revision:**  
- A model produces output that is significantly better or worse than its table rating on 2+ consecutive tasks of the same type  
- A new model version is released (e.g., Sonnet 4.7, GPT-5.4-Codex, Gemini 4.0)  
- A benchmark report covering any of the three tools is published with materially different numbers  
- A new capability is announced (e.g., Codex gains integrated browser; Claude Code gains native visual runner)

**Monthly review (first Monday of each month):**  
1. Check official changelogs for each of the three tools  
2. Run a web search for `"[model name] benchmark 2026"` for each of the three tools  
3. Update the benchmark reference table above with any new numbers  
4. Update capability ratings if a model has moved up or down on 2+ tasks  
5. Commit the update and log it in session notes

**What NOT to do:**  
- Do not update ratings based on a single task result  
- Do not remove a "Best" rating unless you have a benchmark or 3+ QA log entries showing degradation  
- Do not add a new row for a capability unless the orchestrator approves it as a standing routing dimension

---

Last updated: 2026-06-09
