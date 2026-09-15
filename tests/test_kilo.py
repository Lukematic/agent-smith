"""Phase 4: Kilo host adapter.

Kilo is a VS Code extension with no host present in this sandbox and no
extension API documented in this tree. These tests pin the honest
contract: the adapter is a separate class with its own detection and its
own evidence file, a missing host yields an ``unverified`` report (never
a crash, never a silent pass), and unknown host names raise
``UnknownHost``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from awino.hosts import Evidence, UnknownHost, get_adapter, supported_hosts
from awino.hosts.base import HostAdapter
from awino.hosts.kilo import KiloAdapter


class TestAdapterIdentity:
    def test_kilo_adapter_is_registered(self):
        adapter = get_adapter("kilo")
        assert isinstance(adapter, KiloAdapter)
        assert isinstance(adapter, HostAdapter)

    def test_each_host_has_its_own_adapter_class(self):
        assert type(get_adapter("kilo")) is not type(get_adapter("claude_code"))
        assert type(get_adapter("kilo")) is not type(get_adapter("roo"))
        assert get_adapter("kilo").HOST == "kilo"

    def test_supported_hosts_lists_all_three(self):
        assert supported_hosts() == ["claude_code", "kilo", "roo"]

    def test_unknown_host_raises_never_silent(self):
        with pytest.raises(UnknownHost) as excinfo:
            get_adapter("notahost")
        assert "notahost" in str(excinfo.value)
        assert "kilo" in excinfo.value.supported


class TestMissingHostHonesty:
    def test_kilo_not_detected_in_this_sandbox(self):
        assert get_adapter("kilo").detect_live() is False

    def test_status_reports_unverified_never_parity(self):
        status = get_adapter("kilo").status()
        assert status.host == "kilo"
        assert status.detected_live is False
        assert status.evidence == Evidence.UNVERIFIED
        assert status.evidence != Evidence.LIVE
        assert status.missing_apis, "the missing surface must be spelled out"
        assert status.checked_at, "the report must be timestamped"

    def test_status_never_raises(self):
        adapter = get_adapter("kilo")
        adapter.detect_live = lambda: (_ for _ in ()).throw(OSError("disk gone"))
        status = adapter.status()
        assert status.evidence == Evidence.UNVERIFIED


class TestPerHostEvidence:
    def test_evidence_file_is_namespaced_per_host(self, tmp_path: Path):
        kilo = get_adapter("kilo")
        roo = get_adapter("roo")
        assert kilo.evidence_file(tmp_path).parent.name == "kilo"
        assert kilo.evidence_file(tmp_path) != roo.evidence_file(tmp_path)

    def test_record_and_read_evidence_round_trip(self, tmp_path: Path):
        adapter = get_adapter("kilo")
        assert adapter.read_evidence(tmp_path) == []
        path = adapter.record_evidence(
            tmp_path,
            session_id="sess-1",
            evidence=Evidence.DOUBLE_DRIVEN,
            boundaries=["session_start", "user_turn", "tool_result"],
            detail="faithful double journey",
        )
        assert path.is_file()
        records = adapter.read_evidence(tmp_path)
        assert len(records) == 1
        record = records[0]
        assert record["host"] == "kilo"
        assert record["evidence"] == Evidence.DOUBLE_DRIVEN
        assert record["boundaries"] == ["session_start", "user_turn", "tool_result"]
        assert record["at"], "evidence must be timestamped"

    def test_invalid_evidence_label_is_rejected(self, tmp_path: Path):
        with pytest.raises(ValueError, match="evidence must be"):
            get_adapter("kilo").record_evidence(
                tmp_path,
                session_id="s",
                evidence="definitely-live-trust-me",
                boundaries=[],
            )

    def test_evidence_appends_does_not_overwrite(self, tmp_path: Path):
        adapter = get_adapter("kilo")
        adapter.record_evidence(
            tmp_path, session_id="a", evidence=Evidence.UNVERIFIED, boundaries=[]
        )
        adapter.record_evidence(
            tmp_path, session_id="b", evidence=Evidence.UNVERIFIED, boundaries=[]
        )
        assert len(adapter.read_evidence(tmp_path)) == 2
