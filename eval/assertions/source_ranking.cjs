// Asserts high-tier sources (independent blogs, Hacker News, Reddit, ...) are
// NOT under-represented among the stories the brief featured, compared to their
// share of the digest. Tier definitions are read from brief_config.json so the
// Python pipeline and this check stay in sync.
//
// theme.top_articles / personal.top_stories hold numeric article ids (see
// ensure_article_ids in brief_generator.py), not titles - resolve them via
// assignIds() to get the source directly. A reference that isn't a known id
// falls back to the previous normalised-substring re-match against the
// digest (defensive: a model that ignored the id instruction).
const fs = require('fs');

const norm = (s) => (s || '').toLowerCase().replace(/\s+/g, ' ').trim();

function tier(name, cfg) {
  const lowered = (name || '').toLowerCase();
  if (!lowered) return 'medium';
  for (const t of (cfg.prioritised_sources || [])) {
    if (t && lowered.includes(t.toLowerCase())) return 'high';
  }
  for (const t of (cfg.deprioritised_sources || [])) {
    if (t && lowered.includes(t.toLowerCase())) return 'low';
  }
  return 'medium';
}

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
  const cfg = JSON.parse(fs.readFileSync('../src/rss_email/brief_config.json', 'utf-8'));
  const digest = JSON.parse(fs.readFileSync(context.vars.fixture, 'utf-8'));
  const idMap = assignIds(digest);

  // Flatten the digest into {title, tier}, for the fallback re-match.
  const articles = [];
  for (const items of Object.values(digest)) {
    for (const it of items) articles.push({ n: norm(it.title), tier: tier(it.source, cfg) });
  }
  const digestHigh = articles.filter((a) => a.tier === 'high').length / (articles.length || 1);

  // Article references the brief featured, resolved to a tier: by id first,
  // falling back to a normalised-substring title re-match.
  const brief = JSON.parse(output);
  const refs = [];
  for (const body of Object.values(brief.categories || {})) {
    for (const theme of (body.themes || [])) {
      for (const t of (theme.top_articles || [])) refs.push(t);
    }
  }
  for (const t of ((brief.personal || {}).top_stories || [])) refs.push(t);

  const seen = new Set();
  const tiers = [];
  for (const ref of refs) {
    const byId = idMap[ref];
    if (byId) {
      const key = norm(byId.title);
      if (!seen.has(key)) {
        seen.add(key);
        tiers.push(tier(byId.source, cfg));
      }
      continue;
    }
    const s = norm(ref);
    const match = articles.find((a) => a.n === s || a.n.includes(s) || s.includes(a.n));
    if (match && !seen.has(match.n)) {
      seen.add(match.n);
      tiers.push(match.tier);
    }
  }
  if (tiers.length === 0) {
    return { pass: false, score: 0, reason: 'no surfaced articles matched the digest' };
  }
  const surfacedHigh = tiers.filter((t) => t === 'high').length / tiers.length;

  const tol = 0.05;
  const pass = surfacedHigh >= digestHigh - tol;
  const pct = (x) => `${(x * 100).toFixed(0)}%`;
  return {
    pass,
    score: pass ? 1 : Math.max(0, surfacedHigh / (digestHigh || 1)),
    reason: `high-tier share: surfaced ${pct(surfacedHigh)} vs digest ${pct(digestHigh)} `
      + `(${tiers.length} matched)`,
  };
};
