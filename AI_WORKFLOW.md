# AI-Native Workflow Guide

This repository utilizes an **AI-Native Agentic Workflow** to automate tasks from ideation to deployment. The agents (powered by Claude Code and Gemini) assist in planning, executing, and reviewing code.

## How It Works

### 1. Planning phase
- Open a **GitHub Issue** detailing the feature or bug you want to fix.
- The `Agent Planner` workflow will automatically run, analyze the request, and comment on the issue with a proposed implementation plan.

### 2. Steering & Execution phase (Human-in-the-Loop)
- Review the agent's proposed plan on the issue.
- If you want the agent to proceed, comment on the issue with:
  > `@agent execute`
- The `Agent Executor` workflow will then check out a branch, write the code, run tests, and open a Pull Request.

### 3. Review phase
- For the open Pull Request, you can provide feedback on specific lines of code.
- If you need the agent to fix something, comment on the PR using `@agent` and your instructions.
- The `Agent Reviewer` workflow will automatically wake up, implement the requested changes, and push new commits to the PR.

### 4. Dependabot / Triage
- If Dependabot opens a PR, the `Agent Auto-Triage` workflow will automatically assess the risk.
- Low-complexity updates (where CI passes and the risk is low) are automatically merged without human intervention.
- High-complexity or high-risk updates will require your standard manual review.

## Requirements
- `ANTHROPIC_API_KEY` and/or `GEMINI_API_KEY` must be configured as GitHub Actions Secrets in this repository.
