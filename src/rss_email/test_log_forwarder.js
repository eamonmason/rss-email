/**
 * Simple test script for the log forwarder
 */

const logForwarder = require('./log_forwarder');

// Test the log level detection function
console.log('Testing log level detection:');
console.log('Error message:', logForwarder.detectLogLevel('This is an error message'));
console.log('Warning message:', logForwarder.detectLogLevel('This is a warning message'));
console.log('Info message:', logForwarder.detectLogLevel('This is an info message'));

console.log('Log forwarder loaded successfully');
