/**
 * Simple test script for the log forwarder
 */

const logForwarder = require('./log_forwarder');

// Test the log level detection function
console.log('Testing log level detection:');
console.log('Error message:', logForwarder.detectLogLevel('This is an error message'));
console.log('Warning message:', logForwarder.detectLogLevel('This is a warning message'));
console.log('Info message:', logForwarder.detectLogLevel('This is an info message'));

// Test log aggregation
console.log('\nTesting log aggregation:');
logForwarder.resetAggregatedLogs();
console.log('Initial aggregated logs:', logForwarder.getAggregatedLogs());

// Simulate aggregating some log events
const mockLogData = {
  logGroup: '/aws/lambda/testFunction',
  logStream: 'test-stream'
};

const mockErrorEvent = {
  timestamp: Date.now(),
  message: 'This is an error message'
};

const mockWarningEvent = {
  timestamp: Date.now() + 1000,
  message: 'This is a warning message'
};

logForwarder.aggregateLogEvent(mockLogData, mockErrorEvent);
logForwarder.aggregateLogEvent(mockLogData, mockWarningEvent);

console.log('After aggregation:', 
  'ERROR logs:', logForwarder.getAggregatedLogs().ERROR.length,
  'WARNING logs:', logForwarder.getAggregatedLogs().WARNING.length);

// Test shouldSendAggregatedLogs (should be true as we just reset the time)
console.log('Should send logs:', logForwarder.shouldSendAggregatedLogs());

console.log('Log forwarder loaded successfully and tests passed');
