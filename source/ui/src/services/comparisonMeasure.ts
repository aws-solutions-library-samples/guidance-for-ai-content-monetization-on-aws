/**
 * Browser-side measurement for the scrape-vs-licensed-Markdown comparison
 * (spec `scrape-vs-api-comparison`, design §3b, R5).
 *
 * Pure helpers over a document string: no network, no React. Documents are
 * parsed with `DOMParser` only, which never executes scripts, and fetched HTML
 * is never rendered with `innerHTML`.
 *
 * Boilerplate removal is heuristic and says so: its time and bytes are
 * measured, "what a scraper would remove" is an assumption.
 */

/** Tokenizer used for every estimate; shown next to each token figure. */
export const TOKENIZER_NAME = 'o200k_base';
/** Pinned in package.json; keep in step with the dependency. */
export const TOKENIZER_VERSION = '4.1.0';
export const TOKENIZER_LABEL = `${TOKENIZER_NAME} (gpt-tokenizer ${TOKENIZER_VERSION})`;

/** Elements whose text a reader never sees as article copy. */
const NON_VISIBLE_SELECTOR = 'script, style, noscript, template, svg';
const STRIP_SELECTOR = 'script, style, noscript, template';
const BOILERPLATE_SELECTOR =
  'nav, aside, footer, [role="navigation"], [role="complementary"], [data-ad-size], [aria-hidden="true"]';
/** Class/id heuristics for consent, comments, newsletter and similar roots. */
const BOILERPLATE_NAME =
  /(^|[\s_-])(cookie|cookies|consent|gdpr|comment|comments|newsletter|subscribe|subscription|advert|advertisement|ad-slot|sidebar|popup|modal|share|social|related|promo)([\s_-]|$)/i;

const encoder = new TextEncoder();

/** UTF-8 size of a string. */
export function byteLength(text: string): number {
  return encoder.encode(text).length;
}

/** Words as whitespace-separated runs, the same rule for both sides. */
export function wordCount(text: string): number {
  const matches = text.match(/\S+/g);
  return matches ? matches.length : 0;
}

function parse(html: string): Document {
  return new DOMParser().parseFromString(html, 'text/html');
}

/** Text of a node tree, text nodes joined by a space so adjacent blocks do not run together. */
function textOf(root: Node | null): string {
  if (!root) return '';
  const owner = root.ownerDocument ?? (root as Document);
  const parts: string[] = [];
  const walker = owner.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    if (node.nodeValue) parts.push(node.nodeValue);
  }
  return parts.join(' ').replace(/\s+/g, ' ').trim();
}

/** The text a human reader sees on the page: scripts, styles and embeds removed. */
export function visibleText(html: string): string {
  const doc = parse(html);
  doc.querySelectorAll(NON_VISIBLE_SELECTOR).forEach((node) => node.remove());
  return textOf(doc.body);
}

/** Visible text of the first `<article>` (fallback `<main>`), or null when neither exists. */
export function articleText(html: string): string | null {
  const doc = parse(html);
  doc.querySelectorAll(NON_VISIBLE_SELECTOR).forEach((node) => node.remove());
  const root = doc.querySelector('article') ?? doc.querySelector('main');
  return root ? textOf(root) : null;
}

type TokenCounter = (text: string) => number;
let counterPromise: Promise<TokenCounter> | null = null;

async function defaultLoader(): Promise<TokenCounter> {
  const mod = await import('gpt-tokenizer/encoding/o200k_base');
  return mod.countTokens;
}

/**
 * Lazily load the tokenizer (separate chunk, not in the main bundle) and count
 * tokens. `loader` is injectable for tests.
 */
export async function countTokens(
  text: string,
  loader: () => Promise<TokenCounter> = defaultLoader
): Promise<number> {
  if (loader !== defaultLoader) {
    return (await loader())(text);
  }
  counterPromise ??= defaultLoader();
  return (await counterPromise)(text);
}

export type CleanupStepId =
  | 'strip_scripts_styles'
  | 'extract_article'
  | 'remove_boilerplate'
  | 'normalize'
  | 'tokenize';

export interface CleanupStep {
  readonly id: CleanupStepId;
  readonly status: 'ok' | 'skipped';
  readonly reason: string | null;
  /** Time of the step itself; token counting between steps is not included. */
  readonly wall_clock_ms: number;
  /** Bytes remaining after the step. */
  readonly bytes: number;
  /** Tokens remaining after the step. */
  readonly tokens: number;
}

export interface CleanupResult {
  readonly steps: readonly CleanupStep[];
  /** Plain text left after normalisation: what is handed to a model. */
  readonly text: string;
  /** Tokens of the raw document, before any cleanup (R3.4). */
  readonly documentTokens: number;
  readonly visibleTextTokens: number;
  readonly visibleWords: number;
  /** Null when the document has neither `<article>` nor `<main>`. */
  readonly articleWords: number | null;
  readonly totalCleanupMs: number;
}

export type AsyncCounter = (text: string) => Promise<number>;

function looksLikeBoilerplate(el: Element): boolean {
  return BOILERPLATE_NAME.test(`${el.getAttribute('class') ?? ''} ${el.getAttribute('id') ?? ''}`);
}

