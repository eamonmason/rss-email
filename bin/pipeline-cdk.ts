#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { RSSPipelineStack } from '../lib/rss_pipeline_stack';

const app = new cdk.App();
new RSSPipelineStack(app, 'RSSPipelineStack', {
  env: {
    account: process.env.AWS_ACCOUNT_ID,
    region: process.env.AWS_REGION
  }
});

app.synth();