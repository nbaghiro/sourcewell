import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

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
  h1: ({ children }) => <p className="mb-1 mt-1 font-semibold text-foreground">{children}</p>,
  h2: ({ children }) => <p className="mb-1 mt-1 font-semibold text-foreground">{children}</p>,
  h3: ({ children }) => <p className="mb-1 mt-1 font-semibold text-foreground">{children}</p>,
};

/** Render an agent's markdown reply as styled inline UI (bold, lists, links, inline code). */
export function Markdown({ children }: { children: string }) {
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={COMPONENTS}>
      {children}
    </ReactMarkdown>
  );
}
