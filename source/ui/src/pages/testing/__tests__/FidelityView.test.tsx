/**
 * Component tests for FidelityView (R14, design §3c): every state, the threshold colours,
 * the side-by-side with its source toggle, the unmatched lists, and the structure flags.
 */
import React from 'react';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import FidelityView, { UNMATCHED_LIST_CAP } from '../FidelityView';

const HTML = `<html><body><nav><a href="/">Home</a></nav><article>
<h1>How Protected Lanes Reshaped a City</h1>
<p>Protected lanes change who feels safe enough to ride. The city added forty kilometres in two years.</p>
<h2>What changed</h2>
<p>Ridership among women <strong>doubled</strong>, according to the <a href="/r">annual count</a>.</p>
<p>Opponents argued that parking would vanish entirely from the district. It did not happen anywhere.</p>
</article><footer>Copyright</footer></body></html>`;

const FAITHFUL_MD = `# How Protected Lanes Reshaped a City

Protected lanes change who feels safe enough to ride. The city added forty kilometres in two years.

## What changed

Ridership among women **doubled**, according to the [annual count](https://x/r).

Opponents argued that parking would vanish entirely from the district. It did not happen anywhere.
`;

const LOSSY_MD = `# How Protected Lanes Reshaped a City \\| Daily

## How Protected Lanes Reshaped a City

|  |
| --- |
| [Voiced by Polly](https://x/polly) |

Protected lanes change who feels safe enough to ride.

Related stories you might also enjoy reading today.
`;

describe('FidelityView states', () => {
  it('asks for a run when there is no HTML yet', () => {
    render(<FidelityView html={null} markdown={null} markdownState="pending" />);
    expect(screen.getByText(/Run a URL to compare/)).toBeTruthy();
  });

  it('shows the article alone while the Markdown is pending, with no fabricated comparison', () => {
    render(<FidelityView html={HTML} markdown={null} markdownState="pending" />);
    expect(screen.getByText(/Markdown pending/)).toBeTruthy();
    expect(screen.getByText(/Protected lanes change who feels safe enough to ride/)).toBeTruthy();
    expect(screen.queryByTestId('fidelity-coverage')).toBeNull();
    expect(screen.queryByText('Copyright')).toBeNull(); // article region only, not the footer
  });

  it('says there is no Markdown to compare on a precondition failure', () => {
    render(<FidelityView html={HTML} markdown={null} markdownState="failed" />);
    expect(screen.getByText(/No Markdown to compare/)).toBeTruthy();
    expect(screen.queryByTestId('fidelity-coverage')).toBeNull();
  });
});

