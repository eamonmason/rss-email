import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import { CodePipeline, CodePipelineSource, CodeBuildStep } from 'aws-cdk-lib/pipelines';
import { RSSPipelineAppStage } from './rss_pipeline_app_stage';
import { ManualApprovalStep } from 'aws-cdk-lib/pipelines';
import {BuildEnvironmentVariableType} from 'aws-cdk-lib/aws-codebuild';
import * as iam from 'aws-cdk-lib/aws-iam';
import { PipelineType } from 'aws-cdk-lib/aws-codepipeline';

export class RSSPipelineStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    const pipeline = new CodePipeline(this, 'Pipeline', {
      pipelineName: 'RSSPipeline',
      pipelineType: PipelineType.V2,
      synth: new CodeBuildStep('Synth', {
        input: CodePipelineSource.connection('eamonmason/rss-email', 'main', {
          connectionArn: 'arn:aws:codeconnections:eu-west-1:002681522526:connection/54e18fbd-fb99-4888-ac79-3a906b55f7ae',
        }),
        commands: ['npm ci', 'npx cdk synth'],
        buildEnvironment: {
          environmentVariables: {
            CDK_DOCKER: { value: 'false' },
            SOURCE_DOMAIN: { value: 'rss-email-SOURCE_DOMAIN', type: BuildEnvironmentVariableType.PARAMETER_STORE},
            SOURCE_EMAIL_ADDRESS: {value: 'rss-email-SOURCE_EMAIL_ADDRESS', type: BuildEnvironmentVariableType.PARAMETER_STORE},
            TO_EMAIL_ADDRESS: {value: 'rss-email-TO_EMAIL_ADDRESS', type: BuildEnvironmentVariableType.PARAMETER_STORE},
            EMAIL_RECIPIENTS: {value: 'rss-email-EMAIL_RECIPIENTS', type: BuildEnvironmentVariableType.PARAMETER_STORE},
            AWS_ACCOUNT_ID: {value: 'rss-email-AWS_ACCOUNT_ID', type: BuildEnvironmentVariableType.PARAMETER_STORE},
            AWS_REGION: {value: 'rss-email-AWS_REGION', type: BuildEnvironmentVariableType.PARAMETER_STORE},
            FEED_DEFINITIONS_FILE: {value: 'rss-email-FEED_DEFINITIONS_FILE', type: BuildEnvironmentVariableType.PARAMETER_STORE}
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
    
    // Grant the pipeline role permission to use the CodeConnections connection
    // This must be done after the pipeline is built
    pipeline.buildPipeline();
    pipeline.pipeline.role.addToPrincipalPolicy(new iam.PolicyStatement({
      actions: ['codeconnections:UseConnection'],
      resources: ['arn:aws:codeconnections:eu-west-1:002681522526:connection/54e18fbd-fb99-4888-ac79-3a906b55f7ae'],
      conditions: {
        StringEquals: {
          'codeconnections:FullRepositoryId': 'eamonmason/rss-email'
        }
      }
    }));
    
    //   testingStage.addPost(new ManualApprovalStep('approval'));
    //   testingStage.addPost(new ShellStep("validate", {
    //     commands: ['../tests/validate.sh'],
    //   }));
  }
}
