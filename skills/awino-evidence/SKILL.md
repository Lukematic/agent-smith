---
name: awino-evidence

description: Evidence sufficiency and citation gate for research RAG QA and scientific workflows. Decides answer retrieve-more clarify no-answer or block-unsupported-synthesis before factual output is released.
---

# A.W.I.N.O. Evidence

Use when A.W.I.N.O. is designing or reviewing a research assistant, RAG system,
scientific synthesis, market analysis, or any factual workflow where related
sources are not automatically sufficient evidence.

This composes the local `evidence-sufficiency-gates` and
`citation-support-verification` disciplines into A.W.I.N.O.'s gate vocabulary.

## Core rule

Do not answer because related evidence exists. Evidence must support the requested
entities, scope, timeframe, claim type, specificity, and level of detail.

## Required decisions

Return exactly one:

- `ANSWER`
- `ANSWER_WITH_LIMITATIONS`
- `RETRIEVE_MORE`
- `ASK_CLARIFICATION`
- `NO_ANSWER`
- `BLOCK_UNSUPPORTED_SYNTHESIS`

## Process

1. Parse the user need: entities, timeframe, geography/jurisdiction, answer type,
   and decisions the answer may influence.
2. Decompose it into atomic claims.
3. Map inspectable evidence to each claim by source ID plus page/row/chunk/URL.
4. Grade each claim:
   - `SUFFICIENT`
   - `PARTIAL`
   - `WEAK`
   - `CONTRADICTORY`
   - `MISSING`
   - `UNCHECKABLE`
5. Apply the decision rules:
   - Any essential `MISSING`, `UNCHECKABLE`, or unresolved `CONTRADICTORY` claim
     prevents `ANSWER`.
   - Ambiguity routes to `ASK_CLARIFICATION`.
   - Fillable gaps route to `RETRIEVE_MORE` with targeted queries.
   - An answer exceeding its sources routes to `BLOCK_UNSUPPORTED_SYNTHESIS`.
6. After drafting, atomize compound claims and audit every clause against its
   citations. Topical relevance is not direct support.

## Research-tool contract

For an open research assistant:

- every displayed synthesis claim carries source IDs;
- abstract-only evidence is visibly labelled;
- full-text claims cannot be inferred from abstracts;
- unsupported clauses are removed or explicitly marked unsupported;
- contradictory evidence remains visible;
- source metadata and retrieval query are preserved;
- no source laundering through uncited summaries.

## Output

```markdown
# Evidence Gate

## Decision
- Gate: ...
- Confidence: HIGH | MEDIUM | LOW
- Reason: ...

## Claim Map
| ID | Atomic claim | Required evidence | Evidence IDs | Sufficiency |

## Missing Information
| ID | Gap | Targeted next query or source class |

## Release Boundary
- Supported claims: ...
- Claims removed or limited: ...
```

## Failure Modes

| Mode | Definition |
| --- | --- |
| `TOPIC_IS_SUPPORT` | treating related evidence as direct support |
| `SOURCE_LAUNDERING` | repeating a source-free summary as established fact |
| `COMPOUND_CITATION` | one citation attached to several independently testable claims |
| `SCOPE_INFLATION` | source supports a narrower entity/timeframe than the answer |
| `ABSTRACT_OVERREACH` | presenting abstract evidence as full-text review |
| `RETRIEVAL_FOREVER` | broad retries instead of targeted gap-closing queries |
| `UNCHECKABLE_CONFIDENCE` | assigning confidence without inspectable provenance |

## Completion

Done when every released factual claim maps to inspectable evidence and the gate
decision is explicit. Verify the result with an independent citation-support audit;
do not let the generating agent grade itself.

Grounding: chapters/8-practices/2-evaluation.md,
chapters/11-agent-readiness/3-readiness-principles.md,
chapters/12-long-horizon-agent-state/3-memory-and-intent.md
