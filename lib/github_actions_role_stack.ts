import * as cdk from 'aws-cdk-lib';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';

// The GitHub OIDC provider is account-wide and is shared with other projects in this
// account, so it is imported rather than created - creating a second one fails.
const GITHUB_OIDC_PROVIDER_ARN_SUFFIX = 'oidc-provider/token.actions.githubusercontent.com';

const GITHUB_REPO = 'eamonmason/rss-email';
const DEPLOY_BRANCH = 'main';
const CDK_BOOTSTRAP_QUALIFIER = 'hnb659fds';

// Config the deploy workflow reads out of Parameter Store at deploy time. Parameter
// Store remains the source of truth for these values; GitHub holds no copies.
const CONFIG_PARAMETERS = [
  'rss-email-AWS_ACCOUNT_ID',
  'rss-email-AWS_REGION',
  'rss-email-EMAIL_RECIPIENTS',
  'rss-email-SOURCE_DOMAIN',
  'rss-email-SOURCE_EMAIL_ADDRESS',
  'rss-email-TO_EMAIL_ADDRESS',
];

/**
 * IAM role assumed by the GitHub Actions deploy workflow via OIDC.
 *
 * Deployed once, by hand, and then left alone:
 *
 *   npx cdk deploy --app "npx tsx bin/github-oidc-cdk.ts"
 *
 * The role itself is deliberately near-powerless: all it can do is assume the CDK
 * bootstrap roles (which hold the actual deployment permissions) and read the
 * rss-email config parameters.
 */
export class GitHubActionsRoleStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    const provider = iam.OpenIdConnectProvider.fromOpenIdConnectProviderArn(
      this,
      'GitHubOidcProvider',
      `arn:aws:iam::${this.account}:${GITHUB_OIDC_PROVIDER_ARN_SUFFIX}`
    );

    const role = new iam.Role(this, 'GitHubActionsDeployRole', {
      roleName: 'rss-email-github-actions-deploy',
      description: `Deploys cd-RSSEmailStack from GitHub Actions (${GITHUB_REPO}@${DEPLOY_BRANCH})`,
      maxSessionDuration: cdk.Duration.hours(1),
      assumedBy: new iam.OpenIdConnectPrincipal(provider, {
        StringEquals: {
          'token.actions.githubusercontent.com:aud': 'sts.amazonaws.com',
        },
        // Scoped to pushes to the deploy branch only - a workflow running from a
        // fork or a feature branch cannot assume this role.
        StringLike: {
          'token.actions.githubusercontent.com:sub': `repo:${GITHUB_REPO}:ref:refs/heads/${DEPLOY_BRANCH}`,
        },
      }),
    });

    const bootstrapRoles = ['deploy', 'file-publishing', 'image-publishing', 'lookup'].map(
      (name) =>
        `arn:aws:iam::${this.account}:role/cdk-${CDK_BOOTSTRAP_QUALIFIER}-${name}-role-${this.account}-${this.region}`
    );

    role.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['sts:AssumeRole'],
      resources: bootstrapRoles,
    }));

    role.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['ssm:GetParameters'],
      resources: CONFIG_PARAMETERS.map(
        (name) => `arn:aws:ssm:${this.region}:${this.account}:parameter/${name}`
      ),
    }));

    // The CDK CLI reads the bootstrap version before deciding how to deploy.
    role.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['ssm:GetParameter'],
      resources: [
        `arn:aws:ssm:${this.region}:${this.account}:parameter/cdk-bootstrap/${CDK_BOOTSTRAP_QUALIFIER}/version`,
      ],
    }));

    new cdk.CfnOutput(this, 'DeployRoleArn', {
      value: role.roleArn,
      description: 'Set this as the AWS_DEPLOY_ROLE_ARN repository variable in GitHub',
    });
  }
}
