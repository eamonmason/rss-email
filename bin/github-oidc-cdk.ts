#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { GitHubActionsRoleStack } from '../lib/github_actions_role_stack.js';

// Separate app entry point from bin/cdk.ts on purpose: this stack is one-time
// bootstrap infrastructure and must never be swept into the CI deploy path.
//
//   npx cdk deploy --app "npx tsx bin/github-oidc-cdk.ts"

const app = new cdk.App();

new GitHubActionsRoleStack(app, 'RSSEmailGitHubActionsRoleStack', {
  env: {
    account: process.env.AWS_ACCOUNT_ID || process.env.CDK_DEFAULT_ACCOUNT,
    region: process.env.AWS_REGION || process.env.CDK_DEFAULT_REGION
  }
});
