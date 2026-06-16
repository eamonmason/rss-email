// Asserts high-tier sources (independent blogs, Hacker News, Reddit, ...) are
// NOT under-represented among the stories the brief featured, compared to their
// share of the digest. Tier definitions are read from brief_config.json so the
// Python pipeline and this check stay in sync.
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

module.exports = (output, context) => {
  const cfg = JSON.parse(fs.readFileSync('src/rss_email/brief_config.json', 'utf-8'));
  const digest = JSON.parse(fs.readFileSync(context.vars.fixture, 'utf-8'));

  // Flatten the digest into {title, tier}.
  const articles = [];
  for (const items of Object.values(digest)) {
    for (const it of items) articles.push({ n: norm(it.title), tier: tier(it.source, cfg) });
  }
  const digestHigh = articles.filter((a) => a.tier === 'high').length / (articles.length || 1);

  // Titles the brief featured.
  const brief = JSON.parse(output);
  const surfacedTitles = [];
  for (const body of Object.values(brief.categories || {})) {
    for (const theme of (body.themes || [])) {
      for (const t of (theme.top_articles || [])) surfacedTitles.push(norm(t));
    }
  }
  for (const t of ((brief.personal || {}).top_stories || [])) surfacedTitles.push(norm(t));

  // Resolve each surfaced title to a digest article's tier (dedup).
  const seen = new Set();
  const tiers = [];
  for (const s of surfacedTitles) {
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
