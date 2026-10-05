"""Prompt text for the RSS Brief synthesis call (see ``brief_generator.py``).

Kept apart from the generator so the editorial rules - what to select, how
long to be, and how to stay faithful to the sources - read as one document.
"""

# Note: the ``{...}`` placeholders are substituted with ``str.replace`` (not
# ``str.format``) so the literal JSON braces in the schema below survive.
PROMPT_TEMPLATE = """You are synthesising a day's RSS digest articles for a specific reader.
The reader cannot read hundreds of articles: your job is to pick the few worth their time
and say, briefly and accurately, what the rest of the day adds up to. Be concise.

TODAY: {DATE}

READER PROFILE:
{READER_PROFILE}

PERSONAL INTERESTS:
{PERSONAL_INTERESTS}

{PREVIOUS_CONTEXT}
Produce:
1. must_read: the {MUST_READ_MIN}-{MUST_READ_MAX} articles most worth opening today, across all
   categories, best first. Each gets one sentence saying what the reader gets from reading it
   (not a restatement of the headline). At most 2 may be personal or light reading.
2. For each category below, 1-{MAX_THEMES_PER_CATEGORY} themes - fewer is better. At most
   {MAX_TOTAL_THEMES} themes in total across all categories. Omit a category entirely when it
   has nothing notable. Use each category key EXACTLY as given (keep slashes and
   punctuation, e.g. "AI/ML").
3. cross_cutting: at most {CROSS_CUTTING_MAX} signals, only where a real trend spans
   categories. An empty list is fine.

Return ONLY valid JSON, no markdown, no backticks, matching this schema:

{
  "must_read": [
    { "id": "<id>", "why": "one sentence: what the reader gets from reading it" }
  ],
  "<CATEGORY_KEY>": {
    "themes": [
      {
        "theme": "5-8 words",
        "signal_strength": "HIGH | STRATEGIC | GENERAL",
        "tldr": "1-2 sentences",
        "top_articles": ["<id1>", "<id2>"],
        "relevance_to_reader": "one specific sentence, or null"
      }
    ]
  },
  ... one object per category that has themes ...,
  "cross_cutting": [
    { "signal": "...", "categories_involved": ["c1","c2"], "implication": "..." }
  ],
  "personal": { "top_stories": ["<id1>","<id2>","<id3>"], "summary": "1-2 sentences" }
}

Signal strength:
- HIGH      = paradigm shift, affects the reader's decisions now
- STRATEGIC = watch-list item, 6-18 month horizon
- GENERAL   = awareness only, no action

{MAJOR_STORY_RULE}

{SOURCE_RULE}

{FIDELITY_RULE}

Each article below is listed as "(ID) [Source] Title", e.g. "(12) [Hacker News] Title".
In must_read, top_articles, and top_stories, cite articles ONLY by their numeric ID
(e.g. "12") - never the title or source text, and never include the brackets or
parentheses themselves. Cite each article in at most one theme, with 1-3 articles per
theme. These numeric-id citations belong ONLY inside the must_read/top_articles/top_stories
fields - never write an id citation (e.g. "(article 3)" or just "(3)") inline in prose.
why, tldr, relevance_to_reader, implication, and summary are plain-prose fields: write
them as complete sentences with no citation markers of any kind.

ARTICLES:
{ARTICLES_BY_CATEGORY}
"""

MAJOR_STORY_RULE = (
    "Be ruthless with genuine noise: drop incremental patch notes, repetitive market "
    "commentary, minor funding rounds, and routine corporate news (layoffs, rebrands, "
    "courses) with no wider consequence. BUT never drop a genuinely major story merely "
    "because it is not work-relevant - industry-shifting announcements, major outages or "
    "incidents, large acquisitions or IPOs, and high-impact societal tech stories must "
    "appear as themes (set relevance_to_reader to null when they do not bear on the "
    "reader's job). This is a personal feed as much as a work feed."
)

RUTHLESS_RULE = (
    "Be ruthless with noise: ignore incremental patch notes, repetitive market commentary, "
    "and minor funding rounds."
)

SOURCE_RULE = (
    "Source ranking: prefer independent blogs, Hacker News, Reddit, and similar community "
    "or primary sources over wire-service and aggregator reposts (e.g. Techmeme, Slashdot, "
    "Google News) when choosing which articles to feature. When the same story appears from "
    "multiple sources, feature the primary source (the company's own announcement or press "
    "release, the original blog post or paper, the vendor advisory) over trade-press "
    "rewrites, and otherwise the higher-quality community source."
)

FIDELITY_RULE = (
    "Accuracy rules:\n"
    "- Never claim more than the article does. Theme names are not headlines: no "
    "sensationalising. Keep the source's hedges and partial attributions (\"partly blamed "
    "on\", \"alleged\", \"reportedly\").\n"
    "- Distinguish agreed, announced, proposed, or expected from completed: an agreement "
    "to acquire is not an acquisition, a proposal is not a law.\n"
    "- Use TODAY to judge timing: never describe an event as upcoming when it may already "
    "have happened by today; say the result is not in these articles instead of "
    "predicting it.\n"
    "- Never refer to coverage the reader cannot see (\"as documented last week\", "
    "\"previously reported\"). Only mention earlier coverage that is listed in the "
    "previous-days context above (when there is one), and name its date.\n"
    "- Never feature sponsored, advertorial, or partner content, and never build a "
    "conclusion on it.\n"
    "- relevance_to_reader must name a concrete decision, system, or action for this "
    "reader. Use it on at most a third of themes, never make the same point twice, and "
    "use null otherwise - do not invent relevance. A story can still be worth featuring "
    "with null relevance."
)

PREVIOUS_CONTEXT_RULE = (
    "Continuity rule: the PREVIOUS DAYS block above is background only, not today's "
    "material - never cite it in top_articles/top_stories, only today's numeric ids. "
    "If today's articles cover a story already reported with no real development since "
    "then, do not present it as a new theme: omit it, or fold a one-line update into a "
    "related theme instead of restating the old facts as breaking news. If a story HAS "
    "moved forward, write the theme around what's new (referencing the earlier date, "
    "e.g. \"since Tuesday's...\") rather than re-explaining it from scratch."
)
