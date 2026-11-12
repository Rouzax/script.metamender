## What and why

<!-- What changes for users or for the code, and why it has to be this way. One line per paragraph: GitHub renders single newlines as line breaks. -->

## Validation

- [ ] `python3 -m pytest tests/ -q`, `ruff check resources/lib/` and pyflakes (see CONTRIBUTING.md)
- [ ] Tried on a real Kodi, or why not:

## Checklist

- [ ] Docs page (`docs/`) and `strings.po` updated for anything a user sees
- [ ] A failed lookup never counts as a check (see "The maturity clock" in CONTRIBUTING.md)
- [ ] No API keys, private names, paths or hostnames in code, tests, logs or this description
