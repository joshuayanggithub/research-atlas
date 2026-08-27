// Copies the current view as a link.
//
// The address bar already holds the facets (state/useUrlSync), so this is a convenience rather
// than the mechanism — which matters, because a reader can equally copy the URL themselves and
// a link built by hand still works.

import { Check, Link2 } from "lucide-react";
import { useEffect, useState } from "react";

export function ShareLinkButton() {
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) return;
    const id = window.setTimeout(() => setCopied(false), 1800);
    return () => window.clearTimeout(id);
  }, [copied]);

  const copy = async () => {
    const url = window.location.href;
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
    } catch {
      // Clipboard access needs a secure context and permission; over plain http, or when the
      // user denies it, fall back to a selection the reader can copy with the keyboard rather
      // than silently doing nothing.
      const field = document.createElement("textarea");
      field.value = url;
      field.style.position = "fixed";
      field.style.opacity = "0";
      document.body.appendChild(field);
      field.select();
      try {
        setCopied(document.execCommand("copy"));
      } finally {
        document.body.removeChild(field);
      }
    }
  };

  return (
    <button
      type="button"
      className="text-btn"
      onClick={() => void copy()}
      title="Copy a link to this view — filters, dates and the selected paper"
    >
      {copied ? <Check size={12} aria-hidden="true" /> : <Link2 size={12} aria-hidden="true" />}
      {copied ? "Copied" : "Copy link"}
    </button>
  );
}
