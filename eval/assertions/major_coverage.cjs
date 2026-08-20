// Asserts the brief surfaces every "must-cover" major story for this fixture,
// regardless of work-relevance. Titles are matched normalised + substring.
//
// theme.top_articles / personal.top_stories hold numeric article ids (see
// ensure_article_ids in brief_generator.py), not titles - resolve them via
// assignIds() before matching. A reference that isn't a known id is treated
// as literal text (defensive: a model that ignored the id instruction).
const fs = require('fs');

const norm = (s) => (s || '').toLowerCase().replace(/\s+/g, ' ').trim();

// Mirrors ensure_article_ids(): flatten the digest in dict/list order and
// number articles from 1. No config/category-order dependency, since both
// languages walk the same parsed JSON in the same insertion order.
function assignIds(digest) {
  const idMap = {};
  let counter = 0;
  for (const items of Object.values(digest)) {
    for (const it of items) {
      counter += 1;
      idMap[String(counter)] = { title: it.title, source: it.source };
    }
  }
  return idMap;
}

module.exports = (output, context) => {
  const brief = JSON.parse(output);
  const digest = JSON.parse(fs.readFileSync(context.vars.fixture, 'utf-8'));
  const idMap = assignIds(digest);
  const resolve = (ref) => (idMap[ref] ? idMap[ref].title : ref);

  // Every article title the brief featured: category themes + personal block.
  const surfaced = [];
  for (const body of Object.values(brief.categories || {})) {
    for (const theme of (body.themes || [])) {
      for (const t of (theme.top_articles || [])) surfaced.push(norm(resolve(t)));
    }
  }
  for (const t of ((brief.personal || {}).top_stories || [])) surfaced.push(norm(resolve(t)));

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
