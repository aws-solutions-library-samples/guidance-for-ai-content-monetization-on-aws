/**
 * Unit tests for comparisonFidelity.ts (R14, design §3c).
 *
 * The fixtures mirror what the GEO pipeline actually produced for a live AWS blog run:
 * the title duplicated as H1 and H2, a one-cell `| Voiced by Polly |` widget table, and
 * `\.`/`\|` escapes in the heading. A quality check must surface exactly those.
 */

import { describe, expect, it } from 'vitest';

import {
  articleReference,
  articleRegion,
  buildFidelityReport,
  compareStructure,
  coverageStatus,
  fidelityScore,
  htmlStructure,
  leakageStatus,
  markdownStructure,
  markdownToText,
  matchSentences,
  normaliseSentence,
  schemaTitleLine,
  scoreStatus,
  splitSchemaParts,
  splitSchemaTitle,
  splitSentences,
} from '../comparisonFidelity';

const ARTICLE_HTML = `<!doctype html><html><head><title>Lanes | Daily</title></head><body>
<nav><a href="/">Home</a> <a href="/sport">Sport</a></nav>
<article>
  <h1>How Protected Lanes Reshaped a City</h1>
  <p>Protected lanes change who feels safe enough to ride. The city added forty kilometres in two years.</p>
  <h2>What changed</h2>
  <p>Ridership among women <strong>doubled</strong>, according to the <a href="/report">annual count</a>.</p>
  <figure><img src="/fig.svg" alt="A lane"></figure>
  <p>Opponents argued that parking would vanish. It did not.</p>
</article>
<aside class="related">Related: <a href="/a">Other story</a></aside>
<footer>Copyright</footer>
</body></html>`;

// What a good pipeline run produces for ARTICLE_HTML (plus the two real-world artefacts).
const GOOD_MARKDOWN = `# How Protected Lanes Reshaped a City

Protected lanes change who feels safe enough to ride. The city added forty kilometres in two years.

## What changed

Ridership among women **doubled**, according to the [annual count](https://daily.example/report).

![A lane](https://daily.example/fig.svg)

Opponents argued that parking would vanish. It did not.
`;

const ARTEFACT_MARKDOWN = `# How Protected Lanes Reshaped a City \\| Daily

## How Protected Lanes Reshaped a City

|  |
| --- |
| [Voiced by Polly](https://aws.amazon.com/polly/) |

Protected lanes change who feels safe enough to ride. The city added forty kilometres in two years.

Ridership among women **doubled**, according to the [annual count](https://daily.example/report).

Related: Other story
`;

describe('markdownToText', () => {
  it('strips syntax but keeps the visible text of links, images, emphasis and code', () => {
    const text = markdownToText('# Title\n\nSee [the report](https://x/y) and ![alt text](https://x/i.png), **bold**, `code`.');
    expect(text).toBe('Title\n\nSee the report and alt text, bold, code.');
  });
  it('drops front matter, table rules and pipes, and unescapes punctuation', () => {
    const text = markdownToText('---\ntitle: t\n---\n|  |\n| --- |\n| cell |\n\nGPT-6\\.1 Terra \\| Blog');
    expect(text).toContain('cell');
    expect(text).not.toContain('---');
    expect(text).toContain('GPT-6.1 Terra | Blog');
  });
  it('keeps fenced code bodies as text', () => {
    expect(markdownToText('```js\nconst a = 1;\n```')).toBe('const a = 1;');
  });
});

describe('sentences', () => {
  it('splits on terminal punctuation and on line breaks', () => {
    expect(splitSentences('One sentence. Two! Three?\nA heading without a stop\nFour.')).toEqual([
      'One sentence.',
      'Two!',
      'Three?',
      'A heading without a stop',
      'Four.',
    ]);
  });
  it('normalises case, quotes, punctuation and whitespace', () => {
    expect(normaliseSentence('  The “City’s” Lanes,  doubled!  ')).toBe('the city s lanes doubled');
  });
});

