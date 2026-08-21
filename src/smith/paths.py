"""Filesystem layout for Agent Smith.

Two roots, deliberately separated:

- **Smith home** holds knowledge, skills, doctrine, and cross-project lessons.
  It is shared and must never be forked per project.
- **Target project** holds the run ledger, plans, and project-local memory. It is
  whatever repository the work is actually about.

Conflating them causes the two symmetric failures: writing a project's ledger into
Smith pollutes the shared install, and copying Smith into each project forks the
knowledge base. One place knows the difference, and that is this module.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

HOME_MARKERS = ("plugin.json", "knowledge")
PROJECT_MARKERS = (
    ".git",
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
    ".seeds",
    ".goosehints",
)


@dataclass(frozen=True)
class SmithPaths:
    """Resolved locations inside a Smith installation."""

    root: Path

    @classmethod
    def discover(cls, start: Path | None = None) -> SmithPaths:
        """Find the Smith home directory.

        Resolution order, most explicit first:

        1. ``SMITH_HOME`` if set, so a caller can always be unambiguous.
        2. Walking upward from ``start``, which succeeds when invoked inside Smith.
        3. The package's own location, which succeeds when Smith is installed as a
           plugin and invoked from an unrelated project.
        """
        override = os.environ.get("SMITH_HOME")
        if override:
            candidate = Path(override).expanduser().resolve()
            if cls._is_home(candidate):
                return cls(root=candidate)

        here = (start or Path.cwd()).resolve()
        for candidate in (here, *here.parents):
            if cls._is_home(candidate):
                return cls(root=candidate)

        # src/smith/paths.py -> src/smith -> src -> home
        return cls(root=Path(__file__).resolve().parents[2])

    @staticmethod
    def _is_home(candidate: Path) -> bool:
        return all((candidate / marker).exists() for marker in HOME_MARKERS)

    # ── knowledge ────────────────────────────────────────────────────────────
    @property
    def knowledge(self) -> Path:
        return self.root / "knowledge"

    @property
    def registry(self) -> Path:
        return self.knowledge / "REGISTRY.yaml"

    @property
    def sources(self) -> Path:
        return self.knowledge / "SOURCES.yaml"

    @property
    def cache(self) -> Path:
        return self.knowledge / "cache"

    @property
    def manifest(self) -> Path:
        return self.knowledge / "MANIFEST.json"

    @property
    def drift_report(self) -> Path:
        return self.knowledge / "DRIFT.md"

    # ── memory ───────────────────────────────────────────────────────────────
    @property
    def memory(self) -> Path:
        return self.root / "memory"

    @property
    def lessons(self) -> Path:
        return self.memory / "lessons.md"

    @property
    def expertise(self) -> Path:
        return self.memory / "expertise"

    @property
    def session_log(self) -> Path:
        return self.memory / "SESSION_LOG.md"

    # ── authored artifacts ───────────────────────────────────────────────────
    @property
    def skills(self) -> Path:
        return self.root / "skills"

    @property
    def agents(self) -> Path:
        return self.root / "agents"

    @property
    def emitted(self) -> Path:
        return self.root / "emitted"

    @property
    def specs(self) -> Path:
        return self.root / "specs"

    @property
    def archive(self) -> Path:
        return self.root / "archive"

    @property
    def docs(self) -> Path:
        return self.root / "docs"

    def ensure_scaffold(self) -> list[Path]:
        """Create every directory Smith expects. Returns those newly created."""
        wanted = [
            self.knowledge,
            self.cache,
            self.memory,
            self.expertise,
            self.skills,
            self.agents,
            self.emitted,
            self.specs,
            self.archive,
            self.docs,
        ]
        created = [d for d in wanted if not d.exists()]
        for d in wanted:
            d.mkdir(parents=True, exist_ok=True)
        gitignore = self.cache / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text("*\n!.gitignore\n", encoding="utf-8")
        return created


@dataclass(frozen=True)
class ProjectPaths:
    """The repository the work is actually about.

    Distinct from Smith home. Runs, plans, and project-local memory live here so
    that a shared Smith install never accumulates one project's state, and a
    project never needs a copy of Smith.
    """

    root: Path
    is_smith_home: bool = False

    @classmethod
    def discover(cls, start: Path | None = None, home: SmithPaths | None = None) -> ProjectPaths:
        """Find the repository containing ``start``.

        ``SMITH_PROJECT`` overrides everything, which matters when an agent runs
        with a working directory that is not the project it is editing.

        Smith home is skipped as a candidate unless nothing else matches. Smith has
        its own ``pyproject.toml`` and ``.git``, so a naive upward walk from inside
        it would always stop there and every project would look like Smith itself.
        """
        override = os.environ.get("SMITH_PROJECT")
        if override:
            resolved = Path(override).expanduser().resolve()
            return cls(root=resolved, is_smith_home=cls._same(resolved, home))

        here = (start or Path.cwd()).resolve()
        fallback: Path | None = None
        for candidate in (here, *here.parents):
            if not any((candidate / marker).exists() for marker in PROJECT_MARKERS):
                continue
            if cls._same(candidate, home):
                # Remember it, but keep looking outward for the real project.
                fallback = fallback or candidate
                continue
            return cls(root=candidate, is_smith_home=False)

        if fallback is not None:
            # Only Smith home matched, so the work genuinely is on Smith.
            return cls(root=fallback, is_smith_home=True)

        # No repository markers anywhere: treat the current directory as the
        # project rather than silently falling back to Smith home, which would
        # write another project's state into the shared install.
        return cls(root=here, is_smith_home=cls._same(here, home))

    @staticmethod
    def _same(candidate: Path, home: SmithPaths | None) -> bool:
        if home is None:
            return False
        try:
            return candidate.resolve() == home.root.resolve()
        except OSError:
            return False

    @property
    def name(self) -> str:
        return self.root.name

    # ── project-local state ──────────────────────────────────────────────────
    @property
    def smith_dir(self) -> Path:
        """Where Smith keeps this project's state. Gitignorable, disposable."""
        return self.root / ".smith"

    @property
    def runs(self) -> Path:
        return self.smith_dir / "run"

    @property
    def memory(self) -> Path:
        """Project-local memory. Conventions here, doctrine in Smith home."""
        return self.smith_dir / "memory"

    @property
    def project_lessons(self) -> Path:
        return self.memory / "lessons.md"

    @property
    def thoughts(self) -> Path:
        """RPI output. Committed, because it documents this codebase."""
        return self.root / "thoughts"

    @property
    def research(self) -> Path:
        return self.thoughts / "research"

    @property
    def plans(self) -> Path:
        return self.thoughts / "plans"

    @property
    def seeds(self) -> Path:
        return self.root / ".seeds"

    def ensure_state(self) -> list[Path]:
        """Create the project-local directories Smith writes to."""
        wanted = [self.smith_dir, self.runs, self.memory]
        created = [d for d in wanted if not d.exists()]
        for d in wanted:
            d.mkdir(parents=True, exist_ok=True)
        gitignore = self.smith_dir / ".gitignore"
        if not gitignore.exists():
            # Runs are disposable evidence; project lessons are worth keeping.
            gitignore.write_text("run/\n", encoding="utf-8")
        return created


