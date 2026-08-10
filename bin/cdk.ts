#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { RSSEmailStack } from '../lib/rss_lambda_stack.js';
import 'dotenv/config'

const app = new cdk.App();

// The stack name is pinned to 'cd-RSSEmailStack' for continuity: it was previously
// deployed through a CDK Pipelines Stage named 'cd', which prefixed the stack name.
// Renaming it would create a second, parallel copy of every resource.
new RSSEmailStack(app, 'RSSEmailStack', {
  stackName: 'cd-RSSEmailStack',
  env: {
    account: process.env.AWS_ACCOUNT_ID,
    region: process.env.AWS_REGION
  }
});
