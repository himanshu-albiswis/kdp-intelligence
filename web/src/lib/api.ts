/* Thin typed client for the FastAPI backend at /api.
   In development Vite proxies /api to 127.0.0.1:8000; in production the
   same server serves this bundle, so the path is always relative. */

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type Any = any;

export interface Job {
  id: string;
  seed: string;
  kind?: "research" | "trends" | "discovery" | "teardown" | "crawl";
  status: "queued" | "running" | "done" | "partial" | "failed";
  pct: number;
  stage?: string;
  message?: string;
  marketplace: string;
  created_at?: string;
  demo?: boolean;
  result?: Any;
}

export async function api<T = Any>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch("/api" + path, init);
  if (!r.ok) {
    let detail = r.statusText;
    try {
      const body = await r.json();
      // FastAPI validation errors carry an array of {loc, msg} objects,
      // which once alerted as "[object Object]" in a live run
      detail = Array.isArray(body.detail)
        ? body.detail.map((d: Any) => `${(d.loc || []).join(".")}: ${d.msg}`).join("; ")
        : body.detail || r.statusText;
    } catch {
      /* non-JSON error body */
    }
    throw new Error(detail);
  }
  return r.json();
}

export const postJson = <T = Any>(path: string, body: unknown) =>
  api<T>(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

export const fmt = (n: unknown): string => (n == null ? "?" : Number(n).toLocaleString("en-US"));

export const isFinished = (status: Job["status"]) => ["done", "partial", "failed"].includes(status);

export const MKTS = ["us", "uk", "ca", "au", "de", "fr", "it", "es", "jp", "in", "nl", "se", "br", "mx"];

export const CATS: [string, string][] = [
  ["health", "Health"], ["self_help", "Self-help"], ["cooking", "Cooking"],
  ["finance", "Finance"], ["kids", "Kids & parenting"], ["career", "Career"],
];

export const MKTS_TRANSLATE: [string, string][] = [
  ["de", "German"], ["fr", "French"], ["it", "Italian"], ["es", "Spanish"], ["nl", "Dutch"],
  ["se", "Swedish"], ["jp", "Japanese"], ["br", "Portuguese"], ["mx", "Spanish (MX)"], ["uk", "UK (English)"],
];

export type Tab = "research" | "discover" | "trends" | "teardown" | "listing" | "categories" | "calc";
export const TABS: { id: Tab; label: string }[] = [
  { id: "research", label: "Niche Research" },
  { id: "discover", label: "Discovery" },
  { id: "trends", label: "Trend Radar" },
  { id: "teardown", label: "Teardown" },
  { id: "listing", label: "Listing" },
  { id: "categories", label: "Categories" },
  { id: "calc", label: "Royalty Calculator" },
];
