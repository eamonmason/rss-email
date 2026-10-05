# AI-Native Workflow Guide

This repository uses an agentic workflow to take an issue through spec, code
and review. The engine is three reusable workflows in
[`eamonmason/ai-workflows`](https://github.com/eamonmason/ai-workflows); the
files here are thin callers pinned to a specific commit of it.

## The gate, stated once

**Only the repository owner can trigger any of this**, and the trigger is a
comment containing `@agent execute`. Every stage re-checks
`author_association` — both here and inside the reusable workflow, because the
engine is callable by other repositories and a gate that lives in only one
caller is not a gate.

The `agent:*` labels below are *advisory state*. They are not the gate: the
agent can write them, so they describe what happened, not what is permitted.

## 1. Spec

Open a **GitHub Issue**. The `Agent Planner` writes an implementation spec to
`docs/specs/<issue>-<slug>.md` on a branch `agent/<issue>-<slug>`, opens a
**spec-only pull request**, and labels the issue `agent:spec`.

Review the spec *before any code exists*. It carries EARS acceptance criteria
(`AC1`, `AC2`, …), an explicit blast-radius section, and implementation steps
that each end in a runnable `verify:` line.

To change it, comment on the issue and the planner revises the spec in place.

## 2. Build

Comment `@agent execute` on the issue. The `Agent Executor`:

1. finds the spec by **issue number** (so editing the title cannot orphan it),
   and refuses to start if there is not exactly one spec branch and one spec
   file — no guessing;
2. implements it, then runs `./scripts/verify.sh`;
3. if that fails, gives itself up to three repair attempts;
4. if it still fails, **opens no PR** — it comments the failure output on the
   issue and labels it `agent:blocked`;
5. if it passes, pushes onto the *same* branch as the spec and labels the issue
   `agent:review`.

So one pull request grows from spec to spec-plus-code, and both halves are
visible in a single diff.

`./scripts/verify.sh` mirrors the `build` job in `lint_and_test.yml` exactly.
It is a **pre-filter, not the merge gate** — the required status checks
`build (3.13)` and `test-cdk-synth` remain authoritative.

## 3. Review

Comment `@agent` on the pull request, either on a diff line or in the
conversation. The `Agent Reviewer` addresses the feedback, pushes a commit, and
posts a table with one row per acceptance criterion — `met`, `not met`, or
`not covered by tests` — followed by a section listing anything in the diff
that **no acceptance criterion authorised**.

That last section is the point. Scope creep is the failure an autonomous
executor actually has, and it is invisible in a normal review because every
individual hunk looks reasonable.

## Merging

**Squash-merge.** `main` requires signed commits, and the agent's commits are
made by `github-actions[bot]` and are unsigned. Squash and rebase are safe —
GitHub creates and signs a new commit — but a **merge commit carries the
unsigned commits onto `main` and is rejected**. That presents as "merge
blocked" with no failing check.

## Dependencies

Dependabot PRs are handled separately by `.github/workflows/dependabot.yml`,
which delegates to a reusable workflow in `eamonmason/.github`: it skips
`semver-major`, waits for required checks, and squash-merges the rest. Because
it merges with a GitHub App token rather than `GITHUB_TOKEN`, those merges do
trigger `deploy.yml`. A dependency bump can therefore reach production without
a human. That is deliberate.

The `ai-workflows` pin is excluded from Dependabot in `.github/dependabot.yml`,
so the agent engine is never bumped automatically.

## Labels

| Label | Meaning | Set by |
|---|---|---|
| `agent:spec` | spec written, awaiting approval | planner |
| `agent:build` | approved, executor working | executor |
| `agent:review` | verified and pushed, awaiting a human | executor |
| `agent:blocked` | verification failed, or no single approved spec | executor |
| `tier-a` / `tier-b` | blast radius, per the spec | planner |

## Requirements

`ANTHROPIC_API_KEY`, `APP_ID` and `APP_PRIVATE_KEY` must be configured as
Actions secrets. The App token is not cosmetic: a pull request opened with
`GITHUB_TOKEN` does not trigger `pull_request` workflows, so required checks
would never report and the PR could never be merged.