describe('matchSentences', () => {
  it('reports full coverage and no leakage when the Markdown is the article', () => {
    const c = matchSentences(
      'Protected lanes change who feels safe enough to ride. The city added forty kilometres in two years.',
      'Protected lanes change who feels safe enough to ride.\n\nThe city added forty kilometres in two years.',
    );
    expect(c.coverage).toBe(1);
    expect(c.leakage).toBe(0);
    expect(c.exactMatches).toBe(2);
    expect(c.missingFromMarkdown).toEqual([]);
    expect(c.notArticle).toEqual([]);
  });

  it('ignores formatting differences (emphasis, link syntax, case) as loss', () => {
    const c = matchSentences(
      'Ridership among women doubled, according to the annual count.',
      markdownToText('Ridership among women **doubled**, according to the [Annual Count](https://x).'),
    );
    expect(c.coverage).toBe(1);
  });

  it('matches a re-flowed sentence fuzzily and counts it separately', () => {
    const c = matchSentences(
      'The city added forty kilometres of lanes in two years.',
      'The city added forty kilometres of protected lanes in two years.',
    );
    expect(c.coverage).toBe(1);
    expect(c.fuzzyMatches).toBe(1);
    expect(c.exactMatches).toBe(0);
  });

  it('shows what was lost and what leaked, as text', () => {
    const c = matchSentences(
      'Kept sentence number one here. Dropped paragraph about parking vanishing entirely.',
      'Kept sentence number one here.\nVoiced by Polly\nRelated stories you might like today.',
    );
    expect(c.coverage).toBe(0.5);
    expect(c.missingFromMarkdown).toEqual(['Dropped paragraph about parking vanishing entirely.']);
    // Short residue ("Voiced by Polly") is listed but not in the denominator.
    expect(c.notArticle).toEqual([
      { text: 'Voiced by Polly', origin: 'unknown' },
      { text: 'Related stories you might like today.', origin: 'unknown' },
    ]);
    expect(c.markdownSentences).toBe(2);
    expect(c.leakage).toBe(0.5);
  });

  it('returns null figures when a side has no substantive sentences', () => {
    const c = matchSentences('', 'Only markdown here with words.');
    expect(c.coverage).toBeNull();
    expect(c.leakage).toBe(1);
  });

  it('never matches one Markdown sentence to two article sentences', () => {
    const c = matchSentences('Same sentence repeated here. Same sentence repeated here.', 'Same sentence repeated here.');
    expect(c.articleMatched).toBe(1);
    expect(c.articleSentences).toBe(2);
    expect(c.coverage).toBe(0.5);
  });
});

describe('structure', () => {
  it('counts the HTML article region only (nav and aside links excluded)', () => {
    const s = htmlStructure(ARTICLE_HTML);
    expect(s.headings).toEqual([1, 1, 0, 0, 0, 0]);
    expect(s.links).toBe(1);
    expect(s.images).toBe(1);
    expect(s.emphasis).toBe(1);
    expect(s.tables).toBe(0);
  });

  it('counts Markdown structure by syntax', () => {
    const s = markdownStructure(GOOD_MARKDOWN);
    expect(s.headings).toEqual([1, 1, 0, 0, 0, 0]);
    expect(s.links).toBe(1);
    expect(s.images).toBe(1);
    expect(s.emphasis).toBe(1);
    expect(s.tables).toBe(0);
  });

  it('flags nothing for a faithful conversion', () => {
    expect(compareStructure(ARTICLE_HTML, GOOD_MARKDOWN).flags).toEqual([]);
  });

  it('flags the duplicated title heading, the one-cell widget table and escaped punctuation', () => {
    const { flags, markdown } = compareStructure(ARTICLE_HTML, ARTEFACT_MARKDOWN);
    expect(markdown.tables).toBe(1);
    expect(flags).toEqual(
      expect.arrayContaining([
        { kind: 'escaped_punctuation_in_heading', text: 'How Protected Lanes Reshaped a City \\| Daily' },
        { kind: 'single_cell_table', text: 'table 1' },
        { kind: 'missing_structure', what: 'images' },
      ]),
    );
  });

  it('flags a heading repeated verbatim', () => {
    const { flags } = compareStructure(ARTICLE_HTML, '# Same Title\n\n## Same Title\n\nBody.');
    expect(flags).toContainEqual({ kind: 'duplicate_heading', text: 'Same Title' });
  });

  it('flags structure the HTML has but the Markdown lost', () => {
    const { flags } = compareStructure(ARTICLE_HTML, 'Just a paragraph with no structure at all.');
    const kinds = flags.filter((f) => f.kind === 'missing_structure').map((f) => (f as { what: string }).what);
    expect(kinds).toEqual(expect.arrayContaining(['headings', 'links', 'images', 'emphasis']));
  });
});

