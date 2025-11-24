import * as cdk from 'aws-cdk-lib';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as events from 'aws-cdk-lib/aws-events';
import { SnsEventSource } from 'aws-cdk-lib/aws-lambda-event-sources';
import * as targets from 'aws-cdk-lib/aws-events-targets';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as sns from 'aws-cdk-lib/aws-sns';
import * as ses from 'aws-cdk-lib/aws-ses';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as destinations from 'aws-cdk-lib/aws-logs-destinations';
import * as actions from 'aws-cdk-lib/aws-ses-actions';
import * as cloudwatch_actions from 'aws-cdk-lib/aws-cloudwatch-actions';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as ssm from 'aws-cdk-lib/aws-ssm';
import { Construct } from 'constructs';
import * as fs from 'fs';
import * as path from 'path';
import { execSync } from 'child_process';


const BUCKET_NAME = 'rss-bucket';
const KEY = 'rss.xml';
const SNS_RECEIVE_EMAIL = 'rss-receive-email';
const SNS_ERROR_ALERTS = 'rss-error-alerts';
const RSS_RULE_SET_NAME = 'RSSRuleSet';
const LAST_RUN_PARAMETER = 'rss-email-lastrun';

const PODCAST_LAST_RUN_PARAMETER = 'rss-podcast-lastrun';
const PODCAST_CLOUDFRONT_DOMAIN_PARAMETER = 'rss-podcast-cloudfront-domain';
const ANTHROPIC_API_KEY_PARAMETER = 'rss-email-anthropic-api-key';

