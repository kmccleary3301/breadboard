Task: Fix linting and typing issues across a small Python package and ensure tests pass.

Requirements:
- Add and configure Ruff (lint + format) and Pyright (type checking) via pyproject.toml.
- Create a minimal package `pkg/` with:
  - `__init__.py` exposing a `version()` function
  - `calc.py` with functions: `add(a: int, b: int) -> int`, `div(a: int, b: int) -> float` (handle division by zero)
- Write unit tests in `tests/` (pytest):
  - Cover happy paths and error cases for `div`.
- Introduce common lint nits intentionally (unused imports, long lines, spacing) and then fix them via edits.
- Commands to run:
  - `pytest -q`
  - `ruff check . && ruff format --check .` (and `ruff format .` if needed)
  - `pyright`
- Goal: All three (tests, ruff, pyright) pass cleanly.