describe('FidelityView ready', () => {
  it('reports full coverage and no leakage for a faithful conversion, with no flags', () => {
    render(<FidelityView html={HTML} markdown={FAITHFUL_MD} markdownState="ready" />);
    expect(screen.getByTestId('fidelity-coverage').textContent).toBe('100.0%');
    expect(screen.getByTestId('fidelity-leakage').textContent).toBe('0.0%');
    expect(screen.getByText(/No structural artefacts flagged/)).toBeTruthy();
    expect(screen.getByText(/Missing from the Markdown \(0\)/)).toBeTruthy();
  });

  it('shows what was lost, what leaked, and the structure flags for a lossy conversion', () => {
    render(<FidelityView html={HTML} markdown={LOSSY_MD} markdownState="ready" />);
    const coverage = parseFloat(screen.getByTestId('fidelity-coverage').textContent ?? '');
    expect(coverage).toBeLessThan(100);
    // Lost sentences are listed by text (R14.3). The same sentence also appears in the article
    // column of the side-by-side (as it should), so scope the assertion to the list.
    fireEvent.click(screen.getByText(/Missing from the Markdown \(/));
    const missing = screen.getAllByText(/Opponents argued that parking would vanish/);
    expect(missing.length).toBeGreaterThanOrEqual(2); // once in the list, once in the article column
    // The lossy Markdown drops several sentences; the header carries a non-zero count.
    const header = screen.getByText(/Missing from the Markdown \(\d+\)/).textContent ?? '';
    expect(Number(/\((\d+)\)/.exec(header)?.[1])).toBeGreaterThanOrEqual(2);
    // Leaked residue is listed too, once, with its origin (R14.12).
    fireEvent.click(screen.getByText(/Not the article \(/));
    expect(screen.getAllByText('Voiced by Polly').length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByText(/Only in the Markdown/)).toBeNull();
    // Structure flags (R14.4): the single-cell widget table and the escaped heading.
    expect(screen.getByText(/single cell/)).toBeTruthy();
    expect(screen.getByText(/Escaped punctuation in a heading/)).toBeTruthy();
  });

  it('renders the Markdown beside the article text and toggles to source', () => {
    render(<FidelityView html={HTML} markdown={FAITHFUL_MD} markdownState="ready" />);
    const article = screen.getByTestId('fidelity-article');
    const md = screen.getByTestId('fidelity-markdown');
    expect(within(article).getByText(/The city added forty kilometres/)).toBeTruthy();
    // Rendered: the heading is an <h1>, not "# ..." text.
    expect(within(md).getByRole('heading', { level: 1, name: /How Protected Lanes/ })).toBeTruthy();
    expect(within(md).queryByText(/^# How Protected/)).toBeNull();
    fireEvent.click(screen.getByTestId('toggle-markdown-source'));
    expect(within(md).getByText(/# How Protected Lanes Reshaped a City/)).toBeTruthy();
  });

  it('never renders the raw HTML page (R14.1): nav and footer text do not appear', () => {
    render(<FidelityView html={HTML} markdown={FAITHFUL_MD} markdownState="ready" />);
    expect(screen.queryByText('Home')).toBeNull();
    expect(screen.queryByText('Copyright')).toBeNull();
  });

  it('does not render raw HTML embedded in the Markdown as markup', () => {
    render(<FidelityView html={HTML} markdown={'# T\n\n<img src=x onerror="alert(1)">\n\nBody text here is fine.'} markdownState="ready" />);
    const md = screen.getByTestId('fidelity-markdown');
    expect(md.querySelector('img')).toBeNull();
  });

  it('caps the unmatched lists and reports the remainder', () => {
    const many = Array.from({ length: UNMATCHED_LIST_CAP + 7 }, (_, i) => `<p>Unique article sentence number ${i} about lanes.</p>`).join('');
    render(<FidelityView html={`<html><body><article>${many}</article></body></html>`} markdown={'# Nothing matches here at all'} markdownState="ready" />);
    fireEvent.click(screen.getByText(/Missing from the Markdown \(/));
    expect(screen.getByText('+7 more')).toBeTruthy();
  });

  it('lists by-design additions and excluded boilerplate apart, and does not count them (R14.8, R14.9)', () => {
    const html = `<html><head><title>How Protected Lanes Reshaped a City | Daily</title></head><body><article>
<h1>How Protected Lanes Reshaped a City</h1>
<div class="ad-slot" data-ad-size="300x250">Advertisement medium rectangle placeholder here</div>
<p>Protected lanes change who feels safe enough to ride. The city added forty kilometres in two years.</p>
<figure><img src="/a.jpg" alt="A cyclist waits at a crossing"></figure>
<footer>Home · License terms here</footer>
</article></body></html>`;
    const md = `# How Protected Lanes Reshaped a City \\| Daily

Advertisement medium rectangle placeholder here

Protected lanes change who feels safe enough to ride. The city added forty kilometres in two years.

A cyclist waits at a crossing
`;
    render(<FidelityView html={html} markdown={md} markdownState="ready" />);
    expect(screen.getByTestId('fidelity-coverage').textContent).toBe('100.0%');
    // One leaked sentence (the ad the pipeline kept) of four counted: the title (matched to the
    // headline it contains) and two body sentences; the alt text is not counted.
    expect(screen.getByTestId('fidelity-leakage').textContent).toBe('25.0%');
    expect(screen.getByTestId('fidelity-verdict').textContent).toMatch(/1 listed items are page boilerplate/);
    fireEvent.click(screen.getByText(/Not the article \(1\)/));
    expect(screen.getByText(/page boilerplate the cleanup removes; the pipeline kept it/)).toBeTruthy();
    fireEvent.click(screen.getByText(/Added by the pipeline by design \(not counted\) \(1\)/));
    expect(screen.getByText(/Image alt text \(the pipeline keeps an image as its alt text\)/)).toBeTruthy();
    fireEvent.click(screen.getByText(/Excluded from the article reference \(boilerplate\) \(2\)/));
    expect(screen.getAllByText(/Home · License terms here/).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/The Markdown title is "How Protected Lanes Reshaped a City \| Daily" \(from the page's <title>\)/)).toBeTruthy();
  });

  it('lists the schema 1.1 parts as added by design and keeps the faithful score (R14.14)', () => {
    const md = `# How Protected Lanes Reshaped a City

- Author: Ana Ruiz
- Source: <https://x/lanes>

> Summary (generated): Protected lanes doubled ridership among women within two years.

${FAITHFUL_MD.split('\n').slice(2).join('\n')}
## Frequently asked questions (generated)

Generated automatically from the article above. Not part of the original article.

### How many kilometres did the city add?

The city added forty kilometres of protected lanes in two years.

Source: How Protected Lanes Reshaped a City by Ana Ruiz. <https://x/lanes>`;
    render(<FidelityView html={HTML} markdown={md} markdownState="ready" />);
    expect(screen.getByTestId('fidelity-coverage').textContent).toBe('100.0%');
    expect(screen.getByTestId('fidelity-leakage').textContent).toBe('0.0%');
    expect(screen.getByTestId('fidelity-score').textContent).toMatch(/100 \/ 100/);
    fireEvent.click(screen.getByText(/Added by the pipeline by design \(not counted\) \(6\)/));
    expect(screen.getAllByText(/Metadata block \(the pipeline lists the author/).length).toBe(2);
    expect(screen.getByText(/Generated summary, labelled as generated/)).toBeTruthy();
    expect(screen.getAllByText(/Generated FAQ, labelled as generated/).length).toBe(2);
    expect(screen.getByText(/Attribution line \(the pipeline closes with the source credit\)/)).toBeTruthy();
  });

  it('names a byline the metadata block carries in the coverage detail (R14.15)', () => {
    const html = `<html><body><article>
<h1>How Protected Lanes Reshaped a City</h1>
<p class="byline">By Ana Ruiz · November 2, 2026 · Transport</p>
<p>Protected lanes change who feels safe enough to ride. The city added forty kilometres in two years.</p>
</article></body></html>`;
    const md = `# How Protected Lanes Reshaped a City

- Author: Ana Ruiz
- Published: 2026-11-02

Protected lanes change who feels safe enough to ride. The city added forty kilometres in two years.`;
    render(<FidelityView html={html} markdown={md} markdownState="ready" />);
    expect(screen.getByTestId('fidelity-coverage').textContent).toBe('100.0%');
    expect(screen.getByTestId('fidelity-verdict').textContent).toMatch(/the byline carried by the metadata block/);
  });

  it('says when the Markdown is a reused catalogue record (R14.11)', () => {
    render(<FidelityView html={HTML} markdown={FAITHFUL_MD} markdownState="ready" reused />);
    expect(screen.getByTestId('fidelity-reused').textContent).toMatch(/earlier ingest, not from this fetch/);
  });

  it('shows no reused note for a fresh ingest', () => {
    render(<FidelityView html={HTML} markdown={FAITHFUL_MD} markdownState="ready" />);
    expect(screen.queryByTestId('fidelity-reused')).toBeNull();
  });

  it('explains the whole-page fallback when the HTML has no article region', () => {
    render(
      <FidelityView
        html={'<html><body><p>Only body text, four words or more here.</p></body></html>'}
        markdown={'Only body text, four words or more here.'}
        markdownState="ready"
      />,
    );
    expect(screen.getByText(/whole page text is the reference/)).toBeTruthy();
  });
});

describe('FidelityView verdict, buyer-facing first (R14.12)', () => {
  // Heat-pump page of the 2026-11-09 offline check: <title> H1, ad slot and promo kept, header dropped.
  const PAGE = `<html><head><title>Heat pumps win over landlords — Example Press</title></head><body><main><article>
<header><h1>Heat pumps win over landlords</h1><p class="dek">Rental owners are switching faster than homeowners, a new survey shows.</p></header>
<div class="ad-slot" data-ad-size="728x90">Advertisement leaderboard placeholder here</div>
<p>Landlords installed more heat pumps than any other group last year. Running costs fell by a fifth.</p>
</article></main></body></html>`;
  const MD = `# Heat pumps win over landlords — Example Press

Advertisement leaderboard placeholder here

Landlords installed more heat pumps than any other group last year. Running costs fell by a fifth.

Subscribe to our weekly energy letter today
`;

  it('opens with the score and the two buyer statements, before any list', () => {
    render(<FidelityView html={PAGE} markdown={MD} markdownState="ready" />);
    const verdict = screen.getByTestId('fidelity-verdict');
    // 4 article sentences (headline matched inside the title, dek missing, two body sentences);
    // 5 counted Markdown sentences, of which the ad and the newsletter line are not the article.
    expect(screen.getByTestId('fidelity-not-article-sentence').textContent).toBe(
      '2 of 5 sentences the agent receives are not the article',
    );
    expect(screen.getByTestId('fidelity-missing-sentence').textContent).toBe('1 article sentence is missing');
    // 100 − 40.0% − 25.0% = 35.
    expect(screen.getByTestId('fidelity-score').textContent).toBe('35 / 100');
    expect(within(verdict).getByText(/100 − 40\.0% not the article − 25\.0% missing/)).toBeTruthy();
    expect(within(verdict).getAllByText(/Measured\./).length).toBe(3);
    const text = document.body.textContent ?? '';
    expect(text.indexOf('Quality score')).toBeLessThan(text.indexOf('Not the article ('));
  });

  it('makes the title-not-headline flag a top-level finding, not a structure flag', () => {
    render(<FidelityView html={PAGE} markdown={MD} markdownState="ready" />);
    const finding = screen.getByTestId('fidelity-title-finding');
    expect(finding.textContent).toMatch(
      /The Markdown title is "Heat pumps win over landlords — Example Press" \(from the page's <title>\), not the article headline "Heat pumps win over landlords"\./,
    );
    // Stated once, in the verdict; the structure flags no longer repeat it.
    expect(screen.getAllByText(/The Markdown title is/)).toHaveLength(1);
  });

  it('shows one "Not the article" list with an origin label per item', () => {
    render(<FidelityView html={PAGE} markdown={MD} markdownState="ready" />);
    fireEvent.click(screen.getByText('Not the article (2)'));
    const ad = screen.getAllByText('Advertisement leaderboard placeholder here').find((el) => el.querySelector('[class*="awsui_badge"]'));
    expect(ad && within(ad).getByText('boilerplate')).toBeTruthy();
    const letter = screen.getAllByText('Subscribe to our weekly energy letter today').find((el) => el.querySelector('[class*="awsui_badge"]'));
    expect(letter && within(letter).getByText('unknown')).toBeTruthy();
    expect(screen.queryByText(/Only in the Markdown/)).toBeNull();
  });

  it('shows no title finding and a full score when the title is the headline', () => {
    render(<FidelityView html={HTML} markdown={FAITHFUL_MD} markdownState="ready" />);
    expect(screen.queryByTestId('fidelity-title-finding')).toBeNull();
    expect(screen.getByTestId('fidelity-score').textContent).toBe('100 / 100');
    expect(screen.getByTestId('fidelity-not-article-sentence').textContent).toBe('0 of 6 sentences the agent receives are not the article');
    expect(screen.getByTestId('fidelity-missing-sentence').textContent).toBe('0 article sentences are missing');
  });
});

describe('FidelityView rendered Markdown (R14.13)', () => {
  it('renders the Markdown inside Cloudscape TextContent, so headings and paragraphs take its styles', () => {
    render(<FidelityView html={HTML} markdown={FAITHFUL_MD} markdownState="ready" />);
    const rendered = screen.getByTestId('fidelity-rendered-markdown');
    expect(rendered.querySelector('[class*="awsui_text-content"] h1')).not.toBeNull();
    expect(rendered.querySelector('[class*="awsui_text-content"] h2')).not.toBeNull();
    expect(rendered.querySelector('[class*="awsui_text-content"] p')).not.toBeNull();
  });

  it('shows the title source next to the title H1, and that it is the headline', () => {
    render(<FidelityView html={HTML} markdown={FAITHFUL_MD} markdownState="ready" />);
    const flags = screen.getByTestId('markdown-title-source');
    expect(flags.textContent).toMatch(/Title from the page's <h1>/);
    expect(flags.textContent).toMatch(/The article headline/);
    // Next to the H1: same wrapper, and only once (the H2 carries no flag).
    expect(flags.parentElement?.querySelector('h1')?.textContent).toMatch(/How Protected Lanes Reshaped a City/);
    expect(screen.getAllByTestId('markdown-title-source')).toHaveLength(1);
  });

  it('warns next to the H1 when the title is the browser title, not the headline', () => {
    const html = HTML.replace('<html><body>', '<html><head><title>How Protected Lanes Reshaped a City | Daily</title></head><body>');
    render(<FidelityView html={html} markdown={LOSSY_MD} markdownState="ready" />);
    const flags = screen.getByTestId('markdown-title-source');
    expect(flags.textContent).toMatch(/Title from the page's <title>/);
    expect(flags.textContent).toMatch(/Not the article headline/);
  });

  it('flags a title found nowhere on the page', () => {
    render(<FidelityView html={HTML} markdown={'# A heading from nowhere at all\n\nProtected lanes change who feels safe enough to ride.'} markdownState="ready" />);
    expect(screen.getByTestId('markdown-title-source').textContent).toMatch(/Title not found on the page/);
  });
});
