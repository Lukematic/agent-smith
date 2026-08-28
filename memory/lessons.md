# Lessons — binding prevention rules

**Append-only.** Never edit a line in place. To revise, mark the old line
`[SUPERSEDED yyyy-mm-dd]` and append the new one below it.

These rules are loaded at every session start and **override defaults**. Each
line names the failure mode it prevents and the surface it lives on.

Format:

```
- [yyyy-mm-dd] `FAILURE_MODE` — the rule, stated as an imperative. (surface: prompt|model|context|tools)
```

---

## Rules

- [2026-08-21] `KNOWLEDGE_FORK` — install Agent Smith once at user scope using the active harness integration. Repositories get only a minimal pointer, never a private copy of Smith's knowledge base. (surface: context)
- [2026-08-21] `SCOPE_INVERSION` — rules and knowledge are global; findings and worklists are local. A repo's test command is not doctrine. (surface: context)
- [2026-08-21] `SINGLE_WRITE` — every `remember_memory` call is mirrored to a file under `memory/`. Memory MCP is a cache; the files are the ledger. On disagreement, the file wins. (surface: context)
- [2026-08-21] `LINTER_FALSE_POSITIVE` — a validator that flags correct artifacts gets ignored, which is worse than no validator. Checks that cannot apply must report SKIP, never FAIL. (surface: tools)
- [2026-08-21] `IDENTITY_VS_SUBJECT` — when checking capability constraints, read the artifact's name and description, not its body. A skill that *authors* orchestrators legitimately needs Write; one that *is* an orchestrator does not. (surface: tools)
- [2026-08-21] `UNTESTED_VALIDATOR` — a linter is not verified by passing artifacts. Run it against a deliberately broken artifact and confirm it blocks with a nonzero exit. (surface: tools)
- [2026-08-21] `APPENDIX_EXPLOSION` — index chapters at file granularity and appendix example corpora at directory granularity. Indexing 111 nested example configs individually is registry bloat, not coverage. (surface: context)
- [2026-08-21] `DOC_URL_GUESSING` — fetch vendor docs from the repo's raw markdown source, not the rendered site. `goose-docs.ai` returns 403; `raw.githubusercontent.com/block/goose/main/documentation/docs/...` returns the real source. (surface: tools)
- [2026-08-21] `CEREMONY_OVERKILL` — declare the loop (direct / RPI / Ralph / delegate) and the reason before starting work. RPI on a two-line fix is as wrong as improvising a thirty-file refactor. (surface: prompt)
- [2026-08-21] `POWERSHELL_HEREDOC_REGEX` — never build multi-line Python or code edits with `-replace` and backtick-n in PowerShell; the backticks land literally and produce syntax errors. Use the edit tool. Made this mistake twice in one session. (surface: tools)
- [2026-08-21] `YAML_FLOW_QUOTES` — a YAML flow mapping value containing a quote or colon breaks the parser. `use_when: "the agent is bad" - x` crashed the registry load. Keep flow-mapping values plain, or block-quote them. (surface: context)
- [2026-08-21] `PARTIAL_ROUTER_LOOKUP` — resolve registry keys against every key namespace, not just the first. Searching only `chapters` made valid `meta_docs` and `reference_configs` routes look broken. (surface: context)
- [2026-08-21] `HYGIENE_SCAN_NOISE` — exclude `.venv`, `.git`, and `site-packages` from repo hygiene scans. Reporting 49 findings when 8 are real trains the reader to ignore the check. (surface: tools)
- [2026-08-21] `FENCED_TEMPLATE_FALSE_POSITIVE` — strip fenced code blocks before validating document content. `lessons.md` documents its own line format in a fence, and reading that as a real lesson failed a correct file. (surface: tools)
- [2026-08-21] `PROSE_CANNOT_ENFORCE_PROSE` — an instruction cannot enforce an instruction. Replace "always verify before saying done" with a gate that computes completion from recorded exit codes. The agent names the command; the harness observes the result. (surface: tools)
- [2026-08-21] `RUN_IT_TO_FIND_IT` — three real bugs (YAML parse, float keys, scan noise) survived a passing test suite and were found only by executing the CLI end to end. Always run the actual command, not just the tests. (surface: tools)
- [2026-08-24] `MODE_PERMISSION_CONTRADICTION` — never advertise an executable CLI command inside a specialist mode that lacks command permission; describe the capability or route to a canonical `awino-*` skill instead. (surface: tools)
- [2026-08-24] `PLUGIN_TRUST_BYPASS` — install native plugins only through an explicit user plugin action; never turn a pasted URL into agent-driven global mutation, and never initialize project state during plugin install. (surface: tools)
- [2026-08-25] `DEFAULT_AGENT_SHADOWING` — set a plugin's default agent to its scoped identifier (`plugin:agent`), never a bare name that a user or project agent can shadow with a restricted tool list. (surface: tools)
- [2026-08-25] `CHANNEL_ASSUMPTION` — route visuals to the richest format the active client can actually present; never claim inline rendering, Artifact publishing, or image generation without the corresponding tool. (surface: tools)
- [2026-08-25] `PLUGIN_CLI_COLD_START` — a versioned plugin cache cannot rely on a `.venv` from the previous release; launch through `uv run --frozen --no-dev` so every installed version prepares its own locked environment automatically. (surface: tools)
- [2026-08-25] `TENET_WITHOUT_SENSOR` — confirmed project tenets must be injected into every fresh task session and converted to hook checks wherever they are mechanically testable; a line in CLAUDE.md alone is documentation, not enforcement. (surface: memory + tools)
- [2026-08-25] `UNDER_INTERVIEWED_PLAN` — one-question-at-a-time means sequencing, not stopping after one question; continue the adaptive planning grill until behavior, edges, constraints, acceptance, and non-goals are settled. (surface: prompt)
- [2026-08-25] `COMPETING_CONTROLLER` — keep one human-facing A.W.I.N.O. controller and load `awino-delegate` for orchestration; a second master persona creates competing trackers, approval rules, and memory. (surface: context + tools)
- [2026-08-25] `REWARD_THEATER` — fictional points in a prompt invite gaming; compute an advisory score only from plan decisions, executed gates, failures, and corrections, and never let score override closure. (surface: verification)
- [2026-08-26] `PARTIAL_SHIPPED_AS_COMPLETE` — when an objective states a count (N indicators, N sources, N categories), record achieved-vs-stated as gate evidence and refuse closure when achieved < stated unless the human accepts reduced scope in the ledger; a partial artifact labeled complete is a failed task, not partial success. (surface: verification + tools)
- [2026-08-26] `SELF_AUTHORED_ESCAPE_HATCH` — never invent a success-adjacent status (`ungathered`, `unavailable`, `honesty_boundary`) to make skipped work look principled; refusing to fabricate is required, but it is never a substitute for producing the missing input. (surface: prompt)
- [2026-08-26] `ANNOTATE_INSTEAD_OF_GENERATE` — when a required input is absent, first search the repository for the generator that produces it and run it; only after proving no generator exists may absence be reported as a blocker. (surface: context)
- [2026-08-26] `OUTPUT_MISTAKEN_FOR_CAPABILITY` — when asked whether a pipeline generalizes, inspect the generating scripts and their parameters, never the presence or absence of past output directories; a missing artifact for input X proves only that the pipeline has not been run on X. (surface: context)
- [2026-08-26] `UNVERIFIED_AGAINST_ASK` — before claiming done, restate the human's success criterion verbatim, then paste the command output that measures that exact criterion; passing an adjacent test suite is not evidence the stated deliverable exists. (surface: verification)

