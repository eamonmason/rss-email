import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import { CodePipeline, CodePipelineSource, CodeBuildStep } from 'aws-cdk-lib/pipelines';
import { RSSPipelineAppStage } from './rss_pipeline_app_stage';
import { ManualApprovalStep } from 'aws-cdk-lib/pipelines';
import {BuildEnvironmentVariableType} from 'aws-cdk-lib/aws-codebuild';

export class RSSPipelineStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    const pipeline = new CodePipeline(this, 'Pipeline', {
      pipelineName: 'RSSPipeline',
      synth: new CodeBuildStep('Synth', {
        input: CodePipelineSource.gitHub('eamonmason/rss-email', 'main'),
        commands: ['npm ci', 'npx cdk synth'],
        buildEnvironment: {
          environmentVariables: {
            SOURCE_DOMAIN: { value: 'rss-email-SOURCE_DOMAIN', type: BuildEnvironmentVariableType.PARAMETER_STORE},
            SOURCE_EMAIL_ADDRESS: {value: 'rss-email-SOURCE_EMAIL_ADDRESS', type: BuildEnvironmentVariableType.PARAMETER_STORE},
            TO_EMAIL_ADDRESS: {value: 'rss-email-TO_EMAIL_ADDRESS', type: BuildEnvironmentVariableType.PARAMETER_STORE},
            EMAIL_RECIPIENTS: {value: 'rss-email-EMAIL_RECIPIENTS', type: BuildEnvironmentVariableType.PARAMETER_STORE},
            AWS_ACCOUNT_ID: {value: 'rss-email-AWS_ACCOUNT_ID', type: BuildEnvironmentVariableType.PARAMETER_STORE},
            AWS_REGION: {value: 'rss-email-AWS_REGION', type: BuildEnvironmentVariableType.PARAMETER_STORE},
          }
        }
      })        
    });

    const testingStage = pipeline.addStage(new RSSPipelineAppStage(this, "cd", {
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