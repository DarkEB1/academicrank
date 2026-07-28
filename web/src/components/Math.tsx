import { useMemo } from 'react';
import { renderMathToHtml } from '@/lib/katex';
import { cn } from '@/lib/cn';

/**
 * Renders a string that may contain TeX. The HTML is produced by KaTeX from
 * escaped source (see lib/katex.ts) so no untrusted markup reaches the DOM.
 */
export function MathText({
  children,
  className,
  as: Tag = 'span',
}: {
  children: string | null | undefined;
  className?: string;
  as?: 'span' | 'p' | 'div' | 'h1' | 'h2' | 'h3';
}): JSX.Element {
  const html = useMemo(() => renderMathToHtml(children ?? ''), [children]);
  return <Tag className={className} dangerouslySetInnerHTML={{ __html: html }} />;
}

/** A paper title: serif, math-aware, and never silently blank. */
export function PaperTitle({
  title,
  className,
  as = 'span',
}: {
  title: string | null;
  className?: string;
  as?: 'span' | 'p' | 'div' | 'h1' | 'h2' | 'h3';
}): JSX.Element {
  if (!title) {
    const Tag = as;
    return (
      <Tag className={cn('font-serif italic text-ink-faint', className)}>
        Untitled record
      </Tag>
    );
  }
  return <MathText as={as} className={cn('font-serif', className)}>{title}</MathText>;
}
