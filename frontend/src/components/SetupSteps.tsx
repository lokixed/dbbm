import type { ReactNode } from "react";

/** A link to an external page (Google Cloud Console, docs), opened in a new tab. */
export function ExtLink({ href, children }: { href: string; children: ReactNode }) {
  return (
    <a className="text-app-text underline" href={href} target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  );
}

/** One numbered step of a setup guide: a bold title, then its paragraphs. */
export function SetupStep({ title, children }: { title: ReactNode; children: ReactNode }) {
  return (
    <li className="space-y-1">
      <div className="font-medium text-app-text">{title}</div>
      <div className="space-y-1">{children}</div>
    </li>
  );
}

/** "If it does not work": a short list of known errors and their fixes. */
export function Troubleshooting({ children }: { children: ReactNode }) {
  return (
    <div className="space-y-1">
      <div className="font-medium text-app-text">If it does not work</div>
      <ul className="list-disc space-y-1 pl-5">{children}</ul>
    </div>
  );
}
