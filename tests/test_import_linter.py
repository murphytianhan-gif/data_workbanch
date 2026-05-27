"""§11 seam guard — engine MUST NOT import storage implementations.

Runs the ``lint-imports`` CLI declared in pyproject.toml under the same
process so a regression surfaces as a normal pytest failure.
"""
from __future__ import annotations

import shutil
import subprocess


def test_lint_imports_contract_kept():
    binary = shutil.which("lint-imports")
    assert binary is not None, "lint-imports not on PATH — did `pip install -e .[dev]` run?"
    result = subprocess.run(
        [binary], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, (
        f"import-linter failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "Contracts: 1 kept, 0 broken." in result.stdout