/**
 * R5 step 3, in place: remove boilerplate under `root`. Returns the removed elements (outermost
 * only), so the fidelity view (R14.8) can show what the reference excludes.
 */
export function removeBoilerplate(root: Element): Element[] {
  const removed: Element[] = [];
  root.querySelectorAll(BOILERPLATE_SELECTOR).forEach((el) => {
    if (!root.contains(el)) return; // inside an element already removed
    removed.push(el);
    el.remove();
  });
  // Re-query after the first pass: removing a parent invalidates its children.
  root.querySelectorAll('[class], [id]').forEach((el) => {
    if (root.contains(el) && el !== root && looksLikeBoilerplate(el)) {
      removed.push(el);
      el.remove();
    }
  });
  return removed;
}

/**
 * Run the cleanup a scraper performs, in order (R5): strip scripts/styles/
 * templates, extract `<article>` else `<main>`, remove boilerplate, normalise
 * whitespace, tokenize. Each step is timed with `performance.now()`.
 */
export async function runCleanup(
  html: string,
  count: AsyncCounter = (t) => countTokens(t)
): Promise<CleanupResult> {
  const documentTokens = await count(html);
  const steps: CleanupStep[] = [];

  // 1. strip scripts, styles, templates (parse time belongs to this step)
  let t0 = performance.now();
  const doc = parse(html);
  doc.querySelectorAll(STRIP_SELECTOR).forEach((node) => node.remove());
  let ms = performance.now() - t0;
  let serialized = doc.documentElement.outerHTML;
  steps.push({
    id: 'strip_scripts_styles',
    status: 'ok',
    reason: null,
    wall_clock_ms: ms,
    bytes: byteLength(serialized),
    tokens: await count(serialized),
  });

  // 2. extract the article, else main, else keep the body
  t0 = performance.now();
  const holder = doc.querySelector('article') ?? doc.querySelector('main');
  let root: Element = doc.body ?? doc.documentElement;
  let extractReason: string | null = null;
  if (holder) {
    root = holder;
  } else {
    extractReason = 'no <article> or <main> found; the whole body is kept';
  }
  ms = performance.now() - t0;
  serialized = root.outerHTML;
  steps.push({
    id: 'extract_article',
    status: holder ? 'ok' : 'skipped',
    reason: extractReason,
    wall_clock_ms: ms,
    bytes: byteLength(serialized),
    tokens: await count(serialized),
  });

  // 3. remove boilerplate (heuristic)
  t0 = performance.now();
  removeBoilerplate(root);
  ms = performance.now() - t0;
  serialized = root.outerHTML;
  steps.push({
    id: 'remove_boilerplate',
    status: 'ok',
    reason: 'heuristic: tag, role and class-name rules; what a scraper removes is an assumption',
    wall_clock_ms: ms,
    bytes: byteLength(serialized),
    tokens: await count(serialized),
  });

  // 4. normalise whitespace into plain text
  t0 = performance.now();
  const text = textOf(root);
  ms = performance.now() - t0;
  steps.push({
    id: 'normalize',
    status: 'ok',
    reason: null,
    wall_clock_ms: ms,
    bytes: byteLength(text),
    tokens: await count(text),
  });

  // 5. tokenize the cleaned text (the timed work is the tokenizer call itself)
  t0 = performance.now();
  const finalTokens = await count(text);
  ms = performance.now() - t0;
  steps.push({
    id: 'tokenize',
    status: 'ok',
    reason: null,
    wall_clock_ms: ms,
    bytes: byteLength(text),
    tokens: finalTokens,
  });

  const visible = visibleText(html);
  const article = articleText(html);
  return {
    steps,
    text,
    documentTokens,
    visibleTextTokens: await count(visible),
    visibleWords: wordCount(visible),
    articleWords: article === null ? null : wordCount(article),
    totalCleanupMs: steps.reduce((sum, s) => sum + s.wall_clock_ms, 0),
  };
}

/** Percentage saved by the Markdown versus the HTML figure, or null when undefined. */
export function savingPercent(
  html: number | null | undefined,
  markdown: number | null | undefined
): number | null {
  if (html == null || markdown == null || html <= 0) return null;
  return Math.round((1 - markdown / html) * 100);
}

/** Share of the scraped document that is article text, in [0, 1], or null (R5.3). */
export function articleTextShare(articleWords: number | null, visibleWords: number | null): number | null {
  if (articleWords == null || visibleWords == null || visibleWords <= 0) return null;
  return Math.min(1, articleWords / visibleWords);
}

export interface MarkdownMeasurement {
  readonly bytes: number;
  readonly words: number;
  readonly tokens: number;
  /** Time of the tokenizer call, measured in this browser. */
  readonly tokenizeMs: number;
}

/** Path B tokenize step: the Markdown as published, no cleanup. */
export async function measureMarkdown(
  markdown: string,
  count: AsyncCounter = (t) => countTokens(t)
): Promise<MarkdownMeasurement> {
  const t0 = performance.now();
  const tokens = await count(markdown);
  const tokenizeMs = performance.now() - t0;
  return { bytes: byteLength(markdown), words: wordCount(markdown), tokens, tokenizeMs };
}
