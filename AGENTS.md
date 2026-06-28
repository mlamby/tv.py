# AGENTS.md

Guidance for coding agents working on this repository.

## Project Intent

`tv.py` is a copyable, single-file Python 3.9+ terminal dashboard framework for
real-time engineering telemetry. It is intentionally not a general terminal UI
toolkit. Keep the API small, explicit, and dependency-free.

The original design brief lives in [docs/design.md](docs/design.md). Check it
before making changes that affect architecture, public API, keyboard behavior,
layout, rendering, or widget state.

## Repository Map

- `tv.py`: the framework implementation and public API.
- `example.py`: runnable demo application.
- `tests/test_tv.py`: regression tests for rendering, layout, focus, key
  handling, widgets, and public exports.
- `docs/design.md`: design intent and v1 scope.
- `pyproject.toml`: lint, type-check, and pytest configuration.

## Engineering Guidelines

- Keep `tv.py` self-contained and free of runtime third-party dependencies.
- Prefer explicit object-tree APIs over hidden framework magic.
- Preserve the application model: the app owns domain data and the main loop;
  widgets own presentation state.
- Treat widths and clipping in terminal display cells, not Python string length.
- Use text Unicode symbols plus ANSI styles for built-in glyphs. Be cautious
  with colorful emoji because terminal width handling varies.
- Avoid broad refactors unless they directly support the requested change.
- When adding public API, update `__all__`, inline docstrings, README examples
  if relevant, and tests.
- Do not introduce background threads for data handling.

## Verification

For code changes, run:

```sh
python -m py_compile tv.py
pytest -q
```

For documentation-only changes, at least inspect the changed Markdown for
correct links and stale references.
