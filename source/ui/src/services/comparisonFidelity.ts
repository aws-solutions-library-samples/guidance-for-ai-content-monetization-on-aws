/**
 * Content fidelity (spec scrape-vs-api-comparison R14, design §3c).
 *
 * Measures whether the licensed Markdown still contains the article: sentence-level
 * coverage (what the article kept) and leakage (residue in the Markdown), plus a
 * structure comparison that flags pipeline artefacts. Pure functions over text the
 * page already holds; no network, no LLM (R14.7). Every figure is Measured.
 *
 * 1-to-1 rules (R14.8–10): the reference is the article text after R5's cleanup steps 1–3, with
 * the article region chosen as the pipeline chooses it; what the pipeline adds by design (the
 * schema title, image alt text) is listed apart and counted in neither figure.
 */

import { removeBoilerplate } from './comparisonMeasure';

// ── HTML → block-aware text ──────────────────────────────────────────────────
//
// `comparisonMeasure.articleText` joins every text node with a space, which is right for
// word/token COUNTS but wrong for sentence MATCHING: a heading fuses with the paragraph after
// it into one "sentence" and inline tags leave ` ,` gaps. Fidelity needs block boundaries
// (block elements → line breaks, inline elements → nothing), so it has its own extractor.

const NON_VISIBLE = 'script, style, noscript, template, svg';
const BLOCK_TAGS = new Set([
  'ADDRESS', 'ARTICLE', 'ASIDE', 'BLOCKQUOTE', 'DD', 'DIV', 'DL', 'DT', 'FIELDSET', 'FIGCAPTION', 'FIGURE', 'FOOTER',
  'FORM', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'HEADER', 'HR', 'LI', 'MAIN', 'NAV', 'OL', 'P', 'PRE', 'SECTION',
  'TABLE', 'TBODY', 'TFOOT', 'THEAD', 'TR', 'UL', 'BR',
]);
/** Table cells are separated by a space, not a line: a Markdown table row is one line (R14.10). */
const CELL_TAGS = new Set(['TD', 'TH']);

function blockText(node: Node): string {
  if (node.nodeType === Node.TEXT_NODE) return node.nodeValue ?? '';
  if (node.nodeType !== Node.ELEMENT_NODE) return '';
  const el = node as Element;
  const inner = Array.from(el.childNodes).map(blockText).join('');
  if (CELL_TAGS.has(el.tagName)) return ` ${inner} `;
  return BLOCK_TAGS.has(el.tagName) ? `\n${inner}\n` : inner;
}

/** Visible text with one line per block element; inline tags leave no gaps. */
export function blockAwareText(root: Element | null): string {
  if (!root) return '';
  return blockText(root)
    .split('\n')
    .map((line) => line.replace(/\s+/g, ' ').replace(/\s+([,.;:!?])/g, '$1').trim())
    .filter((line) => line.length > 0)
    .join('\n');
}

/** Elements the pipeline's extractor deletes before it picks the article region. */
const PIPELINE_DROPPED = 'nav, header, footer, aside';

/**
 * The article region as the pipeline chooses it (R14.8): the first `<article>` not inside
 * nav/header/footer/aside, else the first such `<main>`, else null. A teaser `<article>` in a
 * menu would otherwise become the reference for a Markdown made from a different element.
 */
export function articleRegion(doc: Document): Element | null {
  const outside = (el: Element) => !el.parentElement?.closest(PIPELINE_DROPPED);
  return (
    Array.from(doc.querySelectorAll('article')).find(outside) ??
    Array.from(doc.querySelectorAll('main')).find(outside) ??
    null
  );
}

export type TitleSource = '<title>' | 'og:title' | '<h1>';

export interface ArticleReference {
  /** Block-aware text of the region after R5 steps 1–3: the reference side. */
  readonly text: string;
  /** No `<article>`/`<main>`: the whole body is the reference. */
  readonly fallback: boolean;
  /** Text of each element the boilerplate step removed from the region (shown, not compared). */
  readonly excluded: readonly string[];
  /** Alt text of the images left in the cleaned region (the pipeline keeps it as text). */
  readonly altTexts: readonly string[];
  /** Alt text of the images inside removed boilerplate. */
  readonly excludedAltTexts: readonly string[];
  /** `<h1>` texts left in the cleaned region (headline candidates). */
  readonly headlines: readonly string[];
  /** Byline elements of the region's own `<header>` (R14.15): the extractor drops them once it knows the author. */
  readonly headerBylines: readonly string[];
  /** `<time>` texts of the region's own `<header>` (R14.15): the extractor drops them once it knows the date. */
  readonly headerDates: readonly string[];
  /** Where the pipeline's schema title can come from: `<title>`, `og:title`, any `<h1>` on the page. */
  readonly titleSources: readonly { readonly source: TitleSource; readonly text: string }[];
}

function squash(text: string | null | undefined): string {
  return (text ?? '').replace(/\s+/g, ' ').trim();
}

function altTextsOf(root: Element): string[] {
  return Array.from(root.querySelectorAll('img[alt]'))
    .map((img) => squash(img.getAttribute('alt')))
    .filter((alt) => alt.length > 0);
}

/**
 * What the 1.1 extractor drops from the article's own `<header>` once the metadata block carries it
 * (R14.15; `extractor._remove_header_byline`): byline elements (`address`, a class or id token
 * `byline`, `author` or `dateline`, `rel="author"`, `itemprop="author"`) and `<time>` elements.
 * Outermost matches only; the text is block-aware, as the reference side is.
 */
