#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { RSSEmailStack } from '../lib/rss_lambda_stack';
import 'dotenv/config'

const app = new cdk.App();
new RSSEmailStack(app, 'RSSEmailStack');