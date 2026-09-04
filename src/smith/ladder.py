"""S2: the ladder gets its first reader.

`detect_rung` has run on every gate open and written an artifact nobody read.
`choose` turns the rung, the verifier's strength, and the declared scope into
the one loop the machine will run. Deterministic: same inputs, same loop, and
the `why` names which observation decided it.

Rules, in order of precedence:
  prompt rung                                  -> direct   (answer; no run)
  reviewer-class skill or "keeps/intermittent" -> graph    (a second opinion is the point)
  3+ disjoint scopes                           -> delegate
  no verify or weak verify                     -> ralph    (expect retries)
  otherwise                                    -> floor    (one pass, one verify)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from smith.models import Rung, detect_rung

_REVIEW_SKILLS = frozenset({"awino-triage", "awino-config-review", "awino-evidence"})
_INTERMITTENT = re.compile(r"\b(keeps|intermittent|sometimes|flaky|randomly|every time)\b", re.I)
_WEAK_VERIFY = re.compile(r"raise SystemExit\(0\)|^true$|^exit 0$|echo ", re.I)


@dataclass(frozen=True)
class LoopChoice:
    loop: str
    why: str
    rung: str


def choose(request: str, skill: str, verify: str | None, scope: list[str]) -> LoopChoice:
    verdict = detect_rung(request)
    rung = verdict.actual.name.lower()
    if verdict.actual is Rung.PROMPT and skill in {
        "awino-consult",
        "awino-visualize",
        "awino-memory",
    }:
        return LoopChoice("direct", f"prompt rung, {skill} answers directly", rung)
    if skill in _REVIEW_SKILLS or _INTERMITTENT.search(request):
        return LoopChoice(
            "graph", "second opinion required: review-class skill or intermittent symptom", rung
        )
    distinct = {s.split("/")[0] for s in scope}
    if len(scope) >= 3 and len(distinct) >= 3:
        return LoopChoice("delegate", f"{len(scope)} disjoint scopes", rung)
    if not verify or _WEAK_VERIFY.search(verify):
        return LoopChoice("ralph", "no red-capable verify yet: expect retries", rung)
    return LoopChoice("floor", f"{rung} rung, one scope, strong verify", rung)