export class RSSEmailStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);
    const SOURCE_DOMAIN = process.env.SOURCE_DOMAIN || "";
    const SOURCE_EMAIL_ADDRESS = process.env.SOURCE_EMAIL_ADDRESS || "";
    const TO_EMAIL_ADDRESS = process.env.TO_EMAIL_ADDRESS || "";
    const EMAIL_RECIPIENTS = process.env.EMAIL_RECIPIENTS?.split(',') || [];
    const FEED_DEFINITIONS_FILE = process.env.FEED_DEFINITIONS_FILE || "";

    const bucket = new s3.Bucket(this, BUCKET_NAME, {
      versioned: false,
      lifecycleRules: [
        {
          id: 'DeleteOldPodcastEpisodes',
          enabled: true,
          prefix: 'podcasts/episodes/',
          expiration: cdk.Duration.days(14),
        }
      ]
    });

    const receive_topic = new sns.Topic(this, SNS_RECEIVE_EMAIL);

    // Create an SNS topic for error alerts
    const error_alerts_topic = new sns.Topic(this, SNS_ERROR_ALERTS);
    const errorSubscription = new sns.Subscription(this, 'ErrorEmailSubscription', {
      topic: error_alerts_topic,
      endpoint: TO_EMAIL_ADDRESS,
      protocol: sns.SubscriptionProtocol.EMAIL
    });

    const MyTopicPolicy = new sns.TopicPolicy(this, 'RSSTopicSNSPolicy', {
      topics: [receive_topic],
    });

    MyTopicPolicy.document.addStatements(new iam.PolicyStatement({
      sid: "0",
      actions: ["SNS:Publish"],
      principals: [new iam.ServicePrincipal('ses.amazonaws.com')],
      resources: [receive_topic.topicArn],
      conditions:
      {
        "StringEquals": {
          "AWS:SourceAccount": process.env.CDK_DEFAULT_ACCOUNT,
        },
        "StringLike": {
          "AWS:SourceArn": "arn:aws:ses:*"
        }
      }
    }
    ));

    const receipt_rule_set = new ses.ReceiptRuleSet(this, RSS_RULE_SET_NAME, {
      rules: [
        {
          enabled: true,
          scanEnabled: true,
          tlsPolicy: ses.TlsPolicy.OPTIONAL,
          recipients: EMAIL_RECIPIENTS,
          actions: [
            new actions.Sns({
              topic: receive_topic,
              encoding: actions.EmailEncoding.UTF8
            })
          ]
        }
      ]
    }
    )

    const role = new iam.Role(this, 'RSSLambdaRole', {
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicExecutionRole'),
      ],
      inlinePolicies: {
        'policy': new iam.PolicyDocument({
          statements: [
            new iam.PolicyStatement({
              effect: iam.Effect.ALLOW,
              actions: ['s3:PutObject', 's3:GetObject', 's3:ListBucket', 's3:ListObjects'],
              resources: [bucket.bucketArn + '/*'],
            }),
            new iam.PolicyStatement({
              effect: iam.Effect.ALLOW,
              actions: ['ssm:PutParameter', 'ssm:GetParameter'],
              resources: [`arn:aws:ssm:*:*:parameter/${LAST_RUN_PARAMETER}`],
            }),
            new iam.PolicyStatement({
              effect: iam.Effect.ALLOW,
              actions: ['ssm:GetParameter'],
              resources: [`arn:aws:ssm:*:*:parameter/${ANTHROPIC_API_KEY_PARAMETER}`],
            }),
            new iam.PolicyStatement({
              effect: iam.Effect.ALLOW,
              actions: ['ses:SendEmail'],
              resources: [
                `arn:aws:ses:*:*:identity/${TO_EMAIL_ADDRESS}`,
                `arn:aws:ses:*:*:identity/${SOURCE_EMAIL_ADDRESS}`,
                `arn:aws:ses:*:*:identity/${SOURCE_DOMAIN}`],
            }),
          ]
        })
      }
    });

    const layer = new lambda.LayerVersion(this, 'RSSLibsLayer', {
      code: lambda.Code.fromAsset('.', {
        exclude: ['*.pyc'],
        bundling: {
          image: lambda.Runtime.PYTHON_3_13.bundlingImage,
          command: [
            'bash', '-c',
            'mkdir -p /asset-output/python/lib/python3.13/site-packages/ && pip install -t /asset-output/python/lib/python3.13/site-packages/ . && rm -r /asset-output/python/lib/python3.13/site-packages/rss_email*'
          ],
          local: {
            tryBundle(outputDir: string) {
              // For environments where Docker is not available (e.g., GitHub Actions with CDK_DOCKER=false)
              if (process.env.CDK_DOCKER === 'false') {
                console.log('Docker not available, using local bundling for Lambda Layer');
                const pythonDir = path.join(outputDir, 'python', 'lib', 'python3.13', 'site-packages');
                fs.mkdirSync(pythonDir, { recursive: true });

                // Read dependencies from pyproject.toml and create requirements.txt
                const pyprojectPath = path.join(process.cwd(), 'pyproject.toml');
                const pyprojectContent = fs.readFileSync(pyprojectPath, 'utf-8');

                // Extract dependencies from pyproject.toml (Standard [project] format)
                const dependencies: string[] = [];
                const lines = pyprojectContent.split('\n');
                let inDependencies = false;

                for (const line of lines) {
                  const trimmed = line.trim();
                  if (trimmed.startsWith('dependencies = [')) {
                    inDependencies = true;
                    continue;
                  }
                  if (inDependencies && trimmed === ']') {
                    inDependencies = false;
                    break;
                  }
                  if (inDependencies) {
                    // Parse "package>=version",
                    const cleanLine = trimmed.replace(/[",]/g, '');
                    if (cleanLine) dependencies.push(cleanLine);
                  }
                }

                // Create requirements.txt file
                const requirementsPath = path.join(outputDir, 'requirements.txt');
                fs.writeFileSync(requirementsPath, dependencies.join('\n'));

                // Use pip to install dependencies
                try {
                  execSync(`pip install -r ${requirementsPath} -t ${pythonDir}`, {
                    stdio: 'inherit',
                    cwd: outputDir
                  });
                  // Clean up
                  fs.unlinkSync(requirementsPath);
                  return true;
                } catch (error) {
                  console.error('Failed to install dependencies locally:', error);
                  return false;
                }
              }
              return false;
            }
          }
        }
      }
      )
    })

    const RSSGenerationFunction = new lambda.Function(this, 'RSSGenerationFunction', {
      code: lambda.Code.fromAsset('src'),
      handler: 'rss_email.retrieve_articles.create_rss',
      runtime: lambda.Runtime.PYTHON_3_13,
      environment: {
        BUCKET: bucket.bucketName,
        KEY: KEY,
        FEED_DEFINITIONS_FILE: FEED_DEFINITIONS_FILE
      },
      role: role,
      layers: [layer],
      timeout: cdk.Duration.seconds(130)
    });

    const generationEventRule = new events.Rule(this, 'generationEventRule', {
      schedule: events.Schedule.cron({ minute: '0', hour: '*/3' }),
    });
    generationEventRule.addTarget(new targets.LambdaFunction(RSSGenerationFunction))

    const RSSEMailerFunction = new lambda.Function(this, 'RSSEmailerFunction', {
      code: lambda.Code.fromAsset('src'), // directory of your lambda function code
      handler: 'rss_email.email_articles.send_email', // filename.methodname
      runtime: lambda.Runtime.PYTHON_3_13,
      environment: {
        BUCKET: bucket.bucketName,
        KEY: KEY,
        SOURCE_EMAIL_ADDRESS: SOURCE_EMAIL_ADDRESS,
        TO_EMAIL_ADDRESS: TO_EMAIL_ADDRESS,
        LAST_RUN_PARAMETER: LAST_RUN_PARAMETER,
        ANTHROPIC_API_KEY_PARAMETER: ANTHROPIC_API_KEY_PARAMETER,
        CLAUDE_MODEL: 'claude-3-5-haiku-20241022',
        CLAUDE_MAX_TOKENS: '100000',
        CLAUDE_MAX_REQUESTS: '5',
        CLAUDE_ENABLED: 'true',
        CLAUDE_API_TIMEOUT: '120',  // 2 minutes (120 seconds) timeout for Anthropic API calls
      },
      role: role,
      layers: [layer],
      timeout: cdk.Duration.seconds(300)  // Increased from 30s to accommodate Claude API calls
    });

    const emailerEventRule = new events.Rule(this, 'emailerEventRule', {
      schedule: events.Schedule.cron({ minute: '30', hour: '7', weekDay: '2-6' }),
    });
    emailerEventRule.addTarget(new targets.LambdaFunction(RSSEMailerFunction))
    RSSEMailerFunction.addEventSource(new SnsEventSource(receive_topic));

    const RSSPodcastFunction = new lambda.Function(this, 'RSSPodcastFunction', {
      code: lambda.Code.fromAsset('src'),
      handler: 'rss_email.podcast_generator.generate_podcast',
      runtime: lambda.Runtime.PYTHON_3_13,
      environment: {
        BUCKET: bucket.bucketName,
        KEY: KEY,
        PODCAST_LAST_RUN_PARAMETER: PODCAST_LAST_RUN_PARAMETER,
        PODCAST_CLOUDFRONT_DOMAIN_PARAMETER: PODCAST_CLOUDFRONT_DOMAIN_PARAMETER,
        ANTHROPIC_API_KEY_PARAMETER: ANTHROPIC_API_KEY_PARAMETER,
        CLAUDE_MODEL: 'claude-3-5-haiku-20241022',
        CLAUDE_MAX_TOKENS: '4000',
      },
      role: role,
      layers: [layer],
      timeout: cdk.Duration.minutes(10) // Longer timeout for TTS and generation
    });

    // Schedule podcast generation to run after email is sent (8:00 AM weekdays)
    const podcastEventRule = new events.Rule(this, 'podcastEventRule', {
      schedule: events.Schedule.cron({ minute: '0', hour: '8', weekDay: '2-6' }),
    });
    podcastEventRule.addTarget(new targets.LambdaFunction(RSSPodcastFunction));

    // Add Polly permissions to the role
    role.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['polly:SynthesizeSpeech'],
      resources: ['*']
    }));

    // Add SSM permission for podcast last run
    role.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['ssm:PutParameter', 'ssm:GetParameter'],
      resources: [`arn:aws:ssm:*:*:parameter/${PODCAST_LAST_RUN_PARAMETER}`],
    }));

    // Add SSM permission for CloudFront domain parameter
    role.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['ssm:GetParameter'],
      resources: [`arn:aws:ssm:*:*:parameter/${PODCAST_CLOUDFRONT_DOMAIN_PARAMETER}`],
    }));

    const rssGenerationLogGroup = new logs.LogGroup(this, 'rssGenerationLogGroup', {
      logGroupName: `/aws/lambda/${RSSGenerationFunction.functionName}`,
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: cdk.RemovalPolicy.DESTROY
    });

    const rssEmailLogGroup = new logs.LogGroup(this, 'rssEmailLogGroup', {
      logGroupName: `/aws/lambda/${RSSEMailerFunction.functionName}`,
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: cdk.RemovalPolicy.DESTROY
    });

    // Add CloudWatch metric filter to extract ERROR and WARNING logs
    const emailerErrorMetricFilter = new logs.MetricFilter(this, 'EmailErrorMetricFilter', {
      logGroup: rssEmailLogGroup,
      filterPattern: logs.FilterPattern.anyTerm('ERROR', 'WARNING', 'Error', 'Warning', 'error', 'warning'),
      metricNamespace: 'RSS/EmailLambda',
      metricName: 'ErrorWarningCount',
      defaultValue: 0,
      metricValue: '1',
    });

    // Create an alarm based on the metric that triggers when there's at least one ERROR or WARNING message
    const errorAlarm = new cdk.aws_cloudwatch.Alarm(this, 'EmailLambdaErrorAlarm', {
      metric: emailerErrorMetricFilter.metric(),
      threshold: 1,
      evaluationPeriods: 1,
      comparisonOperator: cdk.aws_cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
      treatMissingData: cdk.aws_cloudwatch.TreatMissingData.NOT_BREACHING,
      alarmDescription: 'Alarm for ERROR or WARNING log messages in RSS Email Lambda',
      actionsEnabled: true,
    });

    // Add the SNS topic as an action for the alarm
    errorAlarm.addAlarmAction(new cloudwatch_actions.SnsAction(error_alerts_topic));

    // Apply the same for the RSS generation function
    const rssGenerationErrorMetricFilter = new logs.MetricFilter(this, 'GenerationErrorMetricFilter', {
      logGroup: rssGenerationLogGroup,
      filterPattern: logs.FilterPattern.anyTerm('ERROR', 'WARNING', 'Error', 'Warning', 'error', 'warning'),
      metricNamespace: 'RSS/GenerationLambda',
      metricName: 'ErrorWarningCount',
      defaultValue: 0,
      metricValue: '1',
    });

    const generationErrorAlarm = new cdk.aws_cloudwatch.Alarm(this, 'GenerationLambdaErrorAlarm', {
      metric: rssGenerationErrorMetricFilter.metric(),
      threshold: 1,
      evaluationPeriods: 1,
      comparisonOperator: cdk.aws_cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
      treatMissingData: cdk.aws_cloudwatch.TreatMissingData.NOT_BREACHING,
      alarmDescription: 'Alarm for ERROR or WARNING log messages in RSS Generation Lambda',
      actionsEnabled: true,
    });

    generationErrorAlarm.addAlarmAction(new cloudwatch_actions.SnsAction(error_alerts_topic));

    // Create a Lambda function that will forward log events to the SNS topic
    const logForwarderFunction = new lambda.Function(this, 'LogForwarderFunction', {
      runtime: lambda.Runtime.NODEJS_22_X,
      handler: 'rss_email/log_forwarder.handler',
      code: lambda.Code.fromAsset('src'),
      environment: {
        SNS_TOPIC_ARN: error_alerts_topic.topicArn
      },
      timeout: cdk.Duration.minutes(5), // Increased timeout to handle aggregation
      memorySize: 256 // Increased memory for log aggregation
    });

    // Grant the Lambda function necessary permissions
    error_alerts_topic.grantPublish(logForwarderFunction);

    // Add CloudWatch Logs permissions
    logForwarderFunction.addToRolePolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: [
        'logs:CreateLogGroup',
        'logs:CreateLogStream',
        'logs:PutLogEvents',
        'logs:DescribeLogStreams'
      ],
      resources: ['*']
    }));

    // Create log subscription filters to send the actual log messages to the SNS topic via Lambda
    // This ensures the actual log message content is included in the notification
    const emailerLogSubscription = new logs.SubscriptionFilter(this, 'EmailerLogSubscription', {
      logGroup: rssEmailLogGroup,
      destination: new destinations.LambdaDestination(logForwarderFunction),
      filterPattern: logs.FilterPattern.anyTerm('ERROR', 'WARNING', 'Error', 'Warning', 'error', 'warning'),
    });

    const generationLogSubscription = new logs.SubscriptionFilter(this, 'GenerationLogSubscription', {
      logGroup: rssGenerationLogGroup,
      destination: new destinations.LambdaDestination(logForwarderFunction),
      filterPattern: logs.FilterPattern.anyTerm('ERROR', 'WARNING', 'Error', 'Warning', 'error', 'warning'),
    });

    const podcastLogGroup = new logs.LogGroup(this, 'podcastLogGroup', {
      logGroupName: `/aws/lambda/${RSSPodcastFunction.functionName}`,
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: cdk.RemovalPolicy.DESTROY
    });

    const podcastLogSubscription = new logs.SubscriptionFilter(this, 'PodcastLogSubscription', {
      logGroup: podcastLogGroup,
      destination: new destinations.LambdaDestination(logForwarderFunction),
      filterPattern: logs.FilterPattern.anyTerm('ERROR', 'WARNING', 'Error', 'Warning', 'error', 'warning'),
    });

    // Create CloudFront Origin Access Control for podcast bucket access
    const podcastOAC = new cloudfront.S3OriginAccessControl(this, 'PodcastOAC', {
      signing: cloudfront.Signing.SIGV4_ALWAYS,
    });

    // Create CloudFront distribution for public podcast access
    const podcastDistribution = new cloudfront.Distribution(this, 'PodcastDistribution', {
      defaultBehavior: {
        origin: new origins.S3Origin(bucket, {
          originAccessControl: podcastOAC,
        }),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED,
        allowedMethods: cloudfront.AllowedMethods.ALLOW_GET_HEAD,
      },
      comment: 'RSS Email Podcast Distribution - serves all files under /podcasts/ prefix',
    });

    // Add bucket policy to restrict CloudFront access to only /podcasts/* prefix
    bucket.addToResourcePolicy(new iam.PolicyStatement({
      sid: 'AllowCloudFrontServicePrincipalReadOnly',
      effect: iam.Effect.ALLOW,
      principals: [new iam.ServicePrincipal('cloudfront.amazonaws.com')],
      actions: ['s3:GetObject'],
      resources: [bucket.arnForObjects('podcasts/*')],
      conditions: {
        'StringEquals': {
          'AWS:SourceArn': `arn:aws:cloudfront::${this.account}:distribution/${podcastDistribution.distributionId}`
        }
      }
    }));

    // Store CloudFront domain in Parameter Store for Lambda to use
    new ssm.StringParameter(this, 'PodcastCloudFrontDomainParameter', {
      parameterName: PODCAST_CLOUDFRONT_DOMAIN_PARAMETER,
      stringValue: podcastDistribution.distributionDomainName,
      description: 'CloudFront distribution domain for podcast RSS feed',
      tier: ssm.ParameterTier.STANDARD,
    });

    // Output the CloudFront URL for the podcast feed
    new cdk.CfnOutput(this, 'PodcastFeedUrl', {
      value: `https://${podcastDistribution.distributionDomainName}/podcasts/feed.xml`,
      description: 'Public URL for the podcast RSS feed',
      exportName: 'PodcastFeedUrl',
    });

    new cdk.CfnOutput(this, 'PodcastDistributionDomain', {
      value: podcastDistribution.distributionDomainName,
      description: 'CloudFront distribution domain for podcast content',
      exportName: 'PodcastDistributionDomain',
    });
  }
}
