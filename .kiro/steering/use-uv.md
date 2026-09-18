---
inclusion: always
---

# Project guidance

## Environment and dependencies

- Use `uv` for package management, environments, and command execution. Run project tools as `uv run ...`; do not use `pip`, Poetry, Conda, or manually managed environments.
- Target Python 3.12 (`requires-python = ">=3.12,<4.0"`). Keep runtime and optional test/development dependencies in `pyproject.toml` with exact pins.
- Use `uv add --exact` or `uv remove` for dependency changes and keep any lockfile synchronized with `uv lock`; never edit generated lock data manually.
- Set up the development environment with `uv sync --extra dev`. Before handing off changes, run `uv run pytest`, `uv run ruff check .`, and `uv run mypy src`.

## Code and architecture

- Preserve the `src/mc03` package layout. Place business concepts in `domain`, persistence code in `persistence`, and orchestration/integration code in `services`; keep startup configuration in `settings.py` and protected filesystem rules in `storage.py`.
- Use Python type annotations and public-module, class, and function docstrings. Follow the configured Ruff rules (`E`, `F`, `I`, `B`, `UP`), strict mypy checking, and the 100-character line limit.
- Keep runtime configuration typed and validated through `RuntimeSettings`. Preserve its `MC03_` environment prefix, `__` nested delimiter, startup validation, and rejection of unknown settings. Treat database, raw-source, artifact, template, and lock paths as explicit configuration boundaries.

## Persistence and tests

- Persistence is single-host and local. Keep every persistence path below one protected storage root, validate all paths before creating directories, and use `build_protected_storage_paths` and `prepare_protected_storage` rather than bypassing them. Reject UNC and known network-mounted paths; preserve restrictive directory permissions.
- Put tests under `tests/` and use pytest fixtures such as `tmp_path` for isolated filesystem behavior. Cover both successful configuration and rejected invalid or unsafe inputs, then run a targeted test before the full suite.
- Keep secrets out of source control; use `.env.example` as the configuration reference and local `.env` only for development values.
