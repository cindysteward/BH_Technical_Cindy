"""Verify that every business invariant is protected by a test.

``docs/invariants.md`` lists the rules the service must keep, each with
the tests that prove it. The script exits with an error when a listed
test no longer exists or when a ``test_invariant_*`` test is missing from
the list. Running it in CI turns a deleted or renamed test into a failed
build.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTER = ROOT / "docs" / "invariants.md"
TESTS = ROOT / "tests"
TEST_NAME = re.compile(r"`(test_\w+)`")


def listed_tests(register: Path) -> set[str]:
    """Read the test names mentioned in the invariant register.

    Args:
        register: Path to ``docs/invariants.md``.

    Returns:
        Every test name written in backticks in the register's table rows.
    """
    names: set[str] = set()
    for line in register.read_text().splitlines():
        if line.startswith("| I"):
            names.update(TEST_NAME.findall(line))
    return names


def existing_tests(folder: Path) -> set[str]:
    """Collect the names of all test functions in a folder.

    Args:
        folder: Folder with ``test_*.py`` files.

    Returns:
        Names of top-level functions starting with ``test_``.
    """
    names: set[str] = set()
    for path in folder.glob("test_*.py"):
        tree = ast.parse(path.read_text())
        names.update(
            node.name
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name.startswith("test_")
        )
    return names


def find_problems(listed: set[str], existing: set[str]) -> list[str]:
    """Compare the register with the test suite.

    Args:
        listed: Tests named in the register.
        existing: Tests that exist in the code.

    Returns:
        One message per problem. Empty when both sides agree.
    """
    problems = [
        f"{name} is listed in docs/invariants.md but does not exist"
        for name in sorted(listed - existing)
    ]
    unlisted = {
        name for name in existing - listed if name.startswith("test_invariant")
    }
    problems += [
        f"{name} is an invariant test missing from docs/invariants.md"
        for name in sorted(unlisted)
    ]
    return problems


def main() -> int:
    """Run the check and report the result.

    Returns:
        Exit code 0 when the register and tests agree, 1 otherwise.
    """
    listed = listed_tests(REGISTER)
    problems = find_problems(listed, existing_tests(TESTS))
    for problem in problems:
        print(f"✗ {problem}")
    if problems:
        return 1
    print(f"✓ {len(listed)} invariant tests found and registered")
    return 0


if __name__ == "__main__":
    sys.exit(main())
