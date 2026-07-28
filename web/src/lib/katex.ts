import katex from 'katex';

/**
 * OpenAlex titles and abstracts carry TeX inline, in several conventions:
 *   $x^2$, \(x^2\), \[x^2\], $$x^2$$.
 * We render those segments with KaTeX and escape everything else, then hand the
 * result to a single `dangerouslySetInnerHTML`. Escaping is done here, on the
 * plain-text segments, so no untrusted markup ever survives.
 */

type Segment = { kind: 'text' | 'inline' | 'display'; body: string };

const PATTERN = /\$\$([\s\S]+?)\$\$|\\\[([\s\S]+?)\\\]|\$([^$\n]+?)\$|\\\(([\s\S]+?)\\\)/g;

export function escapeHtml(input: string): string {
  return input
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

export function segmentMath(input: string): Segment[] {
  const segments: Segment[] = [];
  let lastIndex = 0;
  PATTERN.lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = PATTERN.exec(input)) !== null) {
    if (match.index > lastIndex) {
      segments.push({ kind: 'text', body: input.slice(lastIndex, match.index) });
    }
    const [, displayDollars, displayBrackets, inlineDollars, inlineParens] = match;
    if (displayDollars !== undefined) segments.push({ kind: 'display', body: displayDollars });
    else if (displayBrackets !== undefined) segments.push({ kind: 'display', body: displayBrackets });
    else if (inlineDollars !== undefined) segments.push({ kind: 'inline', body: inlineDollars });
    else if (inlineParens !== undefined) segments.push({ kind: 'inline', body: inlineParens });
    lastIndex = PATTERN.lastIndex;
  }

  if (lastIndex < input.length) {
    segments.push({ kind: 'text', body: input.slice(lastIndex) });
  }
  return segments;
}

export function renderMathToHtml(input: string): string {
  if (!input) return '';
  const segments = segmentMath(input);
  if (segments.length === 1 && segments[0].kind === 'text') {
    return escapeHtml(input);
  }
  return segments
    .map((segment) => {
      if (segment.kind === 'text') return escapeHtml(segment.body);
      try {
        return katex.renderToString(segment.body, {
          displayMode: segment.kind === 'display',
          throwOnError: false,
          strict: false,
          trust: false,
          output: 'html',
        });
      } catch {
        // Malformed TeX: show the source verbatim rather than dropping content.
        return escapeHtml(segment.kind === 'display' ? `$$${segment.body}$$` : `$${segment.body}$`);
      }
    })
    .join('');
}

/** True if the string appears to contain any TeX at all. */
export function containsMath(input: string): boolean {
  PATTERN.lastIndex = 0;
  return PATTERN.test(input);
}
