import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import { CodePipeline, CodePipelineSource, ShellStep } from 'aws-cdk-lib/pipelines';
import { RSSPipelineAppStage } from './rss_pipeline_app_stage';
import { ManualApprovalStep } from 'aws-cdk-lib/pipelines';

export class RSSPipelineStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    const pipeline = new CodePipeline(this, 'Pipeline', {
      pipelineName: 'RSSPipeline',
      synth: new ShellStep('Synth', {
        input: CodePipelineSource.gitHub('eamonmason/rss-email', 'main'),
        commands: ['npm ci', 'npm run build', 'npx cdk synth']
      })
    });

    const testingStage = pipeline.addStage(new RSSPipelineAppStage(this, "test", {
        env: {
            account: process.env.AWS_ACCOUNT_ID,
            region: process.env.AWS_REGION
        }
      }));
    
    //   testingStage.addPost(new ManualApprovalStep('approval'));
    //   testingStage.addPost(new ShellStep("validate", {
    //     commands: ['../tests/validate.sh'],
    //   }));
  }
}