function headerCarried(header: Element | null): { bylines: string[]; dates: string[] } {
  if (!header) return { bylines: [], dates: [] };
  const isByline = (el: Element): boolean => {
    if (el.tagName === 'ADDRESS') return true;
    if ((el.getAttribute('rel') ?? '').split(/\s+/).includes('author')) return true;
    if (el.getAttribute('itemprop') === 'author') return true;
    const tokens = `${el.getAttribute('class') ?? ''} ${el.id ?? ''}`.toLowerCase().split(/[\s_-]+/);
    return tokens.some((t) => t === 'byline' || t === 'author' || t === 'dateline');
  };
  const outermost = (els: Element[]) => els.filter((el) => !els.some((other) => other !== el && other.contains(el)));
  const text = (els: Element[]) => outermost(els).map((el) => blockAwareText(el)).filter((t) => t.length > 0);
  return {
    bylines: text(Array.from(header.querySelectorAll('*')).filter(isByline)),
    dates: text(Array.from(header.querySelectorAll('time'))),
  };
}

/** The reference side and what the comparison needs to classify the Markdown (R14.8, R14.9). */
export function articleReference(html: string): ArticleReference {
  const doc = new DOMParser().parseFromString(html, 'text/html');
  const titleSources: { source: TitleSource; text: string }[] = [];
  const pageTitle = squash(doc.querySelector('title')?.textContent);
  if (pageTitle) titleSources.push({ source: '<title>', text: pageTitle });
  const ogTitle = squash(doc.querySelector('meta[property="og:title"]')?.getAttribute('content'));
  if (ogTitle) titleSources.push({ source: 'og:title', text: ogTitle });
  doc.querySelectorAll('h1').forEach((h) => {
    const text = squash(h.textContent);
    if (text) titleSources.push({ source: '<h1>', text });
  });

  // R5 step 1, step 2 (pipeline's region choice), step 3 (the same boilerplate heuristic).
  doc.querySelectorAll(NON_VISIBLE).forEach((n) => n.remove());
  const region = articleRegion(doc);
  const root = region ?? doc.body;
  if (!root) {
    return {
      text: '',
      fallback: true,
      excluded: [],
      altTexts: [],
      excludedAltTexts: [],
      headlines: [],
      headerBylines: [],
      headerDates: [],
      titleSources,
    };
  }
  // The extractor picks the region's first <header> before its boilerplate pass and drops the
  // byline only if that header survived it, and only inside an <article>/<main> region.
  const firstHeader = region ? root.querySelector('header') : null;
  const removed = removeBoilerplate(root);
  const carried = headerCarried(firstHeader && root.contains(firstHeader) ? firstHeader : null);
  return {
    text: blockAwareText(root),
    fallback: region === null,
    excluded: removed.map((el) => blockAwareText(el)).filter((t) => t.length > 0),
    altTexts: altTextsOf(root),
    excludedAltTexts: removed.flatMap(altTextsOf),
    headlines: Array.from(root.querySelectorAll('h1')).map((h) => squash(h.textContent)).filter((t) => t.length > 0),
    headerBylines: carried.bylines,
    headerDates: carried.dates,
    titleSources,
  };
}

// ── Markdown → plain text ────────────────────────────────────────────────────

/** Convert Markdown source to the text a reader sees. Deterministic; no parser needed. */
/**
 * Inline link or image: text may hold one level of brackets (`[page [1]]`); the destination may be
 * `<…>` or hold one level of parentheses (`Foo_(bar)`); an optional title follows (R14.10).
 */
