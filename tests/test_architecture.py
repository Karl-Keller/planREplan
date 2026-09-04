"""Fitness tests for the hard design rules in CLAUDE.md.

These rules are load-bearing and stated as prose elsewhere; here they are
executable. Rule 1 (solver isolation) and rule 6 (LLM outputs are proposals,
so no core package may import ``llm``) are both enforced by construction: a
violation fails the suite rather than waiting to be noticed in review.
"""

from __future__ import annotations

import ast
import pathlib
import subprocess
import sys
import textwrap

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "planreplan"

#: Packages that must remain free of both OR-Tools and the LLM adapter.
CORE_PACKAGES = frozenset({"domain", "cpm", "monitor", "repair", "io"})


def _top_level_imports(path: pathlib.Path) -> set[str]:
    """Root module name of every absolute import in a file."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.split(".")[0])
    return found


def _modules_in(package: str | None = None):
    """Yield (relative path, top-level imports) for each module under src."""
    for py in sorted(SRC.rglob("*.py")):
        parts = py.relative_to(SRC).parts
        owner = parts[0] if len(parts) > 1 else None
        if package is None or owner == package:
            yield py.relative_to(SRC), _top_level_imports(py)


def test_only_solve_imports_ortools():
    """Design rule 1: solver isolation."""
    offenders = [
        str(rel)
        for rel, imports in _modules_in()
        if rel.parts[0] != "solve" and "ortools" in imports
    ]
    assert not offenders, f"ortools imported outside solve/: {offenders}"


def test_core_packages_do_not_import_the_llm_adapter():
    """Design rule 6: the proposal gate cannot be bypassed by an import."""
    offenders = []
    for package in sorted(CORE_PACKAGES):
        for rel, imports in _modules_in(package):
            # `from planreplan.llm import ...` lands as a `planreplan` root,
            # so inspect the full module path rather than just the root.
            source = (SRC / rel).read_text(encoding="utf-8")
            if "planreplan.llm" in source or "from planreplan import llm" in source:
                offenders.append(str(rel))
            assert "anthropic" not in imports, f"{rel} imports anthropic directly"
    assert not offenders, f"core packages importing llm/: {offenders}"


def test_every_package_in_the_documented_layout_exists():
    """CLAUDE.md pins the repo layout; drift here is drift in the architecture."""
    expected = CORE_PACKAGES | {"solve", "llm"}
    actual = {p.name for p in SRC.iterdir() if p.is_dir() and (p / "__init__.py").exists()}
    assert expected == actual, (
        f"layout drift: missing={expected - actual}, extra={actual - expected}"
    )


def test_core_packages_import_with_ortools_unavailable():
    """Design rule 1, enforced at runtime rather than by reading imports.

    An AST scan cannot see a transitive import. This blocks ``ortools`` at the
    meta path and then imports the packages that must survive without it —
    which is exactly the promise ADR-2 makes about keeping the backend seam
    real, and the reason ortools is an optional dependency.
    """
    program = textwrap.dedent(
        """
        import sys

        class _Blocker:
            def find_spec(self, fullname, path=None, target=None):
                if fullname == "ortools" or fullname.startswith("ortools."):
                    raise ImportError("ortools is unavailable in this test")
                return None

        sys.meta_path.insert(0, _Blocker())

        import planreplan
        import planreplan.domain
        import planreplan.cpm
        import planreplan.monitor
        import planreplan.repair
        import planreplan.io
        import planreplan.cli
        print("ok")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", program], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, f"core packages need ortools:\n{result.stderr}"
    assert "ok" in result.stdout
