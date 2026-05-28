# Blockers & Environment Decisions

This file documents constraints encountered during the autonomous build and the
decisions taken to work around them, per the project working principles.

## 1. Separate `backtesting-engine` GitHub repo could not be created

**Blocker.** The task asks for the Backtesting Engine to live in its own repo at
`github.com/JaredECohen/backtesting-engine`. In this execution environment:

- The `gh` CLI is not available / not authenticated.
- GitHub access is mediated by an MCP server scoped to a single repository
  (`JaredECohen/options-AI-assistant`). Calls targeting any other repository are
  denied, so `create_repository` for a new `backtesting-engine` repo is not
  possible from here.
- There is exactly one git remote (`options-AI-assistant`) and one writable
  branch (`claude/backtesting-engine-optionscoach-VURUl`).

**Decision (documented, non-blocking).** The Backtesting Engine is built as a
**self-contained, independently-installable Python package** vendored inside this
monorepo under [`backtesting-engine/`](./backtesting-engine/). It has its own
`pyproject.toml`, `src/` layout, tests, README, LICENSE, and CI workflow — i.e.
it is ready to be split out into its own repo with `git subtree split` /
`git filter-repo` at any time, with zero code changes.

OptionsCoach.AI depends on it via a **local editable/path install** rather than a
git URL. When the standalone repo is created, switch the dependency line in
`pyproject.toml` to:

```toml
"backtesting-engine @ git+https://github.com/JaredECohen/backtesting-engine"
```

To publish the engine as its own repo once `gh` is available:

```bash
git subtree split --prefix=backtesting-engine -b engine-export
# create the empty remote, then:
git push git@github.com:JaredECohen/backtesting-engine.git engine-export:main
```

## 2. No live API credentials (`FMP_API_KEY`, `ALPHA_VANTAGE_API_KEY`)

Not set in this environment. This is expected and by design: **all unit tests use
the deterministic `SyntheticOptionsAdapter`** and run with no network or
credentials. Live adapters (`FMPEquityAdapter`, `AlphaVantageOptionsAdapter`) are
fully implemented and exercised by `tests/integration/`, which are skipped unless
the relevant env var is present.

## 3. GCP / Cloud Run deploy of the Streamlit pages

Deploying the new Streamlit UI to a live URL requires GCP credentials and a
project, which are not available here. The pages are implemented and run locally
via `streamlit run apps/streamlit/Home.py`; deployment is left as a follow-up.
