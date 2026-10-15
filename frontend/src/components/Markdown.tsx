import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

// Raw HTML in model output is never rendered (react-markdown escapes it by default).
export default function Markdown({ children }: { children: string }) {
  return (
    <div className="prose-ii">
      <ReactMarkdown remarkPlugins={[remarkGfm]}
        components={{ a: ({ href, children: c }) => <a href={href} target={href?.startsWith("/") ? undefined : "_blank"} rel="noopener noreferrer">{c}</a> }}>
        {children}
      </ReactMarkdown>
    </div>
  );
}
