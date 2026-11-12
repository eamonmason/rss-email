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

### Building real-day fixtures for the brief comparison

The brief is the expensive call, so the model/effort decision needs more than one real day. From
real `batch-metadata` files (see below for fetching them):

```bash
mkdir -p /tmp/batches/reports
for f in /tmp/batches/batch-2*[0-9].json; do   # ends in a digit: skips *.report.json
  uv run python eval/compare_haiku.py "$f" --variants claude-haiku-4-5 --no-adjudicate \
      --write-fixtures eval/fixtures        # categorised with the production Haiku 4.5
done
python eval/make_fixture.py candidates /tmp/batches/batch-<timestamp>.json   # stories many feeds ran
# fixtures written before 2026-11-12 also hold categories the brief never sees (Other, Science...):
uv run python eval/make_fixture.py refilter eval/fixtures/2026-11-*-haiku-4-5
# write eval/fixtures/<day>-haiku-4-5/expected_major.json by hand: {"must_cover": ["<exact title>", ...]}
(cd eval && python make_fixture.py sync-tests)
```

`--write-fixtures` now keeps only what production sends the brief (themed and personal categories,
sponsored dropped), and `refilter` brings older fixtures into line. A story must sit in one of those
categories to be coverable. `major_coverage` counts a story listed in "Read these" as surfaced.

`candidates` only proposes (distinct feeds covering a story); you choose what counts as major.
Fixtures built this way contain article titles and summaries from your feeds, like the existing one.

### Haiku 4.5 -> 5.5 (classification prompt v2)

The original categorisation prompt (**v1**) gave the model a bare category list "in priority order;
prefer tech-related when applicable". Haiku 5.5 follows that literally (anything mentioning AI becomes
AI/ML), and Haiku 4.5 made clear errors of its own (Nvidia stories filed under Media/TV/Film, which
the brief never sees). **v2** (`article_processor.CATEGORY_DEFINITIONS`) defines every category and
how to choose between them, built around what the brief needs. Production stays on v1
(`DEFAULT_CATEGORY_PROMPT_VERSION`, override `CATEGORY_PROMPT_VERSION`) until v2 passes the gates
below; the switch is then that one constant.

`compare_haiku.py` variants are `model[:effort][@prompt][#tag]`. The defaults are `4.5@v1` (what
production runs now), `4.5@v1#2` (its own noise floor), `4.5@v2` (is a gain the prompt or the model?),
`5.5:low@v2` and `5.5:medium@v2`. Each prompt-tuning round, on three days:

```bash
mkdir -p /tmp/batches/reports
for f in /tmp/batches/batch-2026111[01]-*[0-9].json /tmp/batches/batch-20261109-*[0-9].json; do
  uv run python eval/compare_haiku.py "$f" --skip-grouping \
      --out "/tmp/batches/reports/$(basename "${f%.json}").json" \
      | tee "/tmp/batches/reports/$(basename "${f%.json}").txt"
done
```

`--skip-grouping` reuses the stored groups, so the round measures the prompt and model on identical
groups (about $2 a round by estimate). Reports use a blind judge (Sonnet 5.5, options shuffled, no
model names) that is shown the same category definitions, so it grades against the intended policy; it
prints which side won on each disagreement, so patterns can be read, not only counted. Because the
judge uses v2's definitions, also read a sample of the disagreements yourself.

**Classification gate:** no parse failures, refusals, truncations or missing groups; pooled judge win
rate of `5.5@v2` against `4.5@v1` of at least 50% with no day under 45%; brief-visible agreement
within 5 points of 4.5's own noise floor. Then regenerate one fixture per day from `5.5@v2` with
`--write-fixtures` and run the baseline brief eval on it, so any change in what reaches the brief
shows in `major_coverage`.

**Grouping** is judged separately (omit `--skip-grouping`, one or two days): for pairs one grouping
made and the other did not, the judge answers "same specific news event?", and the report gives each
model's pair precision, recall against judge-confirmed pairs and F1. Gate: 5.5's F1 within 0.05 of
4.5's or better. Recall against the stored groups is shown but not used when pairs were judged, because
the stored groups are Haiku 4.5's own output.

Before switching, confirm the Batch API accepts the Haiku 5.5 settings (`output_config.effort`):

```bash
uv run python eval/check_batch.py      # creates a one-request batch, then cancels it
```

If the API ever rejects it in production, `submit_email_batch` retries once without the setting
instead of failing the day.

### Models

The promptfoo eval runs on the **5.5 family only** (`claude-opus-5-5`, `claude-sonnet-5-5`,
`claude-haiku-5-5`), graders included; `tests/test_eval_tools.py` fails if an older id appears in
`promptfooconfig.yaml` or the price table. The one exception is `compare_haiku.py`, which costs
the production Haiku 4.5 baseline (`baseline_prices` in `prices.json`) because it is the model
being replaced.

**CI runs the production setting only.** The monthly `eval.yml` job filters to `opus-5-5/high` with
one repeat (about $0.25 per fixture: a measured ~$0.19 brief plus grading), so it stays cheap as
fixtures are added. To run the whole matrix from CI, start the workflow manually and tick `matrix`.

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
grader), so it is not wired into the per-push CI gate. It runs monthly (the 1st) and on demand via
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

Then run `python make_fixture.py sync-tests` (from `eval/`), which regenerates the `tests:` block
in `promptfooconfig.yaml` from the `fixtures/*` directories. A fixture without an
`expected_major.json` is still graded (schema, source ranking, faithfulness, relevance); only the
`major_coverage` check is skipped until it is curated.
