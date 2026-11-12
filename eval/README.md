# RSS Brief fidelity eval

A [promptfoo](https://promptfoo.dev) eval that continuously checks the **RSS Brief**
against the **digest** it was synthesised from. It drives the real Python pipeline
(`rss_email.brief_generator.synthesize`) via a custom provider, so it measures the
shipping code rather than a copy of the prompt.

## What it checks

| Assertion | Type | Checks |
| --- | --- | --- |
| `is-json` | built-in | The synthesis output is valid JSON. |
| `schema.cjs` | deterministic | All category keys are canonical (no `AI_ML`), `AI/ML` is present, every `signal_strength` is valid. |
| `major_coverage.cjs` | deterministic | Every story in the fixture's `expected_major.json` is surfaced — major news is not dropped. |
| `source_ranking.cjs` | deterministic | High-tier sources (blogs / Hacker News / Reddit) are not under-represented vs the digest. Tiers come from `../src/rss_email/brief_config.json`. |
| faithfulness | llm-rubric | Every claim/number/entity in the brief is supported by the digest — nothing fabricated. |
| relevance | llm-rubric | `relevance_to_reader` is null or genuinely tied to the profile, never forced. |

## Comparing models and effort (cost work)

`promptfooconfig.yaml` runs the **same fixtures through the real pipeline** under several
`providers` (`opus-5-5/high` = production baseline, `opus-5-5/medium`, `sonnet-5-5/high`,
`sonnet-5-5/medium`). `provider.py` applies each provider's `config` (`model`, `effort`,
`max_tokens`) over `brief_config.json`, captures token usage from the pipeline's
`CLAUDE_USAGE` log lines and reports cost from `prices.json` (update it when prices change).
Every cell is repeated 3 times (`evaluateOptions.repeat`), because a brief that drops a major
story one run in three is the failure to catch.

```bash
npm --prefix eval run eval -- --output results.json          # full matrix
python eval/summarise.py eval/results.json --baseline opus-5-5/high
```

`summarise.py` prints, per variant, runs passed, per-assertion pass rate, mean output tokens,
mean cost and cost relative to baseline. **Adopt a cheaper variant only if it passes every run**
on every fixture. Then change `brief_config.json` (`model`, `effort`, `max_tokens`).

Cost: one cell is a synthesis plus two grader calls. Roughly, an Opus synthesis is
~10k input / 15-25k output tokens (about $0.35-0.55 at $4/$20 per million), a Sonnet 5.5 one
about half that, and each grader call a few cents. The full matrix (4 variants x 2 fixtures x
3 repeats = 24 cells) is therefore in the region of $6-10; those are estimates until the
`CLAUDE_USAGE` log or a first run gives real figures. Narrow a run with
`--filter-providers '^sonnet-5-5/high$' --repeat 1`.

### Haiku 4.5 -> 5.5 assessment (grouping and categorising)

`compare_haiku.py` decides whether the production Haiku stages can move to `claude-haiku-5-5`.
It needs a real day's `batch-metadata` file (articles plus the groups production used):

```bash
aws s3 cp s3://<bucket>/batch-metadata/batch-<timestamp>.json /tmp/batch.json
export ANTHROPIC_API_KEY=sk-...
uv run python eval/compare_haiku.py /tmp/batch.json --out /tmp/haiku-report.json \
    --write-fixtures eval/fixtures        # optional: digests for the brief eval
```

It runs the shipping prompts for the Haiku 4.5 baseline and Haiku 5.5 at effort `low` and
`medium`, then prints parse failures, refusals, `max_tokens` stops, tokens and cost, grouping
precision/recall against the stored groups, category agreement against the baseline, and the
disagreements to read by hand. Pass bar: no failures of any kind, category agreement >= 90%,
grouping recall >= 85%, disagreements judged acceptable. The cost shown is the synchronous
price; the Batch API halves the categorising part. Run it on several days before deciding.

To check the downstream effect, add an `expected_major.json` (curate from the baseline fixture)
to a written fixture directory, add a `tests:` entry for it, and run the brief eval.