describe('buildFidelityReport', () => {
  it('uses the <article> region as the reference and reports a faithful run as good', () => {
    const r = buildFidelityReport(ARTICLE_HTML, GOOD_MARKDOWN);
    expect(r.articleFallback).toBe(false);
    expect(r.articleSide).not.toContain('Copyright');
    expect(r.coverage.coverage).toBe(1);
    expect(r.coverage.leakage).toBe(0);
    expect(r.structure.flags).toEqual([]);
  });

  it('surfaces the real-world artefacts: lost paragraph, leaked residue, flags', () => {
    const r = buildFidelityReport(ARTICLE_HTML, ARTEFACT_MARKDOWN);
    expect(r.coverage.coverage).toBeLessThan(1);
    expect(r.coverage.missingFromMarkdown.join(' ')).toContain('Opponents argued');
    expect(r.coverage.notArticle.map((i) => i.text).join(' ')).toContain('Voiced by Polly');
    expect(r.structure.flags.length).toBeGreaterThan(0);
  });

  it('falls back to the whole page when there is no <article> or <main>, and says so', () => {
    const r = buildFidelityReport('<html><body><p>Only a paragraph of body text here.</p></body></html>', 'Only a paragraph of body text here.');
    expect(r.articleFallback).toBe(true);
    expect(r.coverage.coverage).toBe(1);
  });
});

describe('thresholds', () => {
  it('colours coverage and leakage per design §3c', () => {
    expect(coverageStatus(0.96)).toBe('good');
    expect(coverageStatus(0.85)).toBe('warning');
    expect(coverageStatus(0.5)).toBe('poor');
    expect(coverageStatus(null)).toBeNull();
    expect(leakageStatus(0.03)).toBe('good');
    expect(leakageStatus(0.1)).toBe('warning');
    expect(leakageStatus(0.4)).toBe('poor');
  });
});

// ── 1-to-1 rules (R14.8–10, 2026-11-09) ──────────────────────────────────────
//
// Invented text shaped like the two pages checked offline on 2026-11-09 (the demo publisher's free
// article and a news story). PUBLISHER_MARKDOWN is the pipeline's real output for PUBLISHER_HTML
// (`content_pipeline.extractor.extract`, run locally): the <title> as H1, the article <header>
// dropped, the ad slot and the promo card kept, image alt text inlined, the footer dropped.

const PUBLISHER_HTML = `<html><head><title>Heat pumps win over landlords — Example Press</title>
<meta property="og:title" content="Heat pumps win over landlords"></head><body>
<header><nav><article class="teaser"><p>Teaser story in the menu that is not this page.</p></article></nav></header>
<main><article>
<header><p class="kicker">Energy</p><h1>Heat pumps win over landlords</h1>
<p class="dek">Rental owners are switching faster than homeowners, a new survey shows.</p>
<p class="byline">By Sam Rivera · May 2, 2026</p></header>
<div class="ad-slot" data-ad-size="728x90"><span>Advertisement</span> <span>Leaderboard placeholder 728x90</span></div>
<p>Landlords installed more heat pumps than any other group last year. Running costs fell by a fifth.</p>
<figure><img src="a.jpg" alt="A technician fits an outdoor unit."><figcaption>Installers report long waiting lists in spring.</figcaption></figure>
<div class="promo"><img src="b.jpg" alt="Solar panels on a school roof"><p>Related: schools turn to rooftop solar for savings.</p></div>
<footer><p><a href="/">Home</a> · License terms: RSL</p></footer>
</article></main></body></html>`;

const PUBLISHER_MARKDOWN =
  '# Heat pumps win over landlords — Example Press\n\nAdvertisement Leaderboard placeholder 728x90\n\n' +
  'Landlords installed more heat pumps than any other group last year. Running costs fell by a fifth.\n\n' +
  'A technician fits an outdoor unit.\n\nInstallers report long waiting lists in spring.\n\n' +
  'Solar panels on a school roof\n\nRelated: schools turn to rooftop solar for savings.';

