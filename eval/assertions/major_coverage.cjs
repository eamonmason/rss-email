// Asserts the brief surfaces every "must-cover" major story for this fixture,
// regardless of work-relevance. Titles are matched normalised + substring.
const fs = require('fs');

const norm = (s) => (s || '').toLowerCase().replace(/\s+/g, ' ').trim();

module.exports = (output, context) => {
  const brief = JSON.parse(output);

  // Every article title the brief featured: category themes + personal block.
  const surfaced = [];
  for (const body of Object.values(brief.categories || {})) {
    for (const theme of (body.themes || [])) {
      for (const t of (theme.top_articles || [])) surfaced.push(norm(t));
    }
  }
  for (const t of ((brief.personal || {}).top_stories || [])) surfaced.push(norm(t));

  const expected = JSON.parse(fs.readFileSync(context.vars.expected_major, 'utf-8'));
  const mustCover = expected.must_cover || [];

  const missing = mustCover.filter((title) => {
    const n = norm(title);
    return !surfaced.some((s) => s.includes(n) || n.includes(s));
  });

  const covered = mustCover.length - missing.length;
  const score = mustCover.length ? covered / mustCover.length : 1;

  return missing.length === 0
    ? { pass: true, score, reason: `all ${mustCover.length} major stories surfaced` }
    : { pass: false, score, reason: `missing major stories: ${missing.join(' | ')}` };
};
