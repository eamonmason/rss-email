/**
 * CloudWatch Logs to SNS Forwarder
 * 
 * This Lambda function processes CloudWatch log events and forwards
 * ERROR and WARNING messages to an SNS topic for notification.
 */
const { SNSClient, PublishCommand } = require('@aws-sdk/client-sns');
const zlib = require('zlib');

// Initialize SNS client - allow for dependency injection in testing
let snsClient;

/**
 * Initializes the AWS SNS client
 * @returns {SNSClient} The SNS client
 */
const getSNSClient = () => {
  if (!snsClient) {
    snsClient = new SNSClient();
  }
  return snsClient;
};

/**
 * Detects the log level from a log message
 * @param {string} message - The log message to analyze
 * @returns {string} The detected log level ('ERROR' or 'WARNING')
 */
const detectLogLevel = (message) => {
  if (!message) return 'UNKNOWN';
  
  const lowerMessage = message.toLowerCase();
  
  if (lowerMessage.includes('error')) {
    return 'ERROR';
  } else if (lowerMessage.includes('warning') || lowerMessage.includes('warn')) {
    return 'WARNING';
  }
  
  return 'INFO';
};

/**
 * Publishes a log event to SNS
 * @param {object} logData - The CloudWatch log data
 * @param {object} logEvent - The individual log event
 * @returns {Promise} Promise resolving to the SNS publish result
 */
const publishToSNS = async (logData, logEvent) => {
  const message = logEvent.message || '';
  const logLevel = detectLogLevel(message);
  
  const topicArn = process.env.SNS_TOPIC_ARN;
  if (!topicArn) {
    throw new Error('Missing required environment variable: SNS_TOPIC_ARN');
  }
  
  const subject = `[${logLevel}] RSS Email Alert`;
  const snsMessage = `
Log Group: ${logData.logGroup}
Log Stream: ${logData.logStream}
Time: ${new Date(logEvent.timestamp).toISOString()}
Message: ${message}
`;

  const params = {
    TopicArn: topicArn,
    Subject: subject,
    Message: snsMessage
  };

  const command = new PublishCommand(params);
  return getSNSClient().send(command);
};

/**
 * Decodes and parses CloudWatch Logs data from the event
 * @param {object} event - The Lambda event
 * @returns {object} The decoded log data
 */
const decodeLogData = (event) => {
  if (!event.awslogs || !event.awslogs.data) {
    throw new Error('Invalid event structure: missing awslogs.data');
  }
  
  const payload = Buffer.from(event.awslogs.data, 'base64');
  const decompressed = zlib.gunzipSync(payload).toString('utf-8');
  return JSON.parse(decompressed);
};

/**
 * Lambda handler function
 * @param {object} event - The Lambda event containing CloudWatch log data
 * @returns {object} Response object
 */
exports.handler = async (event) => {
  console.log('Received event:', JSON.stringify(event, null, 2));
  
  try {
    // Decode and parse the log data
    const logData = decodeLogData(event);
    console.log('Processing log events from:', logData.logGroup);
    
    // Track results for each event
    const results = {
      processed: 0,
      successful: 0,
      failed: 0
    };
    
    // Process each log event
    for (const logEvent of logData.logEvents) {
      try {
        results.processed++;
        await publishToSNS(logData, logEvent);
        results.successful++;
        console.log('Successfully published log event to SNS');
      } catch (error) {
        results.failed++;
        console.error('Error publishing log event to SNS:', error);
      }
    }
    
    return { 
      statusCode: 200, 
      body: `Log events processed: ${results.processed}, successful: ${results.successful}, failed: ${results.failed}` 
    };
  } catch (error) {
    console.error('Error processing CloudWatch Logs data:', error);
    return { statusCode: 500, body: `Error: ${error.message}` };
  }
};

// For testing purposes
exports.detectLogLevel = detectLogLevel;
exports.decodeLogData = decodeLogData;
exports.setSNSClient = (client) => { snsClient = client; };