describe('reference side = R5 steps 1–3 with the pipeline region choice (R14.8)', () => {
  it('skips an <article> inside nav/header/footer/aside, as the pipeline does', () => {
    const doc = new DOMParser().parseFromString(PUBLISHER_HTML, 'text/html');
    expect(articleRegion(doc)?.className).not.toBe('teaser');
    expect(articleReference(PUBLISHER_HTML).text).not.toContain('Teaser story');
  });

  it('removes boilerplate with the R5 heuristic and lists what it removed', () => {
    const ref = articleReference(PUBLISHER_HTML);
    expect(ref.text).toContain('Landlords installed more heat pumps');
    expect(ref.text).not.toContain('Leaderboard placeholder');
    expect(ref.text).not.toContain('License terms');
    expect(ref.excluded).toEqual(
      expect.arrayContaining([
        'Advertisement Leaderboard placeholder 728x90',
        'Home · License terms: RSL',
        'Related: schools turn to rooftop solar for savings.',
      ]),
    );
    expect(ref.altTexts).toEqual(['A technician fits an outdoor unit.']);
    expect(ref.excludedAltTexts).toEqual(['Solar panels on a school roof']);
    expect(ref.headlines).toEqual(['Heat pumps win over landlords']);
  });

  it('counts boilerplate the pipeline kept as leakage, and no longer reports dropped boilerplate as missing', () => {
    const c = buildFidelityReport(PUBLISHER_HTML, PUBLISHER_MARKDOWN).coverage;
    // One list (R14.12): every item once, labelled with its origin.
    expect(c.notArticle).toEqual([
      { text: 'Advertisement Leaderboard placeholder 728x90', origin: 'boilerplate' },
      { text: 'Solar panels on a school roof', origin: 'boilerplate' },
      { text: 'Related: schools turn to rooftop solar for savings.', origin: 'boilerplate' },
    ]);
    expect(c.missingFromMarkdown).not.toContain('Home · License terms: RSL');
    // 3 leaked of 7 counted Markdown sentences (the alt text is by design, not counted).
    expect(c.markdownSentences).toBe(7);
    expect(c.markdownUnmatched).toBe(3);
  });

  it('keeps genuine pipeline losses visible: the dek and byline the pipeline dropped with <header>', () => {
    const c = buildFidelityReport(PUBLISHER_HTML, PUBLISHER_MARKDOWN).coverage;
    expect(c.missingFromMarkdown).toEqual([
      'Rental owners are switching faster than homeowners, a new survey shows.',
      'By Sam Rivera · May 2, 2026',
    ]);
    expect(c.articleSentences).toBe(6);
    expect(c.articleMatched).toBe(4);
  });

  it('counts structure in the cleaned region (promo and footer links excluded)', () => {
    expect(htmlStructure(PUBLISHER_HTML).links).toBe(0);
    expect(htmlStructure(PUBLISHER_HTML).images).toBe(1);
  });
});

describe('by-design additions are listed apart and not counted (R14.9)', () => {
  it('counts the headline as kept when the <title> H1 contains it, and flags the title', () => {
    const r = buildFidelityReport(PUBLISHER_HTML, PUBLISHER_MARKDOWN);
    expect(r.markdownTitle).toBe('Heat pumps win over landlords — Example Press');
    expect(r.coverage.titleMatches).toBe(1);
    expect(r.coverage.missingFromMarkdown).not.toContain('Heat pumps win over landlords');
    // A top-level finding (R14.12), no longer a structure flag.
    expect(r.titleFinding).toEqual({
      kind: 'title_not_headline',
      title: 'Heat pumps win over landlords — Example Press',
      headline: 'Heat pumps win over landlords',
      source: '<title>',
    });
    expect(r.structure.flags.map((f) => f.kind)).not.toContain('title_not_headline');
    expect(r.titleSource).toBe('<title>');
    expect(r.titleIsHeadline).toBe(false);
  });

  it('lists the alt text of an article image as added by design', () => {
    const c = buildFidelityReport(PUBLISHER_HTML, PUBLISHER_MARKDOWN).coverage;
    expect(c.addedByDesign).toEqual([{ kind: 'image_alt', text: 'A technician fits an outdoor unit.' }]);
    expect(c.notArticle.map((i) => i.text)).not.toContain('A technician fits an outdoor unit.');
  });

  it('lists a <title> H1 as added by design when the headline is outside the article region', () => {
    const html = `<html><head><title>Valley voters change course - Example Times</title></head><body>
<h1>Valley voters change course after a hard year</h1>
<main><article><p>Voters in the valley chose a new representative on Tuesday. Turnout was high this time.</p></article></main></body></html>`;
    const md = '# Valley voters change course \\- Example Times\n\nVoters in the valley chose a new representative on Tuesday. Turnout was high this time.';
    const r = buildFidelityReport(html, md);
    expect(r.coverage.addedByDesign).toEqual([
      { kind: 'document_title', text: 'Valley voters change course - Example Times', source: '<title>' },
    ]);
    expect(r.coverage.leakage).toBe(0);
    expect(r.coverage.coverage).toBe(1);
    expect(r.titleFinding).toEqual({
      kind: 'title_not_headline',
      title: 'Valley voters change course - Example Times',
      headline: 'Valley voters change course after a hard year',
      source: '<title>',
    });
  });

  it('keeps a title that matches no page source as leakage', () => {
    const r = buildFidelityReport(ARTICLE_HTML, `# A heading from nowhere at all\n\n${GOOD_MARKDOWN}`);
    expect(r.markdownTitle).toBe('A heading from nowhere at all');
    expect(r.coverage.notArticle).toContainEqual({ text: 'A heading from nowhere at all', origin: 'unknown' });
    expect(r.titleSource).toBeNull();
    // The figure's alt text ("A lane") is by design; the unexplained title is not.
    expect(r.coverage.addedByDesign).toEqual([{ kind: 'image_alt', text: 'A lane' }]);
  });

  it('splits the schema title off the first line only', () => {
    expect(splitSchemaTitle('# GPT-6\\.1 Terra \\| Blog\n\nBody text.')).toEqual({ title: 'GPT-6.1 Terra | Blog', body: '\nBody text.' });
    expect(splitSchemaTitle('## Not a title\n\nBody.').title).toBeNull();
  });

  it('without a context, matchSentences counts every Markdown sentence (unchanged behaviour)', () => {
    const c = matchSentences('Kept sentence number one here.', 'Kept sentence number one here.\nAn extra line of four words.');
    expect(c.addedByDesign).toEqual([]);
    expect(c.notArticle).toEqual([{ text: 'An extra line of four words.', origin: 'unknown' }]);
  });
});

