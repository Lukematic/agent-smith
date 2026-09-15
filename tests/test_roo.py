"""Phase 4: Roo Code host adapter.

Roo Code is a VS Code extension with no host present in this sandbox and
no extension API documented in this tree. Mirrors the Kilo contract:
separate adapter class, its own evidence file, ``unverified`` on a
missing host — never a crash, never a silent pass.
"""

from __future__ import annotations

from pathlib import Path

from awino.hosts import Evidence, get_adapter
from awino.hosts.base import HostAdapter
from awino.hosts.roo import RooAdapter


class TestAdapterIdentity:
    def test_roo_adapter_is_registered(self):
        adapter = get_adapter("roo")
        assert isinstance(adapter, RooAdapter)
        assert isinstance(adapter, HostAdapter)
        assert adapter.HOST == "roo"

    def test_roo_adapter_is_independent(self):
        assert type(get_adapter("roo")) is not type(get_adapter("kilo"))
        assert type(get_adapter("roo")) is not type(get_adapter("claude_code"))


class TestMissingHostHonesty:
    def test_roo_not_detected_in_this_sandbox(self):
        assert get_adapter("roo").detect_live() is False

    def test_status_reports_unverified_never_parity(self):
        status = get_adapter("roo").status()
        assert status.host == "roo"
        assert status.detected_live is False
        assert status.evidence == Evidence.UNVERIFIED
        assert status.evidence != Evidence.LIVE
        assert status.missing_apis
        assert "Roo" in " ".join(status.missing_apis)


class TestPerHostEvidence:
    def test_evidence_file_is_namespaced_per_host(self, tmp_path: Path):
        roo = get_adapter("roo")
        assert roo.evidence_file(tmp_path).parent.name == "roo"
        assert "roo" in str(roo.evidence_file(tmp_path))

    def test_record_and_read_evidence_round_trip(self, tmp_path: Path):
        adapter = get_adapter("roo")
        adapter.record_evidence(
            tmp_path,
            session_id="sess-9",
            evidence=Evidence.DOUBLE_DRIVEN,
            boundaries=["session_start"],
            detail="faithful double journey",
        )
        records = adapter.read_evidence(tmp_path)
        assert len(records) == 1
        assert records[0]["host"] == "roo"
        assert records[0]["evidence"] == Evidence.DOUBLE_DRIVEN
