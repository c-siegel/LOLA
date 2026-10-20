import type { AnchorHTMLAttributes } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import { Link } from "react-router-dom";
import remarkGfm from "remark-gfm";
import styles from "./MarkdownContent.module.css";

// In-app links ("/impressum") go through the router: a plain <a href> would reload the whole SPA
// and skip the router's basename on a sub-path deployment. External links open in a new tab, so
// a visitor doesn't lose the page they came from.
function MarkdownLink({ href = "", children, node: _node, ...props }: AnchorHTMLAttributes<HTMLAnchorElement> & { node?: unknown }) {
  if (href.startsWith("/") && !href.startsWith("//")) {
    return (
      <Link to={href} {...props}>
        {children}
      </Link>
    );
  }
  const external = /^https?:\/\//i.test(href);
  return (
    <a href={href} {...props} {...(external ? { target: "_blank", rel: "noopener noreferrer" } : {})}>
      {children}
    </a>
  );
}

const COMPONENTS: Components = { a: MarkdownLink };

/** Admin-written Markdown (GitHub flavour) for the public pages. Raw HTML in it is not rendered —
 * react-markdown escapes it, and its default URL filter drops javascript: links — so an admin's
 * text can't run script on a page every visitor opens. */
export function MarkdownContent({ children, className }: { children: string; className?: string }) {
  return (
    <div className={[styles.markdown, className].filter(Boolean).join(" ")}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={COMPONENTS}>
        {children}
      </ReactMarkdown>
    </div>
  );
}