// GOOD_MARKDOWN as Markdown schema 1.1 writes it (markdown_renderer.compose_document): the same
// title and body, with the metadata block, generated summary, generated FAQ and attribution line.
const SCHEMA_11_MARKDOWN = `# How Protected Lanes Reshaped a City

- Author: Ana Ruiz
- Publisher: Daily
- Published: 2026-11-02
- Source: <https://daily.example/lanes>

> Summary (generated): Protected lanes doubled ridership among women within two years of being built.

Protected lanes change who feels safe enough to ride. The city added forty kilometres in two years.

## What changed

Ridership among women **doubled**, according to the [annual count](https://daily.example/report).

A lane

Opponents argued that parking would vanish. It did not.

## Frequently asked questions (generated)

Generated automatically from the article above. Not part of the original article.

### How many kilometres of lanes did the city add?

The city added forty kilometres of protected lanes in two years.

### Did parking vanish as opponents argued?

No, parking did not vanish after the lanes were built.

Source: How Protected Lanes Reshaped a City by Ana Ruiz, 2026-11-02. <https://daily.example/lanes>`;

describe('Markdown schema 1.1 parts are by-design additions (R14.14)', () => {
  it('splits the metadata block, summary, FAQ and attribution line off the article body', () => {
    const parts = splitSchemaParts(SCHEMA_11_MARKDOWN);
    expect(parts.title).toBe('How Protected Lanes Reshaped a City');
    expect(parts.additions).toEqual([
      { kind: 'metadata', text: 'Author: Ana Ruiz' },
      { kind: 'metadata', text: 'Publisher: Daily' },
      { kind: 'metadata', text: 'Published: 2026-11-02' },
      { kind: 'metadata', text: 'Source: https://daily.example/lanes' },
      { kind: 'generated_summary', text: 'Protected lanes doubled ridership among women within two years of being built.' },
      { kind: 'generated_faq', text: 'How many kilometres of lanes did the city add?' },
      { kind: 'generated_faq', text: 'The city added forty kilometres of protected lanes in two years.' },
      { kind: 'generated_faq', text: 'Did parking vanish as opponents argued?' },
      { kind: 'generated_faq', text: 'No, parking did not vanish after the lanes were built.' },
      {
        kind: 'attribution',
        text: 'Source: How Protected Lanes Reshaped a City by Ana Ruiz, 2026-11-02. https://daily.example/lanes',
      },
    ]);
    expect(parts.body).toContain('Protected lanes change who feels safe enough to ride.');
    expect(parts.body).not.toMatch(/Summary \(generated\)|Frequently asked questions|Author: |^Source: /m);
    expect(parts.article.startsWith('# How Protected Lanes Reshaped a City\n\n')).toBe(true);
  });

  it('scores a faithful schema 1.1 conversion like the schema 1.0 one: the parts count in neither figure', () => {
    const r = buildFidelityReport(ARTICLE_HTML, SCHEMA_11_MARKDOWN);
    const baseline = buildFidelityReport(ARTICLE_HTML, GOOD_MARKDOWN);
    expect(r.coverage.coverage).toBe(baseline.coverage.coverage);
    expect(r.coverage.leakage).toBe(baseline.coverage.leakage);
    expect(r.score).toBe(baseline.score);
    expect(r.coverage.notArticle).toEqual(baseline.coverage.notArticle);
    // Each part once, in document order, around the alt text the body holds.
    expect(r.coverage.addedByDesign.map((a) => a.kind)).toEqual([
      'metadata', 'metadata', 'metadata', 'metadata', 'generated_summary', 'image_alt',
      'generated_faq', 'generated_faq', 'generated_faq', 'generated_faq', 'attribution',
    ]);
  });

  it('leaves the FAQ headings out of the structure comparison', () => {
    const r = buildFidelityReport(ARTICLE_HTML, SCHEMA_11_MARKDOWN);
    expect(r.structure.markdown.headings).toEqual([1, 1, 0, 0, 0, 0]);
    // The renderer keeps an image as its alt text, so the Markdown has no image: a real difference, still flagged.
    expect(r.structure.flags).toEqual([{ kind: 'missing_structure', what: 'images' }]);
  });

  it('returns a schema 1.0 document with no parts and the body unchanged', () => {
    const parts = splitSchemaParts(GOOD_MARKDOWN);
    expect(parts.additions).toEqual([]);
    expect(parts.body).toContain('Opponents argued that parking would vanish.');
    expect(buildFidelityReport(ARTICLE_HTML, GOOD_MARKDOWN).coverage.addedByDesign).toEqual([
      { kind: 'image_alt', text: 'A lane' },
    ]);
  });

  it("keeps a publisher's own list, FAQ and closing source line as article text", () => {
    const md = [
      '# Title',
      '',
      '* Author: a list item the article wrote',
      '',
      '## Frequently asked questions',
      '',
      '### Is this the publisher FAQ?',
      '',
      'Source: the article ends with this line.',
    ].join('\n');
    const parts = splitSchemaParts(md);
    expect(parts.additions).toEqual([]);
    expect(parts.body).toContain('## Frequently asked questions');
    expect(parts.body).toContain('Source: the article ends with this line.');
  });

  it('finds the summary without a metadata block, and no attribution line without one', () => {
    const parts = splitSchemaParts('# T\n\n> Summary (generated): A short generated summary here.\n\nBody sentence of the article.\n\nSource: T.');
    expect(parts.additions).toEqual([{ kind: 'generated_summary', text: 'A short generated summary here.' }]);
    expect(parts.body).toContain('Source: T.');
  });
});

