# RSS Brief fidelity eval

A [promptfoo](https://promptfoo.dev) eval that continuously checks the **RSS Brief**
against the **digest** it was synthesised from. It drives the real Python pipeline
(`rss_email.brief_generator.synthesize`) via a custom provider, so it measures the
shipping code rather than a copy of the prompt.

## What it checks

| Assertion | Type | Checks |
| --- | --- | --- |
| `is-json` | built-in | The synthesis output is valid JSON. |
| `schema.js` | deterministic | All category keys are canonical (no `AI_ML`), `AI/ML` is present, every `signal_strength` is valid. |
| `major_coverage.js` | deterministic | Every story in the fixture's `expected_major.json` is surfaced — major news is not dropped. |
| `source_ranking.js` | deterministic | High-tier sources (blogs / Hacker News / Reddit) are not under-represented vs the digest. Tiers come from `src/rss_email/brief_config.json`. |
| faithfulness | llm-rubric | Every claim/number/entity in the brief is supported by the digest — nothing fabricated. |
| relevance | llm-rubric | `relevance_to_reader` is null or genuinely tied to the profile, never forced. |

## Run

```bash
uv sync --dev               # creates .venv with anthropic/pydantic (used by the provider)
npm install                 # installs promptfoo (devDependency)
export ANTHROPIC_API_KEY=sk-...
npm run eval                # runs eval/promptfooconfig.yaml (PROMPTFOO_PYTHON=.venv/bin/python)
npm run eval:view           # opens the promptfoo web UI for the last run
```

Each run makes a handful of Claude calls (one synthesis per fixture + the llm-rubric
grader), so it is not wired into the per-push CI gate. It runs weekly and on demand via
[`.github/workflows/eval.yml`](../.github/workflows/eval.yml).

## Add a fixture

A fixture is one day's digest in the same shape `build_synthesis_input` produces:

```
eval/fixtures/<YYYY-MM-DD>/
  digest_articles.json   # { "AI/ML": [ {title, url, summary, source}, ... ], ... }
  expected_major.json    # { "must_cover": [ "<exact major-story title>", ... ] }
  digest_text.txt        # readable digest used as the faithfulness grader's source
```

The quickest way to capture one is from a live run:

```bash
# Dump the categorised synthesis input the brief actually received.
uv run python src/cli_brief_generator.py --dry-run --days 1 --debug
```

Then add a matching `tests:` entry in `promptfooconfig.yaml`.
