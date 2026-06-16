// Deterministic structural checks on the brief synthesis output.
// Guards the AI/ML key-normalisation fix and signal-strength validity.
const fs = require('fs');

const VALID_SIGNALS = new Set(['HIGH', 'STRATEGIC', 'GENERAL']);

module.exports = (output, context) => {
  let brief;
  try {
    brief = JSON.parse(output);
  } catch (e) {
    return { pass: false, score: 0, reason: `output is not valid JSON: ${e.message}` };
  }

  const cfg = JSON.parse(fs.readFileSync('src/rss_email/brief_config.json', 'utf-8'));
  const known = new Set([...(cfg.themed_categories || []), ...(cfg.personal_categories || [])]);
  const keys = Object.keys(brief.categories || {});

  const problems = [];

  // No sanitised keys (the AI_ML bug): every category key must be a configured name.
  for (const k of keys) {
    if (!known.has(k)) {
      problems.push(`category key "${k}" is not a configured category (expected canonical name e.g. "AI/ML")`);
    }
  }

  // AI/ML had content in the digest, so it must appear under its slashed name.
  if (!keys.includes('AI/ML')) {
    problems.push('expected an "AI/ML" category (got: ' + JSON.stringify(keys) + ')');
  }

  // Every theme has a valid signal strength.
  for (const [cat, body] of Object.entries(brief.categories || {})) {
    for (const theme of (body.themes || [])) {
      if (!VALID_SIGNALS.has(theme.signal_strength)) {
        problems.push(`invalid signal_strength "${theme.signal_strength}" in ${cat}`);
      }
    }
  }

  return problems.length === 0
    ? { pass: true, score: 1, reason: `schema OK (${keys.length} canonical categories)` }
    : { pass: false, score: 0, reason: problems.join('; ') };
};
