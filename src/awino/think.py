"""Critical thinking modes: the executable form of "challenge assumptions".

Nine named ways of thinking, each with a prompt template (the structure of
the thinking), a structural validator (the required output sections), and a
working-memory destination. Thinking that doesn't land in memory didn't
happen: recording a mode's output writes its insights to facts.md or
decisions.md.

Modes map onto stances where they overlap, reusing the stance machinery
instead of duplicating it:

- devil          -> steel-man:        the opposing case before any own view
- feynman        -> teach-back:       explain simply, then test the explanation
- blindspot      -> assumption-audit: what the conclusion needs but never says
- first-principles -> first-principles: facts vs assumptions, rebuild from facts
- assumption-destroyer -> (extends assumption-audit's spirit with teeth:
  invert and reframe every assumption, not just rate it)

The rest are genuinely new -- no stance covers them:

- premortem:      assume the plan failed; name why, with warning signs
- uncomfortable:  ask the avoided question, then answer it straight
- thought-experiment: push one variable to the extreme, read what it reveals
- simplify:       strip to the minimal variables, solve using only those

The validators are structural, not semantic: they check that the required
sections exist and that counted parts (failure reasons, assumptions,
variables) meet their minimums with their required companions (warning
signs, inversions, reframings). Like the stance critic, they are keyword
heuristics -- a pass means "no known violation found".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from awino import working_memory


class ThinkError(Exception):
    """A thinking-mode output failed structural validation."""


@dataclass(frozen=True)
class Mode:
    name: str
    when_to_use: str
    stance: str | None  # the mapped stance, or None when genuinely new
    memory: str  # "decisions" or "facts": where recording lands
    prompt: str  # the template: the structure of the thinking
    sections: tuple[tuple[str, tuple[str, ...]], ...] = field(default_factory=tuple)
    # (canonical section name, heading synonyms) required in the output


MODES: tuple[Mode, ...] = (
    Mode(
        "feynman",
        "when you are learning or unsure: explain it like teaching, and let the gaps show",
        "teach-back",
        "facts",
        (
            "Explain it like you are teaching a smart twelve-year-old.\n"
            "\n"
            "## Simple explanation\n"
            "The idea in plain language, no jargon. If you need a technical\n"
            "term, define it first.\n"
            "\n"
            "## ELI5\n"
            "The core idea for a smart twelve-year-old: plain language, no\n"
            "jargon, no unexplained terms. This is the understanding test --\n"
            "if you cannot say it simply here, the simple explanation above\n"
            "is lying. Both the copilot and the user must genuinely\n"
            "understand, not just nod along.\n"
            "\n"
            "## Diagram\n"
            "The core variables and how they relate, as ASCII or mermaid --\n"
            "boxes and arrows, not prose. Whatever the ELI5 names must appear\n"
            "here, with the relationships between them drawn.\n"
            "\n"
            "## Where it breaks\n"
            "Where does the simple version stop working? Name the exact point\n"
            "the explanation gets shaky -- that gap is where your\n"
            "understanding is thinnest.\n"
            "\n"
            "## Self-check\n"
            "One question that would expose a fake understanding of this.\n"
            "Then answer it honestly: can you?"
        ),
        (
            ("simple explanation", ("simple explanation", "explain simply", "plain language")),
            ("eli5", ("eli5", "explain like i'm five", "twelve-year-old", "for a child")),
            ("diagram", ("diagram", "variable map", "relationship diagram", "boxes and arrows")),
            ("where it breaks", ("where it breaks", "where the explanation breaks", "gaps", "shaky")),
            ("self-check", ("self-check", "self check", "test yourself", "check your understanding")),
        ),
    ),
    Mode(
        "blindspot",
        "when a plan feels too clean: surface what you are not seeing and why it stayed invisible",
        "assumption-audit",
        "decisions",
        (
            "## Assumptions checked\n"
            "List the assumptions this plan or conclusion quietly depends on.\n"
            "\n"
            "## Blind spots\n"
            "For each blind spot: what you are not seeing, and WHY IT WAS\n"
            "INVISIBLE -- say explicitly why it stayed out of view (who\n"
            "benefits from it staying hidden, what frame made it unthinkable,\n"
            "what you never thought to check)."
        ),
        (
            ("assumptions checked", ("assumptions checked", "assumptions")),
            ("blind spots", ("blind spots", "blindspots", "blind spot")),
        ),
    ),
    Mode(
        "devil",
        "before you commit to a decision: the strongest evidence-backed case against your position",
        "steel-man",
        "decisions",
        (
            "## The opposing case\n"
            "The strongest evidence-backed case AGAINST the position.\n"
            "Steel-man it: argue it better than its believers would.\n"
            "\n"
            "## What to take seriously\n"
            "Which part of the opposing case is the real threat -- the part\n"
            "that should change the plan.\n"
            "\n"
            "## My view (optional, and only after the above)\n"
            "Your own judgment, stated after the opposing case has had its\n"
            "full say."
        ),
        (
            ("the opposing case", ("opposing case", "case against", "the case against")),
            ("what to take seriously", ("take seriously", "take most seriously", "what to take")),
        ),
    ),
    Mode(
        "premortem",
        "before implementation approval: assume the plan failed completely, and name why",
        None,
        "decisions",
        (
            "It is one year later. The plan has failed completely and\n"
            "unambiguously.\n"
            "\n"
            "## Failure reasons\n"
            "Name at least three distinct reasons it failed. Number them.\n"
            "\n"
            "## Warning signs\n"
            "For EACH failure reason: the warning signs that would have been\n"
            "visible early -- what you would have noticed in time to change\n"
            "course, had you been watching. Write them with each reason\n"
            '(e.g. a "Warning signs:" line under it).'
        ),
        (
            ("failure reasons", ("failure reasons", "failure reason", "why it failed", "reasons it failed")),
        ),
    ),
    Mode(
        "uncomfortable",
        "when something feels off but unnamed: ask the question you are avoiding, then answer it",
        None,
        "decisions",
        (
            "## The avoided question\n"
            "The question you have been avoiding asking -- about the plan,\n"
            "the decision, or yourself. Write it as a direct question.\n"
            "\n"
            "## The answer\n"
            "Answer it straight. No hedging, no reframing it into a safer\n"
            "question. If the honest answer is \"I don't know,\" say what you\n"
            "would need to find out."
        ),
        (
            ("the avoided question", ("avoided question", "the question")),
            ("the answer", ("the answer", "answer")),
        ),
    ),
    Mode(
        "thought-experiment",
        "to test an idea's limits: push one variable to the extreme and read what it reveals",
        None,
        "decisions",
        (
            "## Scenario\n"
            "The situation, stated plainly.\n"
            "\n"
            "## Push to the extreme\n"
            "Take one variable to its limit -- 10x, zero, infinite, reversed.\n"
            "What happens at the extreme?\n"
            "\n"
            "## What it reveals\n"
            "What does the extreme case reveal about the normal case that you\n"
            "couldn't see before?"
        ),
        (
            ("scenario", ("scenario",)),
            ("push to the extreme", ("push to the extreme", "the extreme", "extreme")),
            ("what it reveals", ("what it reveals", "reveals", "what this reveals")),
        ),
    ),
    Mode(
        "first-principles",
        "when decomposing a problem: separate facts from assumptions, rebuild from facts alone",
        "first-principles",
        "facts",
        (
            "## Facts\n"
            "What is observably true, with evidence. No interpretations.\n"
            "\n"
            "## Assumptions\n"
            "Everything the current thinking quietly takes for granted. Mark\n"
            "each: well-supported / reasonable-unverified / potentially false.\n"
            "\n"
            "## Rebuild from facts\n"
            "Forget the current approach. Rebuild a solution using ONLY the\n"
            "facts above. Name the one assumption most worth challenging."
        ),
        (
            ("facts", ("facts",)),
            ("assumptions", ("assumptions",)),
            ("rebuild from facts", ("rebuild from facts", "rebuild", "rebuilt")),
        ),
    ),
    Mode(
        "assumption-destroyer",
        "during research framing: name five assumptions, invert each, reframe each",
        None,
        "facts",
        (
            "## Assumptions\n"
            "Name at least five assumptions the plan depends on. Number them.\n"
            "\n"
            "For EACH assumption, write:\n"
            "- Inversion: the exact opposite, stated as if it were true\n"
            '  ("What if the opposite were true?")\n'
            "- Reframing: a new frame that dissolves the assumption entirely\n"
            "  -- a way of seeing the problem where the assumption doesn't\n"
            "  apply."
        ),
        (
            ("assumptions", ("assumptions",)),
        ),
    ),
    Mode(
        "simplify",
        "when the problem feels overcomplicated: strip to the minimal variables and solve with only those",
        None,
        "facts",
        (
            "## Minimal variables\n"
            "Strip the problem to the smallest set of variables that still\n"
            "determines the outcome. Name at least two. For each: why it\n"
            "earns its place, and what you threw away.\n"
            "\n"
            "## ELI5\n"
            "What the simplified problem is, for a smart twelve-year-old:\n"
            "plain language, no jargon. Both the copilot and the user must\n"
            "genuinely understand -- not just nod along.\n"
            "\n"
            "## Diagram\n"
            "The minimal variables and their relationships, as ASCII or\n"
            "mermaid: boxes and arrows showing what affects what. Every\n"
            "named variable must appear; nothing else may.\n"
            "\n"
            "## Solution using only these\n"
            "Solve the problem using ONLY the variables named above -- say\n"
            "so explicitly (\"using only these variables\"). If the solution\n"
            "needs something you threw away, the variable set was wrong:\n"
            "revise it, don't smuggle extras in."
        ),
        (
            ("minimal variables", ("minimal variables", "variables")),
            ("eli5", ("eli5", "explain like i'm five", "twelve-year-old", "for a child")),
            ("diagram", ("diagram", "variable map", "relationship diagram", "boxes and arrows")),
            ("solution using only these", ("solution using only these", "solution")),
        ),
    ),
)


def by_name(name: str) -> Mode:
    """The mode with this name; raises ValueError for an unknown mode."""
    for mode in MODES:
        if mode.name == name:
            return mode
    raise ValueError(f"unknown thinking mode: {name}")


MODE_NAMES: tuple[str, ...] = tuple(mode.name for mode in MODES)


# ── structural validation ──────────────────────────────────────────────────


_HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.MULTILINE)
_ENTRY_START_RE = re.compile(r"^(?:#{2,6}\s+|[-*+]\s+|\d+[.)]\s+)")

_WARNING_SIGN_RE = re.compile(
    r"(?i)\b(warning signs?|early warnings?|watch for|early signals?|would notice|leading indicators?)\b"
)
_INVERSION_RE = re.compile(r"(?i)\b(inversion|invert|opposite of|flipped|contrary)\b")
_REFRAME_RE = re.compile(r"(?i)\b(refram\w*|new frame|instead think|rather think|different lens)\b")
_INVISIBILITY_RE = re.compile(
    r"(?i)\b(invisible because|why (it|this) was invisible|missed because|couldn'?t see|blind to|never thought to check)\b"
)
_ONLY_MARKER_RE = re.compile(
    r"(?i)\b(using only|only these|nothing else|no other variables?)\b"
)
# The teaching side: feynman and simplify must draw the core variables and
# their relationships, not just describe them. A diagram is boxes and
# arrows -- a mermaid block, an A --> B relationship line, or box-drawing.
# Prose that merely mentions the variables is not a diagram.
_DIAGRAM_RE = re.compile(
    r"(?im)^\s*(graph|flowchart|sequencediagram|classdiagram)\b"
    r"|\w[\w ]*\s*--?>\s*\w"
    r"|[│┌┐└┘├┤┬┴┼─]"
    r"|\+\s*-{2,}"
)
# devil reuses the steel-man shape: the opposing case must come first.
_OPPOSING_MARKERS: tuple[str, ...] = (
    "the opposing case",
    "opposite position",
    "case against",
    "strongest case against",
    "steel-man",
    "devil's advocate",
)
_OWN_VIEW_MARKERS: tuple[str, ...] = (
    "my view",
    "my take",
    "in my view",
    "my recommendation",
    "i recommend",
)


def _section_text(markdown: str, synonyms: tuple[str, ...]) -> str:
    """Text under the first heading matching any synonym, up to the next
    heading of the same or higher level."""
    matches = list(_HEADING_RE.finditer(markdown))
    for i, match in enumerate(matches):
        heading = match.group(1).lower()
        if any(s in heading for s in synonyms):
            this_level = len(re.match(r"^#+", match.group(0)).group(0))  # type: ignore[union-attr]
            end = len(markdown)
            for nxt in matches[i + 1 :]:
                nxt_level = len(re.match(r"^#+", nxt.group(0)).group(0))  # type: ignore[union-attr]
                if nxt_level <= this_level:
                    end = nxt.start()
                    break
            return markdown[match.end() : end]
    return ""


def _entries(section_text: str) -> list[str]:
    """One entry per bullet, numbered item, or sub-heading block."""
    entries: list[str] = []
    current: list[str] = []

    def flush() -> None:
        text = "\n".join(current).strip()
        if text:
            entries.append(text)
        current.clear()

    for line in section_text.splitlines():
        stripped = line.strip()
        if not stripped:
            flush()
        elif _ENTRY_START_RE.match(stripped):
            flush()
            current.append(stripped)
        else:
            current.append(stripped)
    flush()
    return entries


def _entry_head(entry: str, width: int = 60) -> str:
    """The entry's headline: first line, leading bullet or numbering stripped,
    whitespace collapsed, truncated."""
    first = re.sub(r"^(\d+[.)]|[-*+])\s+", "", entry.splitlines()[0].strip())
    return re.sub(r"\s+", " ", first)[:width]


def _require_sections(mode: Mode, text: str) -> tuple[list[str], dict[str, str]]:
    """The missing required sections, plus the found section bodies by name."""
    missing: list[str] = []
    bodies: dict[str, str] = {}
    for canonical, synonyms in mode.sections:
        body = _section_text(text, synonyms)
        if not body.strip():
            missing.append(f"missing required section: '{canonical}'")
        else:
            bodies[canonical] = body
    return missing, bodies


def _validate_teaching(
    mode_name: str, bodies: dict[str, str], missing: list[str]
) -> None:
    """The teaching side, shared by feynman and simplify: an ELI5 section
    (plain language a smart twelve-year-old follows) and a diagram section
    that actually draws the variables and their relationships. Missing
    either fails with the part named; prose without boxes-and-arrows is not
    a diagram."""
    diagram = bodies.get("diagram", "")
    if diagram and not _DIAGRAM_RE.search(diagram):
        missing.append(
            f"the diagram section of {mode_name} draws no diagram: show the "
            "core variables and their relationships as ASCII or mermaid "
            "(boxes and arrows, e.g. 'A --> B'), not prose"
        )


def _validate_feynman(_mode: Mode, text: str) -> list[str]:
    missing, bodies = _require_sections(_mode, text)
    _validate_teaching("feynman", bodies, missing)
    return missing


def _validate_blindspot(_mode: Mode, text: str) -> list[str]:
    missing, bodies = _require_sections(_mode, text)
    if "blind spots" in bodies:
        for i, entry in enumerate(_entries(bodies["blind spots"]), 1):
            if not _INVISIBILITY_RE.search(entry):
                missing.append(
                    f"blind spot {i} ('{_entry_head(entry)}') does not say "
                    "why it was invisible"
                )
    return missing


def _validate_devil(_mode: Mode, text: str) -> list[str]:
    missing, _ = _require_sections(_mode, text)
    lowered = text.casefold()
    opposing = min(
        (lowered.find(m) for m in _OPPOSING_MARKERS if lowered.find(m) >= 0),
        default=None,
    )
    own = min(
        (lowered.find(m) for m in _OWN_VIEW_MARKERS if lowered.find(m) >= 0),
        default=None,
    )
    # The steel-man shape the devil mode reuses: an own view without the
    # opposing case first is the stance's core violation.
    if own is not None and (opposing is None or opposing > own):
        missing.append("the opposing case must come before your own view")
    return missing


# A "warning signs" section that says nothing is not a warning sign. The
# engineered dodge is a failure reason like "Nothing could go wrong.
# Warning signs: none" -- the marker is present, the substance is a
# negation. The remainder after the marker may only be the negation itself
# (optionally intensified); anything else counts as a real attempt.
_VACUOUS_WARNING_RE = re.compile(
    r"(?i)^(none|nothing|n/?a|nil|no(ne)?|not applicable|unknown|tbd"
    r"|no warning signs?|no early warnings?)"
    r"(\s+(whatsoever|at all|known|identified|yet))?[.,;!\s]*$"
)
_TRAILING_NEGATION_RE = re.compile(
    r"(?i)\bno\s+(warning signs?|early warnings?)\s*[.,;!]*$"
)


def _validate_premortem(_mode: Mode, text: str) -> list[str]:
    missing, bodies = _require_sections(_mode, text)
    body = bodies.get("failure reasons", "")
    entries = _entries(body)
    if not missing and len(entries) < 3:
        missing.append(
            f"premortem names {len(entries)} failure reasons; at least 3 required"
        )
    for i, entry in enumerate(entries, 1):
        if not _WARNING_SIGN_RE.search(entry):
            missing.append(
                f"failure reason {i} ('{_entry_head(entry)}') has no warning signs"
            )
            continue
        real_sign = False
        for match in _WARNING_SIGN_RE.finditer(entry):
            tail = re.sub(r"^[\s:—–-]+", "", entry[match.end() :].strip())
            if _VACUOUS_WARNING_RE.match(tail):
                continue  # this marker says nothing; maybe another one does
            # A marker followed only by punctuation is a trailing negation
            # ("...there are no warning signs."): the period is not substance.
            tail_bare = re.sub(r"[.,;!\s]*$", "", tail)
            if not tail_bare and _TRAILING_NEGATION_RE.search(entry):
                continue  # "...no warning signs" as the entry's last words
            real_sign = True
            break
        if not real_sign:
            missing.append(
                f"failure reason {i} ('{_entry_head(entry)}') names no real "
                "warning signs: 'none' is not a warning sign -- say what you "
                "would actually see going wrong"
            )
    return missing


def _validate_uncomfortable(_mode: Mode, text: str) -> list[str]:
    missing, bodies = _require_sections(_mode, text)
    question = bodies.get("the avoided question", "")
    if question and "?" not in question:
        missing.append("the avoided question section asks no question")
    answer = bodies.get("the answer", "")
    if answer and len(answer.strip()) < 40:
        missing.append("the answer section is too short to be an answer")
    return missing


def _validate_thought_experiment(_mode: Mode, text: str) -> list[str]:
    missing, _ = _require_sections(_mode, text)
    return missing


def _validate_first_principles(_mode: Mode, text: str) -> list[str]:
    # The research breakdown requirement in mode form: facts separated from
    # assumptions, then a rebuild from the facts alone.
    missing, _ = _require_sections(_mode, text)
    return missing


def _validate_assumption_destroyer(_mode: Mode, text: str) -> list[str]:
    missing, bodies = _require_sections(_mode, text)
    entries = _entries(bodies.get("assumptions", ""))
    if not missing and len(entries) < 5:
        missing.append(
            f"assumption-destroyer names {len(entries)} assumptions; at least 5 required"
        )
    for i, entry in enumerate(entries, 1):
        head = _entry_head(entry)
        if not _INVERSION_RE.search(entry):
            missing.append(f"assumption {i} ('{head}') has no inversion")
        if not _REFRAME_RE.search(entry):
            missing.append(f"assumption {i} ('{head}') has no reframing")
    return missing


def _validate_simplify(_mode: Mode, text: str) -> list[str]:
    missing, bodies = _require_sections(_mode, text)
    entries = _entries(bodies.get("minimal variables", ""))
    if not missing and len(entries) < 2:
        missing.append(
            f"simplify names {len(entries)} minimal variables; at least 2 required"
        )
    solution = bodies.get("solution using only these", "")
    if solution and not _ONLY_MARKER_RE.search(solution):
        missing.append(
            "the solution must say it uses only the named variables "
            "(e.g. 'using only these variables')"
        )
    _validate_teaching("simplify", bodies, missing)
    return missing


_VALIDATORS = {
    "feynman": _validate_feynman,
    "blindspot": _validate_blindspot,
    "devil": _validate_devil,
    "premortem": _validate_premortem,
    "uncomfortable": _validate_uncomfortable,
    "thought-experiment": _validate_thought_experiment,
    "first-principles": _validate_first_principles,
    "assumption-destroyer": _validate_assumption_destroyer,
    "simplify": _validate_simplify,
}


# Hostile character tricks: null bytes and explicit Unicode bidi controls
# (overrides, embeddings, isolates) are never legitimate thinking output --
# nulls signal binary/truncated content, and bidi controls can visually
# reorder text so what was validated is not what a human reads. Plain
# international text (CJK, Arabic, Hebrew, emoji) is unaffected: only the
# explicit formatting controls are refused. Same policy as the artifact
# guard in awino.loops; kept local so this module stays dependency-light.
_BIDI_CONTROLS_RE = re.compile("[\u202a-\u202e\u2066-\u2069]")


def _hostile_text_refusal(mode_name: str, text: str) -> str | None:
    """Refuse hostile character tricks in a mode's output, naming the mode."""
    if "\x00" in text:
        return (
            f"thinking output ({mode_name}) contains null bytes: thinking "
            "output is UTF-8 text -- rewrite it as text and re-record"
        )
    if _BIDI_CONTROLS_RE.search(text):
        return (
            f"thinking output ({mode_name}) contains Unicode bidi control "
            "characters: explicit bidi overrides/embeddings/isolates can "
            "visually reorder text -- remove them and re-record"
        )
    return None