### Models

The promptfoo eval runs on the **5.5 family only** (`claude-opus-5-5`, `claude-sonnet-5-5`,
`claude-haiku-5-5`), graders included; `tests/test_eval_tools.py` fails if an older id appears in
`promptfooconfig.yaml` or the price table. The one exception is `compare_haiku.py`, which costs
the production Haiku 4.5 baseline (`baseline_prices` in `prices.json`) because it is the model
being replaced.

### Fixtures for this comparison

- `2026-06-15` - the original real day.
- `2026-06-15-lowrank` - the same day with the must-cover stories relabelled to deprioritised
  sources (`make_fixture.py lowrank`). It stresses the case that failed at medium effort. It is a
  perturbation of real data, not a new day: capture more real days (below) for broader coverage.

`python make_fixture.py text <fixture_dir>` regenerates `digest_text.txt` from
`digest_articles.json`.

## Dependencies

promptfoo lives in **this directory's own** `package.json` / `package-lock.json`, separate
from the repo-root `package.json`. This keeps promptfoo's large transitive tree (mongodb,
googleapis, …) out of the CDK/Lambda deployment build, whose `npm ci` must stay clean.
Always run the eval from `eval/` — the npm scripts assume that working directory, and the
config's paths (`provider.py`, `assertions/`, `fixtures/`, `../src/...`) are relative to it.

`package-lock.json` here needs **npm >= 11.5** (Node >= 24.5). Dependabot writes it with a
current npm, which no longer records optional peer dependencies — so older npm fails
`npm ci` with `Missing: gcp-metadata@7.0.1 from lock file`, because mongodb declares
`gcp-metadata` as an optional peer dep and older npm expects it in the lock.

## Run

```bash
uv sync --dev                    # repo-root .venv with anthropic/pydantic (the provider uses it)
npm --prefix eval ci             # installs promptfoo into eval/node_modules
export ANTHROPIC_API_KEY=sk-...
npm --prefix eval run eval       # runs from eval/ (the script sets PROMPTFOO_PYTHON=../.venv/bin/python)
npm --prefix eval run eval:view  # opens the promptfoo web UI for the last run
```

Or, equivalently, from inside `eval/`: `npm ci && ANTHROPIC_API_KEY=sk-... npm run eval`.

Each run makes a handful of Claude calls (one synthesis per fixture + the llm-rubric
grader), so it is not wired into the per-push CI gate. It runs weekly and on demand via
[`.github/workflows/eval.yml`](../.github/workflows/eval.yml).

In CI a failed run is retried once. Each attempt's results are uploaded separately
(`eval-results-1.json`, `eval-results-2.json` in the `brief-eval-results` artifact), and
`annotate_failures.jq` turns every failed assertion's reason into a run annotation, so
the cause is visible on the run page (and via the check-runs API) without opening the
logs. A run that passed only on the retry carries a "Brief eval retried" warning; treat
it as a flaky pass, not a clean one.

## Add a fixture

A fixture is one day's digest in the same shape `build_synthesis_input` produces:

```
eval/fixtures/<YYYY-MM-DD>/
  digest_articles.json   # { "AI/ML": [ {title, url, summary, source}, ... ], ... }
  expected_major.json    # { "must_cover": [ "<exact major-story title>", ... ] }
  digest_text.txt        # readable digest used as the faithfulness grader's source
```

No `id` field is needed in `digest_articles.json` — `provider.py` backfills one via
`ensure_article_ids` before running synthesis (the same numbering
`build_synthesis_input` uses in production: dict/list order, counting from 1). The
`major_coverage.cjs`/`source_ranking.cjs` assertions mirror that same numbering to
resolve the brief's `top_articles`/`top_stories` ids back to titles/sources.

The quickest way to capture one is from a live run:

```bash
# Dump the categorised synthesis input the brief actually received.
uv run python src/cli_brief_generator.py --dry-run --days 1 --debug
```

Then add a matching `tests:` entry in `promptfooconfig.yaml`.
