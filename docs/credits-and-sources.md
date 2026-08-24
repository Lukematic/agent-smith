# Credits and Sources

A.W.I.N.O. is an independent project by Luke Awino. The project code is released
under the MIT License. External projects, documentation, and dynamically retrieved
knowledge retain their own licenses and are not relicensed by A.W.I.N.O.

No affiliation with or endorsement by the projects below is implied.

## Primary knowledge source

**Agentic Engineering Book** — Jaymin West and contributors  
Repository: <https://github.com/jayminwest/agentic-engineering-book>  
License: CC BY-NC-SA 4.0  
License text: <https://creativecommons.org/licenses/by-nc-sa/4.0/>

A.W.I.N.O. uses the book as a living, source-cited knowledge corpus. It stores a
machine-readable index in `knowledge/REGISTRY.yaml`, retrieves selected chapter
bodies on demand, and cites chapter paths in answers and bundled skills. Agent
A.W.I.N.O.'s routing, summaries, and implementation are adaptations and independent
engineering work; changes have been made. Book-derived material remains subject to
the upstream CC BY-NC-SA terms, including attribution, noncommercial use, and
ShareAlike requirements.

## Reference and integrated projects

| Project | Author/owner | License | Use in A.W.I.N.O. |
| --- | --- | --- | --- |
| Seeds | Jaymin West / `jayminwest` | MIT | Optional git-native issue tracker integration |
| Overstory | Jaymin West / `jayminwest` | MIT | Reference for multi-agent roles, scope, and completion protocols |
| Warren | Jaymin West / `jayminwest` | MIT | Reference for agent composition and workload models |
| Goose | Block, Inc. | Apache-2.0 | Supported harness/plugin integration and documentation reference |
| Kilo Code | Kilo contributors | project license | Supported persona, skills, and mode integration |
| Roo Code | Roo Code contributors | project license | Supported custom mode integration |
| GitHub Copilot | GitHub | proprietary product | Supported VS Code chat-mode integration |
| Claude Code | Anthropic | proprietary product | Supported agent and skill integration |
| Cursor | Anysphere | proprietary product | Supported rule integration |

Exact repository URLs and source roles are recorded in
`knowledge/SOURCES.yaml`. Retrieved paths, revisions, and timestamps are recorded
in `knowledge/MANIFEST.json` when present.

## Local skill-library inspirations

A.W.I.N.O.'s discovery and research modes compose ideas from a local skill
library rather than copying an entire library into every prompt. Relevant
inspirations include:

- `idea-refine` — mission, MVP, alternatives, and non-goals;
- `requirements-analyst` — stakeholder and acceptance discovery;
- `plan-interrogate` — adversarial plan review;
- `evidence-sufficiency-gates` — answer/retrieve/clarify/no-answer decisions;
- `citation-support-verification` — claim-to-source auditing;
- `reproducibility-audit` — run IDs, input/config capture, and audit trails;
- `verification-specialist` — independent evidence before completion;
- `debugging-specialist` — reproduce, collect evidence, bisect, understand, fix.

Where material is later copied or vendored rather than reimplemented, its source
path, repository, license, and modifications must be added here before release.

## Product identity

A.W.I.N.O. is multi-harness. Goose, Kilo, Roo, Claude Code, Copilot, and Cursor
are integrations—not A.W.I.N.O.'s product identity. User-facing documentation
should use neutral terms such as **active harness**, **agent persona**, **skill**,
and **project-local state**, except where an exact product name or command is
operationally necessary.

## Reviewed for adoption (2026-08-23)

- **obra/superpowers** (MIT) — reviewed for the spec/plan document-review-loop
  pattern and the systematic-debugging four-phase methodology. Full comparison:
  docs/updates/third-party-skills-review.md.
- **muratcankoylan/Agent-Skills-for-Context-Engineering** (MIT) — reviewed for the
  tool-design consolidation principle and the harness-engineering surface
  taxonomy. Full comparison: docs/updates/third-party-skills-review.md.

Neither project's code or skill content is vendored verbatim. Where a pattern was
adopted, it was re-implemented against this repo's own architecture (see the
linked review doc for the specific adaptation decisions and rejected items).
