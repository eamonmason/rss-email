#!/usr/bin/env bash
# Single source of truth for "is this change good?".
#
# Mirrors the `build` job in .github/workflows/lint_and_test.yml exactly. The
# agent executor runs this before it is allowed to open a PR, so it must match
# what CI will say -- a gate that disagrees with CI either blocks good changes
# or waves through bad ones.
#
# Deliberately omits flake8: CI does not run it. The pre-commit config does,
# with --max-line-length=120, and CLAUDE.md documents a third variant again.
# CI is the authority here, because CI is what gates the merge.
#
# Requires `pip` on PATH, not just uv. The Lambda layer's bundling has a
# `local.tryBundle` path that shells out to `pip install -t ...` when
# CDK_DOCKER=false, and silently falls back to Docker if that throws -- which
# then runs `false` and fails the synth with a confusing bundling error. CI and
# the agent executor both get pip from actions/setup-python.
#
# CI additionally runs `npm install -g aws-cdk@^2.1018.1` before synth. That is
# a no-op in practice: aws-cdk is a direct dependency in package.json and npx
# prefers node_modules/.bin over a global install. Omitted so this script
# matches CI's behaviour rather than its text.
set -euo pipefail
cd "$(dirname "$0")/.."

uv sync --dev
npm install
uv run pylint --fail-under=9.9 src tests
CDK_DOCKER=false npx cdk synth --no-staging > /dev/null
uv run python -m pytest tests
