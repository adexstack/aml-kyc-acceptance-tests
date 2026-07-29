"""Execute every acceptance-test notebook non-interactively, from a fresh kernel each.

Deliberately not a pytest plugin: the notebooks are the deliverable, and this
runner only starts them and reports. It imports nothing from any notebook, so
adding or removing a notebook needs no change here.

Usage:
    uv run python run_all.py                 # all notebooks
    uv run python run_all.py ToolCorrectness # substring filter
    uv run python run_all.py --timeout 1200

Exit status is the number of failed notebooks (0 = all passed), so CI can gate
on it directly.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import nbformat
from nbclient import NotebookClient
from nbclient.exceptions import CellExecutionError

NOTEBOOK_DIR = Path(__file__).parent / "notebooks"


def run_one(path: Path, timeout: int) -> tuple[bool, str, float]:
    """Execute one notebook. Returns (passed, detail, seconds)."""
    started = time.perf_counter()
    nb = nbformat.read(path, as_version=4)
    client = NotebookClient(
        nb,
        timeout=timeout,
        kernel_name="python3",
        allow_errors=False,
        # Run with the notebook's own directory as cwd so the .env lookup
        # ("next to the notebook, then one level up") behaves as documented.
        resources={"metadata": {"path": str(path.parent)}},
    )
    try:
        client.execute()
        return True, "", time.perf_counter() - started
    except CellExecutionError as exc:
        evalue = (getattr(exc, "evalue", "") or "").strip().splitlines()
        detail = evalue[0] if evalue else exc.__class__.__name__
        return False, f"{getattr(exc, 'ename', 'Error')}: {detail}", time.perf_counter() - started
    except Exception as exc:  # kernel launch failures, timeouts, bad JSON
        return False, f"{exc.__class__.__name__}: {exc}", time.perf_counter() - started


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("filter", nargs="?", default="",
                        help="only run notebooks whose filename contains this substring")
    parser.add_argument("--timeout", type=int, default=900,
                        help="per-cell timeout in seconds (default 900)")
    args = parser.parse_args()

    notebooks = sorted(p for p in NOTEBOOK_DIR.glob("*.ipynb")
                       if args.filter.lower() in p.name.lower())
    if not notebooks:
        print(f"No notebooks matched {args.filter!r} in {NOTEBOOK_DIR}", file=sys.stderr)
        return 1

    failures: list[tuple[str, str]] = []
    for path in notebooks:
        print(f"--> {path.name}", flush=True)
        passed, detail, seconds = run_one(path, args.timeout)
        if passed:
            print(f"    PASS  ({seconds:.0f}s)", flush=True)
        else:
            print(f"    FAIL  ({seconds:.0f}s)  {detail}", flush=True)
            failures.append((path.name, detail))

    print()
    print(f"{len(notebooks) - len(failures)}/{len(notebooks)} notebooks passed")
    for name, detail in failures:
        print(f"  FAILED  {name}: {detail}")
    return len(failures)


if __name__ == "__main__":
    raise SystemExit(main())