const LINK_OR_IMAGE =
  /!?\[((?:[^[\]]|\[[^[\]]*\])*)\]\((?:<[^>\n]*>|(?:[^()\s]|\([^()\s]*\))*)(?:\s+(?:"[^"]*"|'[^']*'|\([^)]*\)))?\s*\)/g;

export function markdownToText(markdown: string): string {
  let text = markdown.replace(/\r\n?/g, '\n');
  text = text.replace(/^---\n[\s\S]*?\n---\n/, ''); // front matter
  text = text.replace(/```[\s\S]*?```/g, (block) => block.replace(/```[^\n]*\n?/g, '')); // fences → body
  text = text.replace(/^\s{0,3}(\|?\s*:?-{3,}:?\s*\|?)+\s*$/gm, ''); // table rules
  // Escaped characters are literal text: protect every escaped ASCII punctuation character before
  // any syntax rule (an escaped `\[` must not open a link, `\#` must not make a heading), restore after.
  text = text.replace(/\\([!-/:-@[-`{-~])/g, (_m, c: string) => `\uE000${c.charCodeAt(0)}\uE001`);
  // Links → text, images → alt. Repeat for an image inside a link (`[![alt](img)](url)`).
  for (let i = 0; i < 3; i += 1) {
    const next = text.replace(LINK_OR_IMAGE, '$1');
    if (next === text) break;
    text = next;
  }
  text = text.replace(/^\s{0,3}#{1,6}\s+/gm, ''); // headings
  text = text.replace(/^\s{0,3}>\s?/gm, ''); // blockquotes
  text = text.replace(/^\s*([-*+]|\d+\.)\s+/gm, ''); // list markers
  text = text.replace(/\|/g, ' '); // table pipes
  text = text.replace(/(\*\*|__)(.*?)\1/g, '$2'); // bold
  text = text.replace(/(\*|_)(?=\S)(.*?)(?<=\S)\1/g, '$2'); // italics
  text = text.replace(/`([^`]*)`/g, '$1'); // code spans
  text = text.replace(/\uE000(\d+)\uE001/g, (_m, code: string) => String.fromCharCode(Number(code))); // restore escapes
  return text.replace(/[ \t]+/g, ' ').replace(/\n{3,}/g, '\n\n').trim();
}

// ── Sentences ────────────────────────────────────────────────────────────────

/** Sentences shorter than this are residue (nav crumbs, "Voiced by Polly", "Share this"): listed as leakage, not counted in denominators. */
const MIN_SENTENCE_WORDS = 4;

/** Lowercase, NFKC, no punctuation, single spaces: formatting differences do not count as loss. */
export function normaliseSentence(sentence: string): string {
  return sentence
    .normalize('NFKC')
    .toLowerCase()
    .replace(/[’‘]/g, "'")
    .replace(/[“”]/g, '"')
    .replace(/[^\p{L}\p{N}\s]/gu, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

/**
 * Words that end in a full stop without ending the sentence (R14.10): titles, AP month and state
 * abbreviations, and common Latin/business forms. Lowercase, without the stop. Words that are also
 * common English words ("ill", "mass", "wash") are left out on purpose.
 */
const ABBREVIATIONS = new Set([
  'mr', 'mrs', 'ms', 'dr', 'prof', 'rep', 'reps', 'sen', 'sens', 'gov', 'gen', 'lt', 'col', 'capt', 'sgt', 'cpl',
  'adm', 'maj', 'rev', 'hon', 'pres', 'supt', 'atty', 'st', 'mt', 'ft', 'jr', 'sr', 'ave', 'blvd', 'rd',
  'jan', 'feb', 'mar', 'apr', 'jun', 'jul', 'aug', 'sep', 'sept', 'oct', 'nov', 'dec',
  'ala', 'ariz', 'ark', 'calif', 'colo', 'conn', 'fla', 'ga', 'kan', 'kans', 'ky', 'md', 'mich', 'minn', 'mo',
  'mont', 'neb', 'nev', 'okla', 'ore', 'pa', 'tenn', 'tex', 'vt', 'va', 'wis', 'wyo',
  'no', 'nos', 'vol', 'fig', 'figs', 'pp', 'approx', 'est', 'dept', 'univ', 'inc', 'corp', 'co', 'ltd', 'bros',
  'vs', 'etc', 'al', 'cf',
]);

/** Terminal punctuation, optional closing quotes/brackets, then whitespace before more text. */
const SENTENCE_END = /[.!?]+["'”’)\]]*\s+(?=\S)/g;

function endsSentence(before: string, after: string): boolean {
  if (/^[\p{Ll}\p{N}]/u.test(after)) return false; // "Aug. 28", "U.S. officials", "e.g. the"
  const token = /(\S+)$/.exec(before)?.[1] ?? '';
  const bare = token.replace(/["'”’)\]]+$/u, '');
  if (!/[^.]\.$/.test(bare)) return true; // only a single full stop can belong to an abbreviation
  const word = bare.slice(0, -1).replace(/^[^\p{L}]+/u, '');
  if (ABBREVIATIONS.has(word.toLowerCase())) return false; // "Rep. David", "Calif. Friday"
  return !/^(?:\p{L}\.)*\p{L}$/u.test(word); // initials and initialisms: "J. Smith", "U.S. Rep."
}

/**
 * Split text into sentences: on `.!?` (optionally followed by closing quotes or brackets) followed
 * by whitespace, and on line breaks (headings and list items are sentences without terminal
 * punctuation). No split after an abbreviation or initialism, or before a lowercase letter or digit.
 */
export function splitSentences(text: string): string[] {
  const out: string[] = [];
  for (const line of text.split(/\n+/)) {
    let start = 0;
    for (const m of line.matchAll(SENTENCE_END)) {
      const at = m.index ?? 0;
      const end = at + m[0].length;
      if (endsSentence(line.slice(start, at + m[0].trimEnd().length), line.slice(end))) {
        out.push(line.slice(start, end));
        start = end;
      }
    }
    out.push(line.slice(start));
  }
  return out.map((s) => s.trim()).filter((s) => s.length > 0);
}

function wordsOf(normalised: string): string[] {
  return normalised ? normalised.split(' ') : [];
}

function jaccard(a: Set<string>, b: Set<string>): number {
  if (a.size === 0 && b.size === 0) return 1;
  let inter = 0;
  for (const w of a) if (b.has(w)) inter += 1;
  return inter / (a.size + b.size - inter);
}

export const FUZZY_THRESHOLD = 0.8;

export interface SentenceSide {
  /** Original sentences, in order. */
  readonly sentences: readonly string[];
  /** Those with at least MIN_SENTENCE_WORDS words (the denominators). */
  readonly substantive: number;
}

export interface FidelityCoverage {
  /** Share of substantive article sentences found in the Markdown (0–1), or null when none. */
  readonly coverage: number | null;
  /** Share of substantive Markdown sentences not from the article (0–1), or null when none. */
  readonly leakage: number | null;
  readonly articleSentences: number;
  readonly articleMatched: number;
  readonly markdownSentences: number;
  readonly markdownUnmatched: number;
  readonly exactMatches: number;
  readonly fuzzyMatches: number;
  /** Article headlines counted as kept because the Markdown title contains them (R14.9). */
  readonly titleMatches: number;
  /** Article bylines counted as kept because the schema 1.1 metadata block carries the author (R14.15). */
  readonly bylineMatches: number;
  /** Header date lines counted as kept because the schema 1.1 metadata block carries the date (R14.15). */
  readonly dateMatches: number;
  /** Article sentences with no counterpart in the Markdown (original text). */
  readonly missingFromMarkdown: readonly string[];
  /**
   * Markdown sentences with no counterpart in the article, including short residue: the one
   * "Not the article" list (R14.12), each item labelled with where it comes from.
   */
  readonly notArticle: readonly NotArticleItem[];
  /** What the pipeline adds by design (R14.9): listed with its source, counted in neither figure. */
  readonly addedByDesign: readonly AddedByDesign[];
}

/**
 * Where a "Not the article" item comes from (R14.12): `boilerplate` is page furniture R5's cleanup
 * removes from the article region (the pipeline kept it); `unknown` is found in neither the cleaned
 * article region nor the boilerplate removed from it.
 */
export type NotArticleOrigin = 'boilerplate' | 'unknown';

export interface NotArticleItem {
  readonly text: string;
  readonly origin: NotArticleOrigin;
}

export type AddedByDesign =
  | { readonly kind: 'document_title'; readonly text: string; readonly source: TitleSource }
  | { readonly kind: 'image_alt'; readonly text: string }
  /** Markdown schema 1.1 parts around the article (R14.14). */
  | { readonly kind: 'metadata' | 'generated_summary' | 'generated_faq' | 'attribution'; readonly text: string };

/** What the matcher needs beyond the two texts to tell by-design additions from leakage. */
export interface MatchContext {
  /** The Markdown's schema title (its first line, `# …`, as text); kept whole, not split. */
  readonly title?: string;
  readonly headlines?: readonly string[];
  readonly titleSources?: ArticleReference['titleSources'];
  readonly altTexts?: readonly string[];
  readonly excluded?: readonly string[];
  /** The schema 1.1 metadata block's `Author:` value (R14.15); a byline naming it counts as kept. */
  readonly author?: string;
  /** The metadata block has a `Published:` line (R14.15), as the extractor's date condition. */
  readonly hasDate?: boolean;
  /** Byline and `<time>` texts of the article's own `<header>` (R14.15, `ArticleReference`). */
  readonly headerBylines?: readonly string[];
  readonly headerDates?: readonly string[];
}

/** The author names in a metadata `Author:` value, normalised; the extractor joins several with ", ". */
function authorNames(author: string | undefined): string[][] {
  if (!author) return [];
  return author
    .split(/\s*(?:[,;&]|\band\b)\s*/i)
    .map((name) => wordsOf(normaliseSentence(name)))
    .filter((words) => words.length > 0);
}

function containsRun(words: readonly string[], run: readonly string[]): boolean {
  for (let i = 0; i + run.length <= words.length; i += 1) {
    if (run.every((w, k) => words[i + k] === w)) return true;
  }
  return false;
}

/** "By <first author> …", naming every author: a byline, not a sentence that merely starts with "By". */
function isBylineOf(normalised: string, authors: readonly (readonly string[])[]): boolean {
  const words = wordsOf(normalised);
  if (words[0] !== 'by') return false;
  const rest = words.slice(1);
  const [first] = authors;
  return first.every((w, k) => rest[k] === w) && authors.every((name) => containsRun(rest, name));
}

/** Whole-word containment on normalised text. */
function containedIn(needle: string, haystacks: readonly string[]): boolean {
  if (!needle) return false;
  const padded = ` ${needle} `;
  return haystacks.some((h) => ` ${h} `.includes(padded));
}

/** Title ⊇ headline on whole words, and the headline is substantive (or equal to the title). */
function titleContainsHeadline(titleNorm: string, headlineNorm: string): boolean {
  if (!titleNorm || !headlineNorm) return false;
  if (titleNorm === headlineNorm) return true;
  return wordsOf(headlineNorm).length >= MIN_SENTENCE_WORDS && containedIn(headlineNorm, [titleNorm]);
}

/**
 * Match article sentences to Markdown sentences: exact on normalised text first, then
 * token-set Jaccard ≥ FUZZY_THRESHOLD. Each sentence matches at most once. With a context, the
 * schema title and image alt text are then classified as by-design additions (R14.9).
 */
export function matchSentences(
  articleTextValue: string,
  markdownTextValue: string,
  context: MatchContext = {},
): FidelityCoverage {
  const art = splitSentences(articleTextValue).map((s) => ({ raw: s, norm: normaliseSentence(s) }));
  const mdSplit = splitSentences(markdownTextValue);
  const title = context.title?.trim();
  const md = (title ? [title, ...mdSplit] : mdSplit).map((s) => ({ raw: s, norm: normaliseSentence(s) }));
  const titleIndex = title ? 0 : -1;

  const artSub = art.filter((s) => wordsOf(s.norm).length >= MIN_SENTENCE_WORDS);

  const mdUsed = new Set<number>();
  const mdIndexByNorm = new Map<string, number[]>();
  md.forEach((s, i) => {
    if (!s.norm) return;
    const list = mdIndexByNorm.get(s.norm) ?? [];
    list.push(i);
    mdIndexByNorm.set(s.norm, list);
  });

  let exact = 0;
  let fuzzy = 0;
  const artMatched = new Set<number>();

  // Pass 1: exact.
  art.forEach((s, i) => {
    if (!s.norm) return;
    const candidates = mdIndexByNorm.get(s.norm) ?? [];
    const free = candidates.find((j) => !mdUsed.has(j));
    if (free !== undefined) {
      mdUsed.add(free);
      artMatched.add(i);
      exact += 1;
    }
  });

  // Pass 2: fuzzy, substantive sentences only (short ones would match spuriously).
  const mdSets = md.map((s) => new Set(wordsOf(s.norm)));
  art.forEach((s, i) => {
    if (artMatched.has(i)) return;
    const words = wordsOf(s.norm);
    if (words.length < MIN_SENTENCE_WORDS) return;
    const set = new Set(words);
    let best = -1;
    let bestScore = 0;
    md.forEach((_m, j) => {
      if (mdUsed.has(j) || mdSets[j].size < MIN_SENTENCE_WORDS) return;
      const score = jaccard(set, mdSets[j]);
      if (score > bestScore) {
        bestScore = score;
        best = j;
      }
    });
    if (best >= 0 && bestScore >= FUZZY_THRESHOLD) {
      mdUsed.add(best);
      artMatched.add(i);
      fuzzy += 1;
    }
  });

  // Pass 3 (R14.9): the schema title. It counts the headline as kept when it contains it
  // ("Headline — Site"); otherwise, taken from <title>/og:title/<h1>, it is a by-design addition.
  let titleMatches = 0;
  const byDesign = new Map<number, AddedByDesign>();
  if (titleIndex >= 0 && !mdUsed.has(titleIndex)) {
    const titleNorm = md[titleIndex].norm;
    const headlineNorms = new Set((context.headlines ?? []).map(normaliseSentence));
    const headline = art.findIndex(
      (s, i) => !artMatched.has(i) && headlineNorms.has(s.norm) && titleContainsHeadline(titleNorm, s.norm),
    );
    const source = (context.titleSources ?? []).find((t) => normaliseSentence(t.text) === titleNorm);
    if (headline >= 0) {
      mdUsed.add(titleIndex);
      artMatched.add(headline);
      titleMatches += 1;
    } else if (source && titleNorm) {
      byDesign.set(titleIndex, { kind: 'document_title', text: md[titleIndex].raw, source: source.source });
    }
  }

  // Pass 4 (R14.15): what schema 1.1 moved into the metadata block. A byline (the article header's
  // byline element, or a "By <author>" line) when the block names the author; the header's date
  // line when the block has a date.
  let bylineMatches = 0;
  let dateMatches = 0;
  const authors = authorNames(context.author);
  const bylineNorms = authors.length > 0 ? (context.headerBylines ?? []).map(normaliseSentence) : [];
  const dateNorms = context.hasDate ? (context.headerDates ?? []).map(normaliseSentence) : [];
  art.forEach((s, i) => {
    if (artMatched.has(i) || !s.norm) return;
    if (authors.length > 0 && (isBylineOf(s.norm, authors) || containedIn(s.norm, bylineNorms))) {
      artMatched.add(i);
      bylineMatches += 1;
    } else if (containedIn(s.norm, dateNorms)) {
      artMatched.add(i);
      dateMatches += 1;
    }
  });

  // Classify what is still unmatched: image alt text is by design; boilerplate the pipeline kept is leakage.
  const altNorms = (context.altTexts ?? []).map(normaliseSentence);
  const excludedNorms = (context.excluded ?? []).map(normaliseSentence);
  md.forEach((s, j) => {
    if (mdUsed.has(j) || byDesign.has(j) || j === titleIndex) return;
    if (containedIn(s.norm, altNorms)) byDesign.set(j, { kind: 'image_alt', text: s.raw });
  });

  const counted = md.filter((_s, j) => !byDesign.has(j));
  const mdSub = counted.filter((s) => wordsOf(s.norm).length >= MIN_SENTENCE_WORDS);
  const artSubMatched = artSub.filter((s) => artMatched.has(art.indexOf(s))).length;
  const mdSubUnmatched = mdSub.filter((s) => !mdUsed.has(md.indexOf(s))).length;
  const onlyInMarkdown = counted.filter((s) => s.norm && !mdUsed.has(md.indexOf(s)));

  return {
    coverage: artSub.length ? artSubMatched / artSub.length : null,
    leakage: mdSub.length ? mdSubUnmatched / mdSub.length : null,
    articleSentences: artSub.length,
    articleMatched: artSubMatched,
    markdownSentences: mdSub.length,
    markdownUnmatched: mdSubUnmatched,
    exactMatches: exact,
    fuzzyMatches: fuzzy,
    titleMatches,
    bylineMatches,
    dateMatches,
    missingFromMarkdown: artSub.filter((s) => !artMatched.has(art.indexOf(s))).map((s) => s.raw),
    notArticle: onlyInMarkdown.map((s) => ({
      text: s.raw,
      origin: containedIn(s.norm, excludedNorms) ? ('boilerplate' as const) : ('unknown' as const),
    })),
    addedByDesign: Array.from(byDesign.entries())
      .sort(([a], [b]) => a - b)
      .map(([, v]) => v),
  };
}

// ── Structure ────────────────────────────────────────────────────────────────

export interface StructureCounts {
  readonly headings: readonly number[]; // index 0 = h1 … index 5 = h6
  readonly links: number;
  readonly images: number;
  readonly tables: number;
  readonly emphasis: number;
  readonly code: number;
}

export type StructureFlag =
  | { readonly kind: 'duplicate_heading'; readonly text: string }
  | { readonly kind: 'single_cell_table'; readonly text: string }
  | { readonly kind: 'missing_structure'; readonly what: keyof Omit<StructureCounts, 'headings'> | 'headings' }
  | { readonly kind: 'escaped_punctuation_in_heading'; readonly text: string };

/**
 * R14.9 / R14.12: the Markdown title is not the article headline. A top-level finding of the
 * fidelity verdict, not a structure flag: it is the first thing an agent reads about the article.
 */
export interface TitleNotHeadline {
  readonly kind: 'title_not_headline';
  readonly title: string;
  readonly headline: string;
  readonly source: TitleSource | null;
}

export interface StructureComparison {
  readonly html: StructureCounts;
  readonly markdown: StructureCounts;
  readonly flags: readonly StructureFlag[];
}

function emptyCounts(): StructureCounts {
  return { headings: [0, 0, 0, 0, 0, 0], links: 0, images: 0, tables: 0, emphasis: 0, code: 0 };
}

/** Structure of the reference region (R14.8: the pipeline's region choice, boilerplate removed; falls back to the body). */
export function htmlStructure(html: string): StructureCounts {
  const doc = new DOMParser().parseFromString(html, 'text/html');
  doc.querySelectorAll(NON_VISIBLE).forEach((n) => n.remove());
  const root = articleRegion(doc) ?? doc.body;
  if (!root) return emptyCounts();
  removeBoilerplate(root);
  const headings = [1, 2, 3, 4, 5, 6].map((n) => root.querySelectorAll(`h${n}`).length);
  return {
    headings,
    links: root.querySelectorAll('a[href]').length,
    images: root.querySelectorAll('img').length,
    tables: root.querySelectorAll('table').length,
    emphasis: root.querySelectorAll('strong, b, em, i').length,
    code: root.querySelectorAll('code, pre').length,
  };
}

interface MarkdownStructure extends StructureCounts {
  readonly headingTexts: readonly string[];
  readonly tableCellCounts: readonly number[];
}

/** Structure of Markdown source by syntax. */
export function markdownStructure(markdown: string): MarkdownStructure {
  const src = markdown.replace(/\r\n?/g, '\n');
  const withoutFences = src.replace(/```[\s\S]*?```/g, (block) => {
    return `\uE000FENCE\uE001${'\n'.repeat(block.split('\n').length - 1)}`;
  });
  const lines = withoutFences.split('\n');
  const headings = [0, 0, 0, 0, 0, 0];
  const headingTexts: string[] = [];
  for (const line of lines) {
    const m = /^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$/.exec(line);
    if (m) {
      headings[m[1].length - 1] += 1;
      headingTexts.push(m[2]);
    }
  }
  // Tables: a block of consecutive pipe rows that includes a rule row.
  const tableCellCounts: number[] = [];
  let block: string[] = [];
  const flush = () => {
    if (block.length >= 2 && block.some((l) => /^\s*\|?\s*:?-{3,}/.test(l))) {
      const rows = block.filter((l) => !/^\s*\|?\s*:?-{3,}/.test(l));
      const cells = rows.reduce((sum, row) => {
        const trimmed = row.trim().replace(/^\|/, '').replace(/\|$/, '');
        return sum + trimmed.split('|').filter((c) => c.trim().length > 0).length;
      }, 0);
      tableCellCounts.push(cells);
    }
    block = [];
  };
  for (const line of lines) {
    if (/^\s*\|.*\|?\s*$/.test(line) && line.includes('|')) block.push(line);
    else flush();
  }
  flush();

  const images = (withoutFences.match(/!\[[^\]]*\]\([^)]*\)/g) ?? []).length;
  const links = (withoutFences.replace(/!\[[^\]]*\]\([^)]*\)/g, '').match(/\[[^\]]+\]\([^)]*\)/g) ?? []).length;
  const emphasis =
    (withoutFences.match(/(\*\*|__)(?=\S)(.+?)(?<=\S)\1/g) ?? []).length +
    (withoutFences.match(/(?<![*\w])(\*|_)(?=\S)([^*_\n]+?)(?<=\S)\1(?![*\w])/g) ?? []).length;
  const fences = (src.match(/```/g) ?? []).length / 2;
  const code = Math.floor(fences) + (withoutFences.match(/`[^`\n]+`/g) ?? []).length;

  return { headings, links, images, tables: tableCellCounts.length, emphasis, code, headingTexts, tableCellCounts };
}

/** Compare the two structures and flag pipeline artefacts (design §3c). */
export function compareStructure(html: string, markdown: string): StructureComparison {
  const h = htmlStructure(html);
  const m = markdownStructure(markdown);
  const flags: StructureFlag[] = [];

  const seen = new Map<string, string>();
  for (const text of m.headingTexts) {
    const key = normaliseSentence(markdownToText(text));
    if (!key) continue;
    if (seen.has(key)) flags.push({ kind: 'duplicate_heading', text: seen.get(key) ?? text });
    else seen.set(key, text);
    if (/\\[.|!#*_()[\]]/.test(text)) flags.push({ kind: 'escaped_punctuation_in_heading', text });
  }
  m.tableCellCounts.forEach((cells, i) => {
    if (cells <= 1) flags.push({ kind: 'single_cell_table', text: `table ${i + 1}` });
  });
  const hHeadings = h.headings.reduce((a, b) => a + b, 0);
  const mHeadings = m.headings.reduce((a, b) => a + b, 0);
  if (hHeadings > 0 && mHeadings === 0) flags.push({ kind: 'missing_structure', what: 'headings' });
  for (const what of ['links', 'images', 'tables', 'emphasis', 'code'] as const) {
    if (h[what] > 0 && m[what] === 0) flags.push({ kind: 'missing_structure', what });
  }

  const { headingTexts: _ht, tableCellCounts: _tc, ...markdownCounts } = m;
  void _ht;
  void _tc;
  return { html: h, markdown: markdownCounts, flags };
}

// ── Whole report ─────────────────────────────────────────────────────────────

export interface FidelityReport {
  /** The article text used as the reference side (R14.8). */
  readonly articleSide: string;
  /** Whether the reference fell back to the whole page (no <article>/<main>). */
  readonly articleFallback: boolean;
  /** Boilerplate R5's cleanup removed from the article region: shown, not compared as article. */
  readonly excludedFromArticle: readonly string[];
  /** The schema title (the Markdown's first-line H1, as text), or null when there is none. */
  readonly markdownTitle: string | null;
  /** Where the schema title comes from on the page (`<title>`, `og:title`, an `<h1>`), or null when it matches none. */
  readonly titleSource: TitleSource | null;
  /** The schema title is the article headline (R14.13: shown next to the rendered H1). */
  readonly titleIsHeadline: boolean;
  /** R14.12: top-level finding when the Markdown title is not the article headline. */
  readonly titleFinding: TitleNotHeadline | null;
  readonly markdownText: string;
  readonly coverage: FidelityCoverage;
  readonly structure: StructureComparison;
  /** R14.12: the derived quality score, 0–100, or null when the article has no substantive sentence. */
  readonly score: number | null;
}

/**
 * The fidelity quality score (R14.12): `100 − leakage% − missing%`, where missing% is the share
 * of article sentences not in the Markdown (`1 − coverage`), clamped to 0–100 and rounded. A
 * Markdown with no substantive sentence has no leakage to subtract. Measured, like its inputs.
 */
export function fidelityScore(coverage: Pick<FidelityCoverage, 'coverage' | 'leakage'>): number | null {
  if (coverage.coverage === null) return null;
  const missing = 1 - coverage.coverage;
  const leakage = coverage.leakage ?? 0;
  return Math.min(100, Math.max(0, Math.round(100 * (1 - leakage - missing))));
}

/** Score colouring: ≥90 good, ≥65 warning. These are the scores at the coverage/leakage thresholds (95/5, 80/15). */
export function scoreStatus(score: number | null): FidelityStatus | null {
  if (score === null) return null;
  return score >= 90 ? 'good' : score >= 65 ? 'warning' : 'poor';
}

/**
 * 1-based line of the schema title in the Markdown source as given (front matter counted), or null.
 * Lets the rendered view find the title's H1 by its source position (R14.13).
 */
export function schemaTitleLine(markdown: string): number | null {
  const src = markdown.replace(/\r\n?/g, '\n');
  const frontMatter = /^---\n[\s\S]*?\n---\n/.exec(src);
  const offset = frontMatter ? frontMatter[0].split('\n').length - 1 : 0;
  const lines = src.slice(frontMatter?.[0].length ?? 0).split('\n');
  const first = lines.findIndex((l) => l.trim().length > 0);
  if (first < 0 || !/^\s{0,3}#\s+\S/.test(lines[first])) return null;
  return offset + first + 1;
}

/**
 * Split off the schema title: schema 1.0 renders the document title as the first line, `# …`.
 * Returns the title as text and the rest of the Markdown source.
 */
export function splitSchemaTitle(markdown: string): { title: string | null; body: string } {
  const lines = markdown.replace(/\r\n?/g, '\n').replace(/^---\n[\s\S]*?\n---\n/, '').split('\n');
  const first = lines.findIndex((l) => l.trim().length > 0);
  const m = first >= 0 ? /^\s{0,3}#\s+(.*?)(?:\s+#+)?\s*$/.exec(lines[first]) : null;
  if (!m) return { title: null, body: markdown };
  const title = markdownToText(m[1]);
  return { title: title || null, body: lines.filter((_l, i) => i !== first).join('\n') };
}

// ── Markdown schema 1.1 parts (R14.14) ───────────────────────────────────────
// The fixed labels `markdown_renderer.compose_document` writes around the article.

const METADATA_LINE = /^- (?:Author|Publisher|Published|Updated|Source): \S/;
const SUMMARY_LINE = /^> Summary \(generated\):\s*/;
const FAQ_HEADING = '## Frequently asked questions (generated)';
const FAQ_NOTICE = 'Generated automatically from the article above. Not part of the original article.';
const ATTRIBUTION_LINE = /^Source: \S/;
const AUTOLINK = /<((?:https?|mailto):[^<>\s]+)>/gi;

export interface SchemaParts {
  /** The schema title as text, or null when the first line is not `# …`. */
  readonly title: string | null;
  /** The article body: the Markdown without the title line and the schema 1.1 parts. */
  readonly body: string;
  /** The title line and the body, without the schema 1.1 parts: what the structure comparison sees. */
  readonly article: string;
  /** The schema 1.1 parts, in document order (R14.14). */
  readonly additions: readonly AddedByDesign[];
  /** The metadata block's `Author:` value as text (R14.15), or null without one. */
  readonly author: string | null;
}

function partText(source: string): string {
  return markdownToText(source).replace(AUTOLINK, '$1');
}

/**
 * Split a Markdown document into its schema title, its article body and the schema 1.1 parts the
 * renderer adds around the article: the metadata block right after the title, the generated
 * summary after it, the generated FAQ section and the closing attribution line (written only with a
 * metadata block). Recognised by their fixed labels and positions only, so a publisher's own list
 * or FAQ stays article text; a schema 1.0 document comes back with no parts.
 */
export function splitSchemaParts(markdown: string): SchemaParts {
  const lines = markdown.replace(/\r\n?/g, '\n').replace(/^---\n[\s\S]*?\n---\n/, '').split('\n');
  const first = lines.findIndex((l) => l.trim().length > 0);
  const titleMatch = first >= 0 ? /^\s{0,3}#\s+(.*?)(?:\s+#+)?\s*$/.exec(lines[first]) : null;
  if (!titleMatch) return { title: null, body: markdown, article: markdown, additions: [], author: null };

  const nextFilled = (from: number): number => {
    let at = from;
    while (at < lines.length && lines[at].trim() === '') at += 1;
    return at;
  };
  const leading: AddedByDesign[] = [];
  let author: string | null = null;
  let at = nextFilled(first + 1);
  const metadataStart = at;
  while (at < lines.length && METADATA_LINE.test(lines[at])) {
    leading.push({ kind: 'metadata', text: partText(lines[at]) });
    const authorLine = /^- Author: (.+)$/.exec(lines[at]);
    if (authorLine) author = partText(authorLine[1]).trim() || null;
    at += 1;
  }
  const hasMetadata = at > metadataStart;
  let bodyStart = hasMetadata ? at : first + 1;
  const summaryAt = nextFilled(bodyStart);
  if (summaryAt < lines.length && SUMMARY_LINE.test(lines[summaryAt])) {
    let end = summaryAt + 1;
    while (end < lines.length && /^>/.test(lines[end])) end += 1;
    const summary = lines.slice(summaryAt, end).join('\n').replace(SUMMARY_LINE, '> ');
    leading.push({ kind: 'generated_summary', text: partText(summary) });
    bodyStart = end;
  }

  let end = lines.length;
  while (end > bodyStart && lines[end - 1].trim() === '') end -= 1;
  const trailing: AddedByDesign[] = [];
  let attribution: AddedByDesign | null = null;
  if (hasMetadata && end > bodyStart && ATTRIBUTION_LINE.test(lines[end - 1])) {
    attribution = { kind: 'attribution', text: partText(lines[end - 1]) };
    end -= 1;
  }
  let bodyEnd = end;
  for (let k = bodyStart; k < end; k += 1) {
    if (lines[k].trim() !== FAQ_HEADING) continue;
    bodyEnd = k;
    for (const line of lines.slice(k + 1, end)) {
      const text = line.trim();
      if (!text || text === FAQ_NOTICE) continue;
      trailing.push({ kind: 'generated_faq', text: partText(text.replace(/^#{1,6}\s+/, '')) });
    }
    break;
  }
  if (attribution) trailing.push(attribution);

  const body = lines.slice(bodyStart, bodyEnd).join('\n');
  return {
    title: markdownToText(titleMatch[1]) || null,
    body,
    article: `${lines[first]}\n\n${body}`,
    additions: [...leading, ...trailing],
    author,
  };
}

/** The page's headline: the first `<h1>`, preferring the article region's. */
function pageHeadline(ref: ArticleReference): string | undefined {
  return ref.headlines[0] ?? ref.titleSources.find((t) => t.source === '<h1>')?.text;
}

/** Where the schema title comes from: the first page source with the same normalised text. */
function sourceOfTitle(title: string | null, ref: ArticleReference): TitleSource | null {
  if (!title) return null;
  const titleNorm = normaliseSentence(title);
  return ref.titleSources.find((t) => normaliseSentence(t.text) === titleNorm)?.source ?? null;
}

/** R14.9: the Markdown title is not the page's headline. */
function titleFinding(title: string | null, ref: ArticleReference): TitleNotHeadline | null {
  if (!title) return null;
  const headline = pageHeadline(ref);
  if (!headline || normaliseSentence(headline) === normaliseSentence(title)) return null;
  return { kind: 'title_not_headline', title, headline, source: sourceOfTitle(title, ref) };
}

/** Build the fidelity report from the HTML the run fetched and the Markdown the pipeline published. */
export function buildFidelityReport(html: string, markdown: string): FidelityReport {
  const ref = articleReference(html);
  // Schema 1.1 parts around the article are by design (R14.14): out of both figures and the structure.
  const { title, body, article, additions, author } = splitSchemaParts(markdown);
  const matched = matchSentences(ref.text, markdownToText(body), {
    title: title ?? undefined,
    headlines: ref.headlines,
    titleSources: ref.titleSources,
    altTexts: ref.altTexts,
    excluded: [...ref.excluded, ...ref.excludedAltTexts],
    author: author ?? undefined,
    hasDate: additions.some((a) => a.kind === 'metadata' && a.text.startsWith('Published: ')),
    headerBylines: ref.headerBylines,
    headerDates: ref.headerDates,
  });
  // Document order: title, metadata and summary, then what the body holds, then the FAQ and attribution.
  const isLeadingPart = (a: AddedByDesign) => a.kind === 'metadata' || a.kind === 'generated_summary';
  const coverage: FidelityCoverage = additions.length
    ? {
        ...matched,
        addedByDesign: [
          ...matched.addedByDesign.filter((a) => a.kind === 'document_title'),
          ...additions.filter(isLeadingPart),
          ...matched.addedByDesign.filter((a) => a.kind !== 'document_title'),
          ...additions.filter((a) => !isLeadingPart(a)),
        ],
      }
    : matched;
  const headline = pageHeadline(ref);
  return {
    articleSide: ref.text,
    articleFallback: ref.fallback,
    excludedFromArticle: ref.excluded,
    markdownTitle: title,
    titleSource: sourceOfTitle(title, ref),
    titleIsHeadline: Boolean(title && headline && normaliseSentence(headline) === normaliseSentence(title)),
    titleFinding: titleFinding(title, ref),
    markdownText: markdownToText(markdown),
    coverage,
    structure: compareStructure(html, article),
    score: fidelityScore(coverage),
  };
}

export type FidelityStatus = 'good' | 'warning' | 'poor';

/** Threshold colouring (design §3c): coverage ≥95% good, ≥80% warning; leakage ≤5% good, ≤15% warning. */
export function coverageStatus(coverage: number | null): FidelityStatus | null {
  if (coverage === null) return null;
  return coverage >= 0.95 ? 'good' : coverage >= 0.8 ? 'warning' : 'poor';
}

export function leakageStatus(leakage: number | null): FidelityStatus | null {
  if (leakage === null) return null;
  return leakage <= 0.05 ? 'good' : leakage <= 0.15 ? 'warning' : 'poor';
}
