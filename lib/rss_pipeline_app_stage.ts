import * as cdk from 'aws-cdk-lib';
import { Construct } from "constructs";
import { RSSEmailStack } from './rss_lambda_stack.js';

export class RSSPipelineAppStage extends cdk.Stage {

    constructor(scope: Construct, id: string, props?: cdk.StageProps) {
      super(scope, id, props);

      const rssEmailStack = new RSSEmailStack(this, 'RSSEmailStack');
    }
}