- [2026-08-28] `PROBE_AS_SHILL` — a diagnostic written in a temp directory proves nothing about the shipped pipeline. Move the logic into the shared module the production scripts import, then run the production script and show the probe output inside its own stdout. A number reported without a run directory the reader can open is indistinguishable from invention. (surface: tools + verification)
- [2026-08-28] `SAME_SYMPTOM_DIFFERENT_BUGS` — three distinct defects (case/legal-suffix, truncation/prefix, parent/division) all presented as "organization names not merging". Reporting each as "fixed the dedup" reads as repeating one fix. Name the specific defect and the specific mechanism every time, or the reader cannot tell progress from churn. (surface: prompt)
- [2026-08-28] `HALF_THE_INPUTS_PROBED` — when a system holds two term lists, measure both against every source. Probing only the query list reported zero federal awards for TRISO while the identity term `TRISO fuel` retrieved four real DoD and DOE awards. Which vocabulary works is a property of the source, never an assumption about where each list belongs. (surface: context)
- [2026-08-28] `WINDOW_UNSTATED` — a retrieval window is a mission parameter, not a default to inherit. A 19-year window silently diluted a five-year trend question and buried current filings under historical noise. State the window in the run output and the README, and make it a flag. (surface: prompt)
- [2026-08-28] `ARRIVAL_ORDER_TRUNCATION` — never truncate a candidate list before ranking it. Taking the first five of 3,924 filings returned e-commerce prospectuses while nuclear utilities sat unread. Rank by source-supplied signals (relevance score, term specificity, term agreement, industry code, recency), then truncate, and record that truncation defers rather than excludes. (surface: tools)
- [2026-08-28] `BROAD_TERM_DROPPED_NOT_INTERSECTED` — a term matching thousands of documents is not useless, it is unqualified. Measure the intersection before discarding it: `spent fuel` 622 and `reprocessing` 1,169 intersect to 99 genuinely on-topic filings. Dropping either would have lost all 99. (surface: context)

---

## Superseded

<!-- moved here with their supersession date, kept as evidence -->

