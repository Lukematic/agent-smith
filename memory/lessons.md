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

- [2026-08-21] `KNOWLEDGE_FORK` — install Agent Smith once as a global git-backed plugin at `~/.agents/plugins/agent-smith/`. Repos get a `.goosehints` pointer, never a copy. (surface: context)
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

---

## Superseded

<!-- moved here with their supersession date, kept as evidence -->

