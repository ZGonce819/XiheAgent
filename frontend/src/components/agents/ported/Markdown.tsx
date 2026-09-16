import type { ReactNode } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { CodeBlock } from './CodeBlock';
import { MarkdownTable } from './MarkdownTable';

interface MarkdownProps {
  content: string;
}

function extractText(children: ReactNode): string {
  return String(children);
}

export function Markdown({ content }: MarkdownProps) {
  return (
    <div className="min-w-0 max-w-full text-[0.8125rem] leading-6 break-words [overflow-wrap:anywhere]">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          h1: ({ children }) => <div className="mt-4 mb-2 text-[1.25rem] font-semibold text-[color:var(--ui-text)]">{children}</div>,
          h2: ({ children }) => <div className="mt-3 mb-2 text-[1.0625rem] font-semibold text-[color:var(--ui-text)]">{children}</div>,
          h3: ({ children }) => <div className="text-[color:var(--ui-accent)] text-[0.9375rem] font-semibold mt-3 mb-1">{children}</div>,
          p: ({ children }) => <p className="my-1.5 text-[color:var(--ui-text)] break-words [overflow-wrap:anywhere]">{children}</p>,
          a: ({ href, children }) => (
            <a className="text-[color:var(--ui-accent)] underline decoration-[color:var(--ui-accent)]/50 break-words [overflow-wrap:anywhere]" href={href} target="_blank" rel="noreferrer">
              {children}
            </a>
          ),
          ul: ({ children }) => <div className="my-1.5 ml-4 min-w-0">{children}</div>,
          ol: ({ children }) => <div className="my-1.5 ml-4 min-w-0">{children}</div>,
          li: ({ children }) => <div className="text-[color:var(--ui-text)] break-words [overflow-wrap:anywhere] [&>p]:inline [&>p]:my-0">- {children}</div>,
          blockquote: ({ children }) => (
            <div className="my-2 border-l-2 border-[var(--ui-border)] pl-3 text-[color:var(--ui-text-muted)] break-words [overflow-wrap:anywhere]">{children}</div>
          ),
          hr: () => <hr className="border-[var(--ui-border-subtle)] my-3" />,
          table: ({ children }) => <MarkdownTable>{children}</MarkdownTable>,
          thead: ({ children }) => <thead className="border-b border-[var(--ui-border)] bg-[var(--ui-panel-2)]">{children}</thead>,
          tbody: ({ children }) => <tbody className="divide-y divide-[var(--ui-border-subtle)]">{children}</tbody>,
          tr: ({ children }) => <tr className="hover:bg-[var(--ui-panel-2)]/50 transition-colors">{children}</tr>,
          th: ({ children }) => <th className="border-0 text-left text-[0.6875rem] font-medium text-[color:var(--ui-text)] whitespace-nowrap align-middle first:font-semibold first:text-[color:var(--ui-accent)]" style={{ padding: '4px 8px' }}>{children}</th>,
          td: ({ children }) => <td className="border-0 text-[0.6875rem] text-[color:var(--ui-text-muted)] align-middle first:font-medium first:text-[color:var(--ui-text)]" style={{ padding: '4px 8px' }}>{children}</td>,
          code: ({ className, children }) => {
            const text = extractText(children);
            const match = /language-([^\s]+)/.exec(className || '');
            const isBlock = match || text.includes('\n');
            if (isBlock) {
              return <CodeBlock text={text.replace(/\n$/, '')} language={match?.[1]} />;
            }
            return <code className="rounded-md bg-[var(--ui-panel-2)] px-1.5 py-0.5 font-mono text-[0.85em] text-[color:var(--ui-accent)] whitespace-pre-wrap break-words [overflow-wrap:anywhere]">{text}</code>;
          },
          pre: ({ children }) => <>{children}</>,
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
