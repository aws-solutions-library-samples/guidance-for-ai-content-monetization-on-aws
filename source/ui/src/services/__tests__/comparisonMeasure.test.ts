import { describe, it, expect } from 'vitest';

import {
  articleText,
  articleTextShare,
  byteLength,
  countTokens,
  measureMarkdown,
  removeBoilerplate,
  runCleanup,
  savingPercent,
  visibleText,
  wordCount,
} from '../comparisonMeasure';
import { articleHtml, fakeCount } from './comparisonFixtures';

describe('text helpers', () => {
  it('visibleText drops script/style/noscript/template/svg and collapses whitespace', () => {
    const html =
      '<body><p>Hello</p><script>evil()</script><style>p{}</style><noscript>no</noscript>' +
      '<template><p>tpl</p></template><svg><text>icon</text></svg><div>  World\n there </div></body>';
    expect(visibleText(html)).toBe('Hello World there');
  });

  it('joins adjacent blocks with a space', () => {
    expect(visibleText('<div>here</div><div>Two</div>')).toBe('here Two');
  });

  it('articleText takes the first article, then main, else null', () => {
    expect(articleText('<article>A one</article><article>B</article>')).toBe('A one');
    expect(articleText('<main>M text</main>')).toBe('M text');
    expect(articleText('<div>plain</div>')).toBeNull();
  });

  it('removeBoilerplate removes in place and returns the outermost removed elements (R14.8 reuses it)', () => {
    const doc = new DOMParser().parseFromString(
      '<article><p>Body stays.</p><aside><nav>inner</nav></aside><div class="promo">Promo</div><footer>F</footer></article>',
      'text/html',
    );
    const root = doc.querySelector('article') as Element;
    const removed = removeBoilerplate(root);
    expect(removed.map((el) => el.tagName)).toEqual(['ASIDE', 'FOOTER', 'DIV']);
    expect(root.textContent).toBe('Body stays.');
  });

  it('wordCount and byteLength', () => {
    expect(wordCount('  a  b\nc ')).toBe(3);
    expect(wordCount('')).toBe(0);
    expect(byteLength('é')).toBe(2);
  });

  it('savingPercent and articleTextShare handle zero and missing baselines', () => {
    expect(savingPercent(100, 25)).toBe(75);
    expect(savingPercent(100, 150)).toBe(-50);
    expect(savingPercent(0, 5)).toBeNull();
    expect(savingPercent(null, 5)).toBeNull();
    expect(articleTextShare(50, 200)).toBe(0.25);
    expect(articleTextShare(null, 200)).toBeNull();
    expect(articleTextShare(5, 0)).toBeNull();
  });
});

describe('countTokens', () => {
  it('uses an injected loader', async () => {
    expect(await countTokens('a b c', async () => (t: string) => t.split(' ').length)).toBe(3);
  });

  it('loads the real o200k_base tokenizer lazily', async () => {
    const n = await countTokens('Hello world, this is a test.');
    expect(n).toBeGreaterThan(3);
    expect(n).toBeLessThan(20);
  });
});

describe('runCleanup', () => {
  it('runs the five steps in order, each timed, with shrinking bytes', async () => {
    const result = await runCleanup(articleHtml, fakeCount);
    expect(result.steps.map((s) => s.id)).toEqual([
      'strip_scripts_styles',
      'extract_article',
      'remove_boilerplate',
      'normalize',
      'tokenize',
    ]);
    for (const s of result.steps) {
      expect(s.wall_clock_ms).toBeGreaterThanOrEqual(0);
      expect(s.bytes).toBeGreaterThan(0);
    }
    const bytes = result.steps.map((s) => s.bytes);
    expect(bytes[0]).toBeGreaterThan(bytes[1]);
    expect(bytes[3]).toBeLessThan(bytes[2]);
    expect(result.text).toBe('Headline One two three four five six seven eight nine ten.');
    expect(result.steps[4].tokens).toBe(11);
    expect(result.documentTokens).toBeGreaterThan(result.steps[4].tokens);
  });

  it('removes nav, aside, footer and class-name boilerplate inside the article', async () => {
    const html =
      '<body><main><p>Keep this</p><div class="cookie-banner">Accept cookies</div>' +
      '<section id="newsletter-signup">Subscribe</section><div role="navigation">Menu</div></main></body>';
    const result = await runCleanup(html, fakeCount);
    expect(result.text).toBe('Keep this');
    expect(result.articleWords).toBe(6);
  });

  it('marks extract_article skipped when there is no article or main', async () => {
    const result = await runCleanup('<body><p>Just text</p></body>', fakeCount);
    const extract = result.steps.find((s) => s.id === 'extract_article');
    expect(extract?.status).toBe('skipped');
    expect(extract?.reason).toMatch(/no <article> or <main>/);
    expect(result.articleWords).toBeNull();
  });

  it('reports the visible-text lower bound and words', async () => {
    const result = await runCleanup(articleHtml, fakeCount);
    expect(result.visibleWords).toBeGreaterThan(result.articleWords ?? 0);
    expect(result.visibleTextTokens).toBe(result.visibleWords);
  });

  it('never executes scripts in the fetched document', async () => {
    (globalThis as Record<string, unknown>).__pwned = false;
    await runCleanup('<body><script>globalThis.__pwned = true</script><article>x</article></body>', fakeCount);
    expect((globalThis as Record<string, unknown>).__pwned).toBe(false);
  });
});

describe('measureMarkdown', () => {
  it('returns bytes, words, tokens and the tokenizer time', async () => {
    const m = await measureMarkdown('# Title\n\nSome words here.\n', fakeCount);
    expect(m.words).toBe(5);
    expect(m.tokens).toBe(5);
    expect(m.bytes).toBe(byteLength('# Title\n\nSome words here.\n'));
    expect(m.tokenizeMs).toBeGreaterThanOrEqual(0);
  });
});
