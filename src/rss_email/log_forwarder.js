/**
 * CloudWatch Logs to SNS Forwarder
 * 
 * This Lambda function processes CloudWatch log events and forwards
 * aggregated ERROR and WARNING messages to an SNS topic for notification
 * no more than once every 5 minutes.
 */
const { SNSClient, PublishCommand } = require('@aws-sdk/client-sns');
const zlib = require('zlib');

// Initialize SNS client - allow for dependency injection in testing
let snsClient;

// Store aggregated log events and the last time they were sent
const aggregatedLogs = {
  ERROR: [],
  WARNING: [],
  INFO: [],
  lastSentTime: 0 // Unix timestamp in ms
};

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
 * Aggregates a log event to be sent later
 * @param {object} logData - The CloudWatch log data
 * @param {object} logEvent - The individual log event
 */
const aggregateLogEvent = (logData, logEvent) => {
  const message = logEvent.message || '';
  const logLevel = detectLogLevel(message);
  
  // Only aggregate ERROR and WARNING logs
  if (logLevel === 'ERROR' || logLevel === 'WARNING') {
    aggregatedLogs[logLevel].push({
      logGroup: logData.logGroup,
      logStream: logData.logStream,
      timestamp: logEvent.timestamp,
      message: message
    });
  }
};

/**
 * Publishes aggregated log events to SNS
 * @returns {Promise} Promise resolving to the SNS publish result
 */
const publishAggregatedLogsToSNS = async () => {
  const topicArn = process.env.SNS_TOPIC_ARN;
  if (!topicArn) {
    throw new Error('Missing required environment variable: SNS_TOPIC_ARN');
  }
  
  const errorCount = aggregatedLogs.ERROR.length;
  const warningCount = aggregatedLogs.WARNING.length;
  
  if (errorCount === 0 && warningCount === 0) {
    console.log('No logs to send');
    return null;
  }
  
  // Format a summary subject line
  let subject = '';
  if (errorCount > 0 && warningCount > 0) {
    subject = `[ALERT] RSS Email: ${errorCount} errors, ${warningCount} warnings`;
  } else if (errorCount > 0) {
    subject = `[ERROR] RSS Email: ${errorCount} error${errorCount > 1 ? 's' : ''}`;
  } else {
    subject = `[WARNING] RSS Email: ${warningCount} warning${warningCount > 1 ? 's' : ''}`;
  }
  
  // Format the message body with all aggregated logs
  let messageBody = 'Aggregated log events:\n\n';
  
  if (errorCount > 0) {
    messageBody += `=== ERRORS (${errorCount}) ===\n\n`;
    aggregatedLogs.ERROR.forEach(log => {
      messageBody += `Time: ${new Date(log.timestamp).toISOString()}\n`;
      messageBody += `Log Group: ${log.logGroup}\n`;
      messageBody += `Log Stream: ${log.logStream}\n`;
      messageBody += `Message: ${log.message}\n\n`;
    });
  }
  
  if (warningCount > 0) {
    messageBody += `=== WARNINGS (${warningCount}) ===\n\n`;
    aggregatedLogs.WARNING.forEach(log => {
      messageBody += `Time: ${new Date(log.timestamp).toISOString()}\n`;
      messageBody += `Log Group: ${log.logGroup}\n`;
      messageBody += `Log Stream: ${log.logStream}\n`;
      messageBody += `Message: ${log.message}\n\n`;
    });
  }
  
  const params = {
    TopicArn: topicArn,
    Subject: subject,
    Message: messageBody
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
 * Should we send the aggregated logs now?
 * Checks if it's been at least 5 minutes since the last send
 * @returns {boolean} True if it's time to send logs
 */
const shouldSendAggregatedLogs = () => {
  const now = Date.now();
  const fiveMinutesInMs = 4 * 60 * 1000;
  return (now - aggregatedLogs.lastSentTime) >= fiveMinutesInMs;
};

/**
 * Reset the aggregated logs after they've been sent
 */
const resetAggregatedLogs = () => {
  aggregatedLogs.ERROR = [];
  aggregatedLogs.WARNING = [];
  aggregatedLogs.INFO = [];
  aggregatedLogs.lastSentTime = Date.now();
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
    let logsAggregated = 0;
    
    // Process each log event by aggregating them
    for (const logEvent of logData.logEvents) {
      aggregateLogEvent(logData, logEvent);
      logsAggregated++;
    }
    
    console.log(`Aggregated ${logsAggregated} log events (${aggregatedLogs.ERROR.length} errors, ${aggregatedLogs.WARNING.length} warnings)`);
    
    // Check if we should send the aggregated logs (if it's been at least 5 minutes)
    let sendResult = null;
    if (shouldSendAggregatedLogs() && (aggregatedLogs.ERROR.length > 0 || aggregatedLogs.WARNING.length > 0)) {
      try {
        console.log('Sending aggregated logs - time threshold reached');
        sendResult = await publishAggregatedLogsToSNS();
        resetAggregatedLogs();
        console.log('Successfully sent aggregated logs');
      } catch (error) {
        console.error('Error sending aggregated logs:', error);
      }
    } else {
      console.log('Not sending aggregated logs yet - time threshold not reached or no logs to send');
    }
    
    return { 
      statusCode: 200, 
      body: `Log events processed and aggregated: ${logsAggregated}, emails sent: ${sendResult ? '1' : '0'}` 
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
exports.aggregateLogEvent = aggregateLogEvent;
exports.publishAggregatedLogsToSNS = publishAggregatedLogsToSNS;
exports.shouldSendAggregatedLogs = shouldSendAggregatedLogs;
exports.resetAggregatedLogs = resetAggregatedLogs;
// Export aggregated logs for testing purposes
exports.getAggregatedLogs = () => aggregatedLogs;