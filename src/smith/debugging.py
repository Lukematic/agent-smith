"""Evidence-first debugging lifecycle persisted in the run artifact ledger."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from smith.enforce import Ledger, LedgerError, TaskClass


class DebugPhase(StrEnum):
    REPRODUCE = "reproduce"
    DIAGNOSE = "diagnose"
    FIX = "fix"
    VERIFY = "verify"


class ArchitectureAssessment(StrEnum):
    LOCAL_FIX = "LOCAL_FIX"
    ARCHITECTURE_QUESTIONABLE = "ARCHITECTURE_QUESTIONABLE"


@dataclass(frozen=True)
class FailureSignature:
    value: str

    @classmethod
    def normalize(cls, output: str) -> FailureSignature:
        value = output.lower().replace("\\", "/")
        value = re.sub(r"(?:[a-z]:)?/(?:[^\s:]+/)+([^\s/:]+\.py)", r"<path>/\1", value)
        value = re.sub(r":\d+", ":<line>", value)
        value = re.sub(r"\b(?:id=)?\d{2,}\b", "<number>", value)
        value = re.sub(r"\s+", " ", value).strip()
        return cls(value)


@dataclass(frozen=True)
class DebugEvidence:
    kind: str
    detail: str


@dataclass(frozen=True)
class Hypothesis:
    statement: str


@dataclass(frozen=True)
class DebugAttempt:
    approach: str
    signature: FailureSignature
    succeeded: bool


@dataclass
class DebugSession:
    ledger: Ledger
    run_id: str

    @classmethod
    def begin(cls, ledger: Ledger, run_id: str, symptom: str, actor: str) -> DebugSession:
        run = ledger.load(run_id)
        if run.task_class != str(TaskClass.BUGFIX):
            raise ValueError("debugging requires a bugfix run")
        if ledger.latest_artifact(run_id, "debug.begin") is not None:
            raise ValueError("debugging already begun for this run")
        ledger.append_artifact(
            run_id,
            "debug.begin",
            actor,
            {"phase": str(DebugPhase.REPRODUCE), "symptom": symptom},
        )
        return cls(ledger, run_id)

    @classmethod
    def current(cls, ledger: Ledger, run_id: str | None = None) -> DebugSession:
        resolved = run_id or ledger.current_id()
        if resolved is None or ledger.latest_artifact(resolved, "debug.begin") is None:
            raise LedgerError("no active debug lifecycle")
        return cls(ledger, resolved)

    @property
    def evidence(self) -> list[DebugEvidence]:
        return [
            DebugEvidence(str(item.payload["kind"]), str(item.payload["detail"]))
            for item in self.ledger.artifacts(self.run_id, "debug.evidence")
        ]

    @property
    def hypotheses(self) -> list[Hypothesis]:
        return [
            Hypothesis(str(item.payload["statement"]))
            for item in self.ledger.artifacts(self.run_id, "debug.hypothesis")
        ]

    @property
    def attempts(self) -> list[DebugAttempt]:
        return [
            DebugAttempt(
                approach=str(item.payload["approach"]),
                signature=FailureSignature(str(item.payload["failure_signature"])),
                succeeded=bool(item.payload["succeeded"]),
            )
            for item in self.ledger.artifacts(self.run_id, "debug.attempt")
        ]

    @property
    def authorized(self) -> bool:
        return self.ledger.latest_artifact(self.run_id, "debug.authorization") is not None

    @property
    def phase(self) -> DebugPhase:
        if self.ledger.latest_artifact(self.run_id, "debug.verification") is not None:
            return DebugPhase.VERIFY
        if any(attempt.succeeded for attempt in self.attempts):
            return DebugPhase.VERIFY
        if self.authorized:
            return DebugPhase.FIX
        if self.evidence:
            return DebugPhase.DIAGNOSE
        return DebugPhase.REPRODUCE

    @property
    def assessment(self) -> ArchitectureAssessment:
        failed_signatures = {
            attempt.signature.value for attempt in self.attempts if not attempt.succeeded
        }
        if len(failed_signatures) >= 3:
            return ArchitectureAssessment.ARCHITECTURE_QUESTIONABLE
        return ArchitectureAssessment.LOCAL_FIX

    def add_evidence(self, kind: str, detail: str, actor: str) -> DebugEvidence:
        item = DebugEvidence(kind.strip(), detail.strip())
        self.ledger.append_artifact(
            self.run_id, "debug.evidence", actor, {"kind": item.kind, "detail": item.detail}
        )
        return item

    def add_hypothesis(self, statement: str, actor: str) -> Hypothesis:
        if not self.evidence:
            raise ValueError("record reproduction evidence before a hypothesis")
        item = Hypothesis(statement.strip())
        self.ledger.append_artifact(
            self.run_id, "debug.hypothesis", actor, {"statement": item.statement}
        )
        return item

    def authorize_fix(self, actor: str) -> None:
        if not self.evidence:
            raise ValueError("fix authorization requires evidence")
        if not self.hypotheses:
            raise ValueError("fix authorization requires a hypothesis")
        self.ledger.append_artifact(self.run_id, "debug.authorization", actor, {"authorized": True})

    def record_attempt(
        self, approach: str, output: str, *, succeeded: bool, actor: str
    ) -> DebugAttempt:
        if not self.authorized:
            raise ValueError("fix attempts require authorization")
        item = DebugAttempt(approach.strip(), FailureSignature.normalize(output), succeeded)
        self.ledger.append_artifact(
            self.run_id,
            "debug.attempt",
            actor,
            {
                "approach": item.approach,
                "failure_signature": item.signature.value,
                "succeeded": succeeded,
            },
        )
        return item

    def verify(self, command: str, output: str, *, succeeded: bool, actor: str) -> None:
        if not self.attempts:
            raise ValueError("verification requires a fix attempt")
        self.ledger.append_artifact(
            self.run_id,
            "debug.verification",
            actor,
            {"command": command, "output": output, "succeeded": succeeded},
        )
