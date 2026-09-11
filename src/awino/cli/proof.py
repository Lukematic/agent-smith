"""owns: proof export, proof verify

Self-contained FAIR proof packs: `proof export` assembles mission, approved
plan hash, ledger trail, test outputs, outcome verdicts, and the compiled
brief into JSON + Markdown with a hash index; `proof verify` re-checks a
pack with no access to the original project.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import typer

from awino import proof as proof_lib
from awino.cli import _echo, _workspace
from awino.cli import brief as brief_mod

proof_app = typer.Typer(
    no_args_is_help=True,
    help="Export and verify self-contained FAIR proof packs.",
)


@proof_app.command("export")
def proof_export(
    out: str | None = typer.Option(
        None,
        "--out",
        help="Directory for the proof pack (default: <state>/proof/<timestamp>/).",
    ),
) -> None:
    """Assemble the FAIR proof pack from live project state."""
    workspace = _workspace()
    brief_lines = brief_mod._compile_brief(workspace.state_root, workspace.project.root, workspace)
    brief_text = "\n".join(brief_lines) + "\n"
    try:
        from awino.cli import _version

        generator = f"awino proof export {_version()}"
    except Exception:
        generator = "awino proof export"
    pack = proof_lib.build_pack(workspace.state_root, brief_text)
    out_dir = (
        Path(out).expanduser()
        if out
        else workspace.state_root / "proof" / datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    )
    proof_lib.write_pack(pack, out_dir, generator=generator)
    _echo(f"PROOF_EXPORT  {out_dir}  ({len(pack)} artifacts + index.json)")


@proof_app.command("verify")
def proof_verify(
    pack: str = typer.Argument(..., help="Proof pack directory to re-verify."),
) -> None:
    """Re-verify a proof pack with no access to the original project.

    Re-checks hashes against index.json, the ledger trail's internal
    consistency (event order, loop transitions), that every verdict
    references a real loop, and that the brief's claims trace to pack
    artifacts. Runs entirely from the pack directory.
    """
    # Deliberately no _workspace(): verification must not see the project.
    pack_dir = Path(pack).expanduser()
    failures = proof_lib.verify_pack(pack_dir)
    if failures:
        for failure in failures:
            _echo(f"FAIL  {failure}")
        _echo(f"PROOF INVALID  {len(failures)} problem(s) in {pack_dir}")
        raise typer.Exit(1)
    index_size = len(proof_lib.PACK_FILES)
    _echo(f"PROOF OK  {pack_dir}  ({index_size} artifacts re-verified)")