@dataclass(frozen=True)
class Workspace:
    """Smith home plus the project under work. The pair every command needs."""

    home: SmithPaths
    project: ProjectPaths

    @classmethod
    def discover(cls, start: Path | None = None) -> Workspace:
        home = SmithPaths.discover(start)
        project = ProjectPaths.discover(start, home=home)
        return cls(home=home, project=project)

    @property
    def working_on_self(self) -> bool:
        """True when Smith is the project, which is its own special case.

        Developing Smith is legitimate. Silently treating an unrelated project as
        Smith is not, so the distinction is explicit rather than incidental.
        """
        return self.project.is_smith_home

    @property
    def nested_install(self) -> bool:
        """True when Smith home sits directly inside the project it is working on.

        This is the ordinary layout: a `.smith/` home beside the project's other
        directories. It matters because the project's state directory and Smith
        home would otherwise be the same path.
        """
        try:
            return self.project.smith_dir.resolve() == self.home.root.resolve()
        except OSError:
            return False

    @property
    def state_root(self) -> Path:
        """Where this project's runs and local memory live.

        Normally the project's own `.smith/`. When Smith home *is* that directory,
        state moves to `<home>/state/` instead. Writing a project's ledger into
        Smith's own knowledge or memory directories would mix shared doctrine with
        one repository's history, which is the failure this resolves.
        """
        if self.nested_install:
            return self.home.root / "state"
        return self.project.smith_dir

    @property
    def runs(self) -> Path:
        return self.state_root / "run"

    @property
    def project_memory(self) -> Path:
        return self.state_root / "memory"

    def ensure_state(self) -> list[Path]:
        wanted = [self.state_root, self.runs, self.project_memory]
        created = [d for d in wanted if not d.exists()]
        for d in wanted:
            d.mkdir(parents=True, exist_ok=True)
        gitignore = self.state_root / ".gitignore"
        if not gitignore.exists():
            # Runs are disposable evidence; project lessons are worth keeping.
            gitignore.write_text("run/\n", encoding="utf-8")
        return created

    def describe(self) -> str:
        if self.working_on_self:
            return f"home={self.home.root.name} project=itself"
        layout = "nested" if self.nested_install else "sibling"
        return f"home={self.home.root.name} project={self.project.name} ({layout})"