def validate(mode_name: str, text: str) -> list[str]:
    """The structural rules a mode's output fails; empty means compliant.

    Raises ValueError for an unknown mode name. Pure structure: required
    sections plus the counted parts each mode demands (failure reasons with
    warning signs, assumptions with inversions and reframings, variables with
    an only-these solution, the asked-and-answered avoided question, and the
    teaching side -- feynman and simplify need both an ELI5 section and a
    drawn diagram of the variables and their relationships).
    """
    mode = by_name(mode_name)  # raises ValueError on unknown
    hostile = _hostile_text_refusal(mode.name, text)
    if hostile is not None:
        return [hostile]
    return _VALIDATORS[mode.name](mode, text)


# ── recording: thinking that doesn't land in memory didn't happen ──────────


def headline(text: str, width: int = 120) -> str:
    """The first non-empty line: the output's headline, used when the driver shares its take."""
    for line in text.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return re.sub(r"\s+", " ", stripped)[:width]
    return "(no text)"


def _summarize(mode_name: str, text: str) -> str:
    """The rationale line recorded alongside the insight in working memory."""
    mode = by_name(mode_name)
    bodies: dict[str, str] = {}
    for canonical, synonyms in mode.sections:
        bodies[canonical] = _section_text(text, synonyms)
    if mode_name == "premortem":
        entries = _entries(bodies.get("failure reasons", ""))
        top = _entry_head(entries[0]) if entries else "(none)"
        return (
            f"{len(entries)} failure reasons named, each with warning signs. "
            f"Top risk: {top}."
        )
    if mode_name == "assumption-destroyer":
        entries = _entries(bodies.get("assumptions", ""))
        return f"{len(entries)} assumptions named, each inverted and reframed."
    if mode_name == "simplify":
        entries = _entries(bodies.get("minimal variables", ""))
        return f"{len(entries)} minimal variables named; solution uses only those."
    if mode_name == "devil":
        serious = bodies.get("what to take seriously", "").strip().splitlines()
        first = re.sub(r"\s+", " ", serious[0])[:100] if serious else "(none)"
        return f"Opposing case made; take most seriously: {first}."
    if mode_name == "uncomfortable":
        question = bodies.get("the avoided question", "").strip().splitlines()
        first = re.sub(r"\s+", " ", question[0])[:120] if question else "(none)"
        return f"Avoided question asked and answered: {first}"
    first = headline(text)
    return f"Thinking recorded ({mode_name}): {first}."


def record_insight(
    mode_name: str, text: str, state_root: Path, source: str
) -> tuple[str, str]:
    """Validate a mode's output and write its insights to working memory.

    Returns (memory filename, entry id). Raises ThinkError naming the missing
    part when the output is not structurally compliant.
    """
    failures = validate(mode_name, text)
    if failures:
        raise ThinkError("; ".join(failures))
    mode = by_name(mode_name)
    head = headline(text)
    rationale = _summarize(mode_name, text)
    stamp = datetime.now(UTC).date().isoformat()
    if mode.memory == "decisions":
        entry_id = working_memory.Decisions(state_root).record(
            decision=f"thinking ({mode_name}): {head}",
            why=rationale,
            source=source,
            key=f"thinking:{mode_name}:{stamp}",
        )
        return working_memory.DECISIONS_FILENAME, entry_id
    entry_id = working_memory.Facts(state_root).append(
        f"thinking ({mode_name}): {headline}\n\n{rationale}"
    )
    return working_memory.FACTS_FILENAME, entry_id