describe('a byline carried by the metadata block counts as kept (R14.15)', () => {
  const html = (byline: string) => `<html><body><article>
<h1>Ports Reopen After the Strike</h1>
<p class="byline">${byline}</p>
<p>Dockworkers returned to the terminals on Monday morning. Cargo moved again within hours.</p>
<p>By then, the backlog had grown to forty ships waiting offshore.</p>
</article></body></html>`;
  // What schema 1.1 writes: the byline left the body; the metadata block carries the author and date.
  const markdown = (author: string | null) =>
    [
      '# Ports Reopen After the Strike',
      '',
      ...(author ? [`- Author: ${author}`, '- Published: 2026-11-02', ''] : []),
      'Dockworkers returned to the terminals on Monday morning. Cargo moved again within hours.',
      '',
      'By then, the backlog had grown to forty ships waiting offshore.',
    ].join('\n');

  it('keeps "By <author> · date · section" when the Author line names that author', () => {
    const r = buildFidelityReport(html('By Ana Ruiz · November 2, 2026 · Transport'), markdown('Ana Ruiz'));
    expect(splitSchemaParts(markdown('Ana Ruiz')).author).toBe('Ana Ruiz');
    expect(r.coverage.bylineMatches).toBe(1);
    expect(r.coverage.missingFromMarkdown).toEqual([]);
    expect(r.coverage.coverage).toBe(1);
    expect(r.score).toBe(100);
  });

  it('keeps a byline naming several authors, as the extractor joins them with ", "', () => {
    const r = buildFidelityReport(html('By Ana Ruiz and Ben Li · November 2, 2026'), markdown('Ana Ruiz, Ben Li'));
    expect(r.coverage.bylineMatches).toBe(1);
    expect(r.coverage.missingFromMarkdown).toEqual([]);
  });

  it('keeps counting the byline as missing for another author, or without an Author line', () => {
    const other = buildFidelityReport(html('By Ana Ruiz · November 2, 2026 · Transport'), markdown('Ben Li'));
    expect(other.coverage.bylineMatches).toBe(0);
    expect(other.coverage.missingFromMarkdown).toEqual(['By Ana Ruiz · November 2, 2026 · Transport']);
    const none = buildFidelityReport(html('By Ana Ruiz · November 2, 2026 · Transport'), markdown(null));
    expect(none.coverage.bylineMatches).toBe(0);
    expect(none.coverage.missingFromMarkdown).toEqual(['By Ana Ruiz · November 2, 2026 · Transport']);
  });

  it('never takes an article sentence that only starts with "By" for the byline', () => {
    const md = markdown('Ana Ruiz').replace('By then, the backlog had grown to forty ships waiting offshore.', '');
    const r = buildFidelityReport(html('By Ana Ruiz · November 2, 2026 · Transport'), md);
    expect(r.coverage.bylineMatches).toBe(1);
    expect(r.coverage.missingFromMarkdown).toEqual(['By then, the backlog had grown to forty ships waiting offshore.']);
  });

  // What the extractor drops from the article's own <header> once the metadata block carries it.
  const headerPage = (main = true) => {
    const region = `<header><h1>Ports Reopen After the Strike</h1>
<div class="author-card">Ana Ruiz, transport correspondent in Rotterdam</div>
<time datetime="2026-11-02">Nov. 2, 2026 Updated 4:05 p.m. ET</time></header>
<p>Dockworkers returned to the terminals on Monday morning. Cargo moved again within hours.</p>`;
    return `<html><body>${main ? `<article>${region}</article>` : `<div>${region}</div>`}</body></html>`;
  };

  it("keeps the header's byline element and its date line when the metadata block carries them", () => {
    const ref = articleReference(headerPage());
    expect(ref.headerBylines).toEqual(['Ana Ruiz, transport correspondent in Rotterdam']);
    expect(ref.headerDates).toEqual(['Nov. 2, 2026 Updated 4:05 p.m. ET']);
    const r = buildFidelityReport(headerPage(), markdown('Ana Ruiz').replace(/\n\nBy then[^\n]*/, ''));
    expect(r.coverage.bylineMatches).toBe(1);
    expect(r.coverage.dateMatches).toBeGreaterThan(0);
    expect(r.coverage.missingFromMarkdown).toEqual([]);
  });

  it('counts the header date line as missing without a Published line, and nothing applies without an article region', () => {
    const noDate = [
      '# Ports Reopen After the Strike',
      '',
      '- Author: Ana Ruiz',
      '',
      'Dockworkers returned to the terminals on Monday morning. Cargo moved again within hours.',
    ].join('\n');
    const r = buildFidelityReport(headerPage(), noDate);
    expect(r.coverage.dateMatches).toBe(0);
    expect(r.coverage.missingFromMarkdown).toEqual(['Nov. 2, 2026 Updated 4:05 p.m. ET']);
    const fallback = articleReference(headerPage(false));
    expect(fallback.fallback).toBe(true);
    expect(fallback.headerBylines).toEqual([]);
    expect(fallback.headerDates).toEqual([]);
  });

  it("reads only the region's first <header>, and only when it survives the boilerplate pass", () => {
    const page = `<html><body><article>
<header class="share-bar"><span class="byline">Ana Ruiz</span></header>
<header><div class="author-card">Ana Ruiz, transport correspondent in Rotterdam</div></header>
<p>Dockworkers returned to the terminals on Monday morning. Cargo moved again within hours.</p>
</article></body></html>`;
    const ref = articleReference(page);
    expect(ref.headerBylines).toEqual([]);
    expect(ref.text).toContain('Ana Ruiz, transport correspondent in Rotterdam');
  });
});

