# Contributing to MetaMender

Thank you for your interest in MetaMender. This page explains how to report a
problem, suggest a feature, and work on the code.

MetaMender is maintained in my spare time. Response times vary, and I may not
get to every request quickly; thank you for your patience.

## Reporting a problem

Open a [bug report](https://github.com/Rouzax/script.metamender/issues/new?template=bug_report.yml).
The form asks for what helps most:

1. The MetaMender and Kodi versions, and the scraper that made the item.
2. One example item: which field, what it shows, and what you expected.
3. A screenshot of that item's **Details window** (context menu, MetaMender,
   Details), which shows where the value came from and why it was left alone.
4. A debug log, if you can: see
   [Troubleshooting](https://rouzax.github.io/script.metamender/docs/troubleshooting/#reporting-a-problem).

Questions and setup help are welcome in the
[Kodi forum thread](https://forum.kodi.tv/showthread.php?tid=389809).

## Suggesting a feature

Open a [feature request](https://github.com/Rouzax/script.metamender/issues/new?template=feature_request.yml)
describing what MetaMender should do and what problem in your library it
solves. I read every suggestion, but cannot promise each will be built.

A new country for age ratings is a good contribution: it is a preset file in
`resources/presets/` (its rating list, prefix, the order of countries to
convert from, and a conversion table from each of them). Open an issue first,
with a source for the rating system.

## Pull requests

1. Open an issue first to discuss the change.
2. Fork the repository and create a branch.
3. Make the change with tests, and update the docs (`docs/`) and
   `resources/language/resource.language.en_gb/strings.po` for anything a user
   sees.
4. Run the checks below, and try the change on a real Kodi if you can.
5. Open the pull request; the template lists what to confirm.

Please keep a pull request to one fix or feature.

## Issue labels and milestones

Labels say what an issue is or what it is waiting on. Milestones say when it is
expected to ship.

| Label | Meaning |
|-------|---------|
| `bug` | Something isn't working. Set by the bug report form. |
| `enhancement` | A new feature or improvement. Set by the feature request form. |
| `age-ratings` | Age ratings, a country preset or its conversions. |
| `documentation` | The docs need changing, not the add-on. |
| `needs-info` | Waiting on the reporter. The issue may be closed if the details never arrive. |
| `needs-upstream` | Waiting on TMDB, TheTVDB, IMDb's data or Kodi; MetaMender cannot fix it alone. |
| `duplicate`, `invalid`, `wontfix` | Why an issue was closed without a change. |

| Milestone | Meaning |
|-----------|---------|
| Next | Planned for the coming release. At release it is renamed to that version and closed, and a new Next is opened. |
| Later | Accepted, but not planned for a particular release yet. |

An issue without a milestone has not been triaged yet. The label list lives in
[.github/sync-labels.sh](.github/sync-labels.sh); change labels there and run
`bash .github/sync-labels.sh` rather than editing them on GitHub.

## Development

### Requirements

- Python 3.10 or newer. Kodi 21 runs 3.11, but some Linux builds use the
  system's Python, so the code stays compatible with 3.10: no `typing.Self`,
  `tomllib`, `except*` or `StrEnum`.
- For the checks: `pytest`, `Pillow` (for the art tests), `ruff`, `pyflakes`
  and `pyright`.
- For the docs site: Zensical, installed from the hash-locked
  `requirements-docs.lock` (see the header of `requirements-docs.txt`).

### Checks

The unit tests run without Kodi: `tests/conftest.py` stubs Kodi's modules, and
no test touches the network.

```bash
python3 -m pytest tests/ -q
ruff check resources/lib/
python3 -m pyflakes $(find resources -name "*.py" -not -path "*/__pycache__/*") default.py
pyright resources/lib/path/to/changed_module.py
zensical build --clean --strict          # when docs/ changed
```

### Trying it in Kodi

`python3 docker/scripts/stage.py folder <kodi>/addons/script.metamender`
copies exactly what the add-on ships into a Kodi profile. `docker/README.md`
describes the test rigs (a headless Kodi and the Flatpak desktop Kodi) and the
live and matrix tests that run against them; they need a test library and are
not part of the unit tests.

### Rules the code follows

- **One outbound HTTP path.** Every request to a site goes through
  `resources/lib/providers/http.py` (`provider_get` / `provider_post`), so the
  circuit breaker sees it and "not found" is told apart from "not reachable" in
  one place.
- **The maturity clock.** A field's next check is decided from how often it was
  really answered and the content's age (`engine/maturity.py`). A lookup that
  failed (a site down, a timeout, a refused write) must never count as a check:
  it yields the transient result and leaves the clock alone, or the field is
  settled for good without ever being checked.
- **Never overwrite what is not ours.** On an item with an NFO file, and for a
  text field in a folder without a language, only empty values are filled
  (`engine/protect.py`); a locked field is skipped entirely (`engine/scan.py`).
- **Structured logging:** a fixed message with `event=` and fields on every
  INFO, WARNING and ERROR line; `log.exception()` inside `except`.
- **Strings** shown to the user live in `strings.po`.

### Docs, screenshots and art

- The docs site is `docs/` (Zensical) plus the landing page in `site/`;
  `tests/test_docs_site.py` and `tests/test_landing_page.py` check them.
- Screenshots are retaken with `python3 docker/scripts/screenshots.py`
  (see `docker/README.md`); every window needs a scene.
- The icon, window textures, wordmark, banner and fanart are drawn by
  `python3 docs/art/build_art.py`.

### Project structure

```
script.metamender/
├── addon.xml, default.py, service.py, context.py, context_details.py
├── resources/
│   ├── lib/
│   │   ├── data/       # Kodi JSON-RPC, the sqlite tracker, sources, presets, NFO detection
│   │   ├── engine/     # the scan, maturity, text and certification chains, number matching
│   │   ├── providers/  # TMDB, TheTVDB, IMDb ratings, FSK, BBFC, Medieraadet
│   │   ├── service/    # the background service, Update now, Details, setup, attention
│   │   └── ui/         # the windows and their models
│   ├── presets/        # one age rating system per country
│   ├── skins/          # the windows' XML and textures
│   └── language/       # strings.po
├── tests/
├── docs/, site/        # documentation and landing page
└── docker/             # test rigs, screenshots, packaging (stage.py)
```

## Licence

By contributing you agree that your contribution is licensed under the
GPL-3.0-only licence of this project ([LICENSE.txt](LICENSE.txt)).
