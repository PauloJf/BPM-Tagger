import { useEffect } from "react";

/** Set the browser tab title (mirrors the per-page {% block title %} the Jinja
 *  templates used). Empty string → just "BPM Tagger". */
export function useTitle(title: string) {
  useEffect(() => {
    document.title = title ? `${title} — BPM Tagger` : "BPM Tagger";
  }, [title]);
}