describe('comparison-logic rules (R14.10)', () => {
  it('does not split after abbreviations, before digits or lowercase, or after initials', () => {
    expect(splitSentences('He met Rep. Dana Ortiz in Fresno, Calif. Friday, Aug. 28, 2026. They talked.')).toEqual([
      'He met Rep. Dana Ortiz in Fresno, Calif. Friday, Aug. 28, 2026.',
      'They talked.',
    ]);
    expect(splitSentences('The U.S. officials left at 3 p.m. on Monday. J. Smith stayed.')).toEqual([
      'The U.S. officials left at 3 p.m. on Monday.',
      'J. Smith stayed.',
    ]);
  });

  it('splits after closing quotes and keeps a news photo caption whole (one alt text, not four fragments)', () => {
    expect(splitSentences('“We want a change.” Then they voted.')).toEqual(['“We want a change.”', 'Then they voted.']);
    expect(
      splitSentences('Ailyn shops in Delano, Calif. where the town struggles, Friday, Sept. 11, 2026. (Photo / For The Times)'),
    ).toEqual(['Ailyn shops in Delano, Calif. where the town struggles, Friday, Sept. 11, 2026.', '(Photo / For The Times)']);
  });

  it('compares tables row by row on both sides', () => {
    const html =
      '<article><table><tr><th>Region</th><th>Share of fleet sales</th></tr><tr><td>Nordic countries</td><td>41 percent</td></tr></table></article>';
    const md = '| Region | Share of fleet sales |\n| --- | --- |\n| Nordic countries | 41 percent |';
    const c = buildFidelityReport(html, md).coverage;
    expect(c.coverage).toBe(1);
    expect(c.leakage).toBe(0);
  });

  it('unescapes every escaped ASCII punctuation character before other rules', () => {
    expect(markdownToText('AT\\&T said 5 \\< 6 and \\[note\\] here')).toBe('AT&T said 5 < 6 and [note] here');
    expect(markdownToText('\\# not a heading')).toBe('# not a heading');
  });

  it('reduces links with bracketed text, <…> destinations, parentheses or titles to their text', () => {
    expect(markdownToText('A link to [the Foo (bar) page [1]](<https://en.example/wiki/Foo_(bar)>) is here.')).toBe(
      'A link to the Foo (bar) page [1] is here.',
    );
    expect(markdownToText('See [text](https://x.example/a_(b) "Title") now.')).toBe('See text now.');
    expect(markdownToText('[![alt text](https://x.example/i.png)](https://x.example/)')).toBe('alt text');
  });
});

