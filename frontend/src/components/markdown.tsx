import { type Components, Streamdown } from "streamdown";

// Style each element so the agent's markdown reads as native chat UI (no heavy typography plugin).
const COMPONENTS: Components = {
  p: ({ children }) => <p className="mb-2 leading-relaxed last:mb-0">{children}</p>,
  ul: ({ children }) => <ul className="mb-2 ml-4 list-disc space-y-1 last:mb-0">{children}</ul>,
  ol: ({ children }) => <ol className="mb-2 ml-4 list-decimal space-y-1 last:mb-0">{children}</ol>,
  li: ({ children }) => <li className="leading-relaxed">{children}</li>,
  strong: ({ children }) => <strong className="font-semibold text-foreground">{children}</strong>,
  em: ({ children }) => <em className="italic">{children}</em>,
  a: ({ href, children }) => (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="font-medium text-primary underline underline-offset-2"
    >
      {children}
    </a>
  ),
  code: ({ children }) => (
    <code className="rounded bg-secondary px-1 py-0.5 font-mono text-[0.85em]">{children}</code>
  ),
  h1: ({ children }) => <p className="mb-1 mt-2 font-semibold text-foreground first:mt-0">{children}</p>,
  h2: ({ children }) => <p className="mb-1 mt-2 font-semibold text-foreground first:mt-0">{children}</p>,
  h3: ({ children }) => <p className="mb-1 mt-2 font-semibold text-foreground first:mt-0">{children}</p>,
  hr: () => <hr className="my-3 border-border" />,
  table: ({ children }) => (
    <div className="my-2 overflow-x-auto rounded-md border border-border">
      <table className="w-full border-collapse text-left text-xs">{children}</table>
    </div>
  ),
  th: ({ children }) => (
    <th className="border-b border-border bg-secondary/50 px-2.5 py-1.5 font-semibold text-foreground">
      {children}
    </th>
  ),
  td: ({ children }) => (
    <td className="border-b border-border/40 px-2.5 py-1.5 align-top">{children}</td>
  ),
};

/**
 * Render an agent's markdown reply as styled inline UI (bold, lists, links, inline code).
 *
 * Uses `streamdown` (an AI-streaming-native markdown renderer) so half-streamed markdown — an
 * unclosed `**` mid-token while the response streams — renders cleanly instead of flashing raw
 * syntax. GFM is on by default; mermaid/code-highlighting are lazy-loaded only if they appear.
 */
export function Markdown({ children }: { children: string }) {
  return (
    <Streamdown components={COMPONENTS} parseIncompleteMarkdown>
      {children}
    </Streamdown>
  );
}
