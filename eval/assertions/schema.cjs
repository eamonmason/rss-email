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

  const cfg = JSON.parse(fs.readFileSync('../src/rss_email/brief_config.json', 'utf-8'));
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

  // Every theme has a valid signal strength, and the brief respects its length caps.
  const perCategory = cfg.max_themes_per_category ?? 3;
  const maxTotal = cfg.max_total_themes ?? 12;
  const mustReadMax = cfg.must_read_max ?? 8;
  let totalThemes = 0;
  for (const [cat, body] of Object.entries(brief.categories || {})) {
    const themes = body.themes || [];
    totalThemes += themes.length;
    if (themes.length > perCategory) {
      problems.push(`${cat} has ${themes.length} themes (max ${perCategory})`);
    }
    for (const theme of themes) {
      if (!VALID_SIGNALS.has(theme.signal_strength)) {
        problems.push(`invalid signal_strength "${theme.signal_strength}" in ${cat}`);
      }
    }
  }
  if (totalThemes > maxTotal) {
    problems.push(`${totalThemes} themes in total (max ${maxTotal})`);
  }

  // The "Read these" list is the brief's lead: it must exist and stay short.
  const mustRead = brief.must_read || [];
  if (mustRead.length < 1 || mustRead.length > mustReadMax) {
    problems.push(`must_read has ${mustRead.length} entries (expected 1-${mustReadMax})`);
  }
  for (const item of mustRead) {
    if (!item.why || !item.why.trim()) {
      problems.push(`must_read entry ${item.id} has no "why"`);
    }
  }

  return problems.length === 0
    ? { pass: true, score: 1, reason: `schema OK (${keys.length} canonical categories)` }
    : { pass: false, score: 0, reason: problems.join('; ') };
};