describe('fidelity verdict (R14.12)', () => {
  it('scores 100 − leakage% − missing%, clamped to 0–100 and rounded', () => {
    expect(fidelityScore({ coverage: 1, leakage: 0 })).toBe(100);
    // Demo article of 2026-11-09: 13/15 kept, 2/15 leaked → 100 − 13.3 − 13.3 = 73.3.
    expect(fidelityScore({ coverage: 13 / 15, leakage: 2 / 15 })).toBe(73);
    expect(fidelityScore({ coverage: 0.2, leakage: 0.9 })).toBe(0); // clamped, never negative
    expect(fidelityScore({ coverage: 0.9, leakage: null })).toBe(90); // no Markdown sentence to leak
    expect(fidelityScore({ coverage: null, leakage: 0.1 })).toBeNull(); // no article sentence to keep
  });

  it('colours the score at the scores the coverage/leakage thresholds meet (95/5 → 90, 80/15 → 65)', () => {
    expect(scoreStatus(fidelityScore({ coverage: 0.95, leakage: 0.05 }))).toBe('good');
    expect(scoreStatus(89)).toBe('warning');
    expect(scoreStatus(fidelityScore({ coverage: 0.8, leakage: 0.15 }))).toBe('warning');
    expect(scoreStatus(64)).toBe('poor');
    expect(scoreStatus(null)).toBeNull();
  });

  it('carries the score, the title source and no title finding for a faithful run', () => {
    const r = buildFidelityReport(ARTICLE_HTML, GOOD_MARKDOWN);
    expect(r.score).toBe(100);
    expect(r.titleFinding).toBeNull();
    expect(r.titleSource).toBe('<h1>');
    expect(r.titleIsHeadline).toBe(true);
  });

  it('finds the schema title line by source position, front matter counted', () => {
    expect(schemaTitleLine('# Title\n\nBody.')).toBe(1);
    expect(schemaTitleLine('\n\n# Title\n\nBody.')).toBe(3);
    expect(schemaTitleLine('---\ntitle: t\n---\n# Title\n')).toBe(4);
    expect(schemaTitleLine('## Not a title\n\n# Later')).toBeNull();
    expect(schemaTitleLine('')).toBeNull();
  });
});
