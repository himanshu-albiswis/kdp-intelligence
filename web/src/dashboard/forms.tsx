import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { api, CATS, MKTS, MKTS_TRANSLATE, postJson, type Any, type Job } from "@/lib/api";
import { Area, Field, Lbl, Row2, Select, Small } from "./primitives";
import { CategoriesView, CalcView, ListingCheckView, TranslateView } from "./views";

/* Every form posts to the API and hands back either a job id (long-running
   work rendered by polling) or a finished node for the main panel. Errors
   surface inline instead of in alert(). */

type JobHandler = (jobId: string) => void;
type ShowHandler = (node: ReactNode) => void;

function useSubmit() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const run = async (fn: () => Promise<void>) => {
    setBusy(true); setError(null);
    try { await fn(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  return { busy, error, run };
}

function ErrorLine({ error }: { error: string | null }) {
  return error ? <div className="warnbox mt-3">⚠️ {error}</div> : null;
}

function Check({ id, checked, onChange, children, hint }: { id: string; checked: boolean; onChange: (v: boolean) => void; children: ReactNode; hint?: ReactNode }) {
  return (
    <div className="mt-4 grid grid-cols-[16px_1fr] gap-x-2 gap-y-1 text-[12.5px] font-semibold leading-[1.4]">
      <Checkbox id={id} checked={checked} onCheckedChange={(v) => onChange(v === true)} className="mt-[2px]" />
      <label htmlFor={id} className="cursor-pointer">{children}</label>
      {hint && <div className="col-start-2"><Small>{hint}</Small></div>}
    </div>
  );
}

function Marketplaces({ name }: { name: string }) {
  return <Select name={name} defaultValue="us">{MKTS.map((m) => <option key={m} value={m}>{m}</option>)}</Select>;
}

const parseProxies = (v: FormDataEntryValue | null) =>
  v ? String(v).split(",").map((s) => s.trim()).filter(Boolean) : null;

/* ---------- niche research ---------- */
export function ResearchForm({ onJob, prefill }: { onJob: JobHandler; prefill: { seed: string; nonce: number } | null }) {
  const { busy, error, run } = useSubmit();
  const [plain, setPlain] = useState(false);
  const [seed, setSeed] = useState("");
  const formRef = useRef<HTMLFormElement>(null);

  // A discovered concept handed over from Discovery: fill the seed and run.
  useEffect(() => {
    if (!prefill) return;
    setSeed(prefill.seed);
    const t = setTimeout(() => {
      formRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
      formRef.current?.requestSubmit();
    }, 50);
    return () => clearTimeout(t);
  }, [prefill]);

  const submit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const body: Any = Object.fromEntries(f.entries());
    ["max_keywords", "books", "deep_dive"].forEach((k) => (body[k] = +body[k]));
    body.proxies = parseProxies(f.get("proxies"));
    body.plain_headers = plain;
    run(async () => onJob((await postJson("/research", body)).job_id));
  };
  const demo = () => run(async () => onJob((await postJson("/research", { seed: "air fryer", demo: true })).job_id));

  return (
    <form ref={formRef} onSubmit={submit}>
      <h2 className="panel-title mb-4">New research</h2>
      <Lbl>Seed keyword</Lbl>
      <Field name="seed" placeholder='e.g. "kidney disease food list"' required minLength={2} value={seed} onChange={(e) => setSeed(e.target.value)} />
      <Row2>
        <div><Lbl>Marketplace</Lbl><Marketplaces name="marketplace" /></div>
        <div><Lbl>Store</Lbl><Select name="store" defaultValue="kindle"><option value="kindle">Kindle</option><option value="books">Print books</option></Select></div>
      </Row2>
      <Row2>
        <div><Lbl>Keywords</Lbl><Field name="max_keywords" type="number" defaultValue={10} min={1} max={40} /></div>
        <div><Lbl>Books to scan</Lbl><Field name="books" type="number" defaultValue={30} min={5} max={200} /></div>
      </Row2>
      <Row2>
        <div><Lbl>Deep-dive books</Lbl><Field name="deep_dive" type="number" defaultValue={8} min={0} max={30} /></div>
        <div><Lbl>Fingerprint</Lbl><Select name="impersonate" defaultValue="edge"><option>edge</option><option>chrome</option><option>safari</option><option>firefox</option></Select></div>
      </Row2>
      <Lbl hint="(optional, comma-separated)">Rotating proxies</Lbl>
      <Field name="proxies" placeholder="http://user:pass@ip1:8080, …" />
      <Check id="plain" checked={plain} onChange={setPlain} hint="— drop the faked Google referer if Amazon returns tiny pages">Plain headers</Check>
      <Button type="submit" className="mt-6 h-11 w-full rounded-full text-[14px]" disabled={busy}>Run research</Button>
      <Button type="button" variant="outline" className="mt-2 h-11 w-full rounded-full border-0 bg-card-3 text-[14px] text-ink hover:bg-card-3/80" onClick={demo} disabled={busy}>Load demo dataset</Button>
      <ErrorLine error={error} />
    </form>
  );
}

/* ---------- discovery ---------- */
export function DiscoverForm({ onJob }: { onJob: JobHandler }) {
  const { busy, error, run } = useSubmit();
  const [cats, setCats] = useState<string[]>([]);
  const submit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const body = { window: f.get("window"), validate_top: +(f.get("validate_top") || 8), categories: cats.length ? cats : null };
    run(async () => onJob((await postJson("/discover", body)).job_id));
  };
  return (
    <form onSubmit={submit}>
      <h2 className="panel-title mb-4">Discovery</h2>
      <p className="m-0"><Small>No keyword needed. Finds what people are asking for right now, then checks whether Amazon already sells it.</Small></p>
      <Lbl>Time window</Lbl>
      <Select name="window" defaultValue="7d"><option value="24h">Last 24 hours</option><option value="7d">Last 7 days</option><option value="30d">Last 30 days</option></Select>
      <Lbl hint="(none selected = all)">Categories</Lbl>
      <CheckGrid options={CATS} value={cats} onChange={setCats} />
      <Lbl>Validate top</Lbl>
      <Field name="validate_top" type="number" defaultValue={8} min={1} max={20} />
      <Button type="submit" className="mt-6 h-11 w-full rounded-full text-[14px]" disabled={busy}>Find opportunities</Button>
      <ErrorLine error={error} />
    </form>
  );
}

function CheckGrid({ options, value, onChange }: { options: [string, string][]; value: string[]; onChange: (v: string[]) => void }) {
  return (
    <div className="mt-2 grid grid-cols-2 gap-x-3 gap-y-2">
      {options.map(([v, l]) => (
        <label key={v} className="flex cursor-pointer items-center gap-2 text-[12.5px] font-medium">
          <Checkbox checked={value.includes(v)} onCheckedChange={(c) => onChange(c === true ? [...value, v] : value.filter((x) => x !== v))} />
          {l}
        </label>
      ))}
    </div>
  );
}

/* ---------- trend radar ---------- */
export function TrendForm({ onJob }: { onJob: JobHandler }) {
  const { busy, error, run } = useSubmit();
  const [plain, setPlain] = useState(false);
  const submit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const body: Any = Object.fromEntries(f.entries());
    body.validate_top = +body.validate_top;
    body.proxies = parseProxies(f.get("proxies"));
    body.plain_headers = plain;
    if (!body.seed || !String(body.seed).trim()) delete body.seed; // empty topic means "scan everything"
    run(async () => onJob((await postJson("/trends", body)).job_id));
  };
  return (
    <form onSubmit={submit}>
      <h2 className="panel-title mb-4">Trend Radar</h2>
      <p className="m-0"><Small>Reads Google + YouTube + Reddit for what people are asking right now, then validates every candidate against live Amazon. Give it a topic to narrow the scan, or leave it blank to sweep every category.</Small></p>
      <Lbl hint="(optional — leave blank to scan every category)">Topic</Lbl>
      <Field name="seed" placeholder='e.g. "vagus nerve", "adhd" — or leave empty' />
      <Row2>
        <div><Lbl>Marketplace</Lbl><Marketplaces name="marketplace" /></div>
        <div><Lbl>Validate top</Lbl><Field name="validate_top" type="number" defaultValue={8} min={1} max={20} /></div>
      </Row2>
      <Lbl hint="(needed for Reddit + heavy use)">Rotating proxies</Lbl>
      <Field name="proxies" placeholder="http://user:pass@ip1:8080, …" />
      <Check id="plain-trend" checked={plain} onChange={setPlain} hint="— use if Amazon returns tiny pages">Plain headers</Check>
      <Button type="submit" className="mt-6 h-11 w-full rounded-full text-[14px]" disabled={busy}>Scan trends</Button>
      <ErrorLine error={error} />
    </form>
  );
}

/* ---------- teardown ---------- */
export function TeardownForm({ onJob }: { onJob: JobHandler }) {
  const { busy, error, run } = useSubmit();
  const submit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const identifiers = new FormData(e.currentTarget).get("identifiers");
    run(async () => onJob((await postJson("/teardown", { identifiers })).job_id));
  };
  return (
    <form onSubmit={submit}>
      <h2 className="panel-title mb-4">Reverse-ASIN teardown</h2>
      <p className="m-0"><Small>Paste ASINs or Amazon links — one per line, or comma separated. Each becomes a row: how the book is positioned, and how contested its shelf is.</Small></p>
      <Lbl hint="(up to 20)">ASINs or Amazon links</Lbl>
      <Area name="identifiers" rows={7} required placeholder={"B08JCQKGXZ\nhttps://www.amazon.com/dp/B0CTFW6JLD\nB0FFNQ7W9J"} />
      <Button type="submit" className="mt-6 h-11 w-full rounded-full text-[14px]" disabled={busy}>Tear down</Button>
      <ErrorLine error={error} />
    </form>
  );
}

/* ---------- listing check & translate ---------- */
export function ListingForm({ onShow }: { onShow: ShowHandler }) {
  const { busy, error, run } = useSubmit();
  const [markets, setMarkets] = useState<string[]>([]);
  const formRef = useRef<HTMLFormElement>(null);

  const body = () => {
    const f = new FormData(formRef.current!);
    const lines = (k: string) => String(f.get(k) || "").split("\n").map((x) => x.trim()).filter(Boolean);
    return {
      title: f.get("title") || "", subtitle: f.get("subtitle") || "", author: f.get("author") || "",
      description: f.get("description") || "", keywords: lines("keywords"), categories: lines("categories"),
    };
  };
  const check = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    run(async () => onShow(<ListingCheckView g={await postJson("/listing/check", body())} />));
  };
  const translate = () =>
    run(async () => {
      if (!markets.length) throw new Error("Pick at least one marketplace to translate for.");
      onShow(<div className="px-6 py-[14vh] text-center text-muted-foreground">Translating…</div>);
      onShow(<TranslateView out={await postJson("/listing/translate", { ...body(), marketplaces: markets })} />);
    });

  return (
    <form ref={formRef} onSubmit={check}>
      <h2 className="panel-title mb-4">Listing check &amp; translate</h2>
      <p className="m-0"><Small>Paste the listing exactly as you'd enter it in KDP. Check it against KDP's metadata rules, then translate it for other marketplaces — every translation is re-checked.</Small></p>
      <Lbl>Title</Lbl><Field name="title" maxLength={300} />
      <Lbl>Subtitle</Lbl><Field name="subtitle" maxLength={300} />
      <Lbl>Author</Lbl><Field name="author" />
      <Lbl hint="(HTML allowed: b, i, u, br, p, h1–h6, ul, ol, li)">Description</Lbl><Area name="description" rows={6} />
      <Lbl hint="(one per line)">7 backend keywords</Lbl><Area name="keywords" rows={7} />
      <Lbl hint="(one per line, up to 3)">Categories</Lbl><Area name="categories" rows={3} />
      <Lbl hint="(optional)">Translate for</Lbl>
      <CheckGrid options={MKTS_TRANSLATE} value={markets} onChange={setMarkets} />
      <Button type="submit" className="mt-6 h-11 w-full rounded-full text-[14px]" disabled={busy}>Check listing</Button>
      <Button type="button" variant="outline" className="mt-2 h-11 w-full rounded-full border-0 bg-card-3 text-[14px] text-ink hover:bg-card-3/80" onClick={translate} disabled={busy}>Translate</Button>
      <ErrorLine error={error} />
    </form>
  );
}

/* ---------- category catalogue ---------- */
export function CategoriesForm({ onJob, onShow }: { onJob: JobHandler; onShow: ShowHandler }) {
  const { busy, error, run } = useSubmit();
  const search = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const q = String(new FormData(e.currentTarget).get("q") || "");
    run(async () => onShow(<CategoriesView q={q} out={await api(`/categories?q=${encodeURIComponent(q)}&limit=200`)} />));
  };
  const crawl = () => run(async () => onJob((await postJson("/categories/crawl", { max_pages: 40 })).job_id));
  return (
    <form onSubmit={search}>
      <h2 className="panel-title mb-4">Category catalogue</h2>
      <p className="m-0"><Small>Every category any scan has observed, plus whatever the bestseller crawl has mapped. Grows with use — search it, or crawl more of Amazon's tree.</Small></p>
      <Lbl>Search</Lbl><Field name="q" placeholder="e.g. air fryer, menopause, budgeting" />
      <Button type="submit" className="mt-6 h-11 w-full rounded-full text-[14px]" disabled={busy}>Search</Button>
      <Button type="button" variant="outline" className="mt-2 h-11 w-full rounded-full border-0 bg-card-3 text-[14px] text-ink hover:bg-card-3/80" onClick={crawl} disabled={busy}>Crawl 40 pages</Button>
      <ErrorLine error={error} />
    </form>
  );
}

/* ---------- royalty calculator ---------- */
export function CalcForm({ onShow, jobs }: { onShow: ShowHandler; jobs: Job[] }) {
  const { busy, error, run } = useSubmit();
  const [color, setColor] = useState(false);
  const feasible = jobs.filter((j) => (j.kind || "research") === "research" && ["done", "partial"].includes(j.status));
  const submit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const f = new FormData(e.currentTarget), body: Any = Object.fromEntries(f.entries());
    ["goal_month", "price", "file_mb", "pages"].forEach((k) => (body[k] = +body[k]));
    body.color = color;
    if (!body.research_job_id) delete body.research_job_id;
    run(async () => onShow(<CalcView r={await postJson("/royalty", body)} />));
  };
  return (
    <form onSubmit={submit}>
      <h2 className="panel-title mb-4">Royalty calculator</h2>
      <Lbl>Monthly royalty goal ($)</Lbl><Field name="goal_month" type="number" defaultValue={1000} min={50} step={50} />
      <Row2>
        <div><Lbl>Format</Lbl>
          <Select name="format" defaultValue="ebook">
            <option value="ebook">eBook</option><option value="paperback">Paperback</option><option value="hardcover">Hardcover</option>
            <option value="audiobook_acx">Audiobook (ACX excl.)</option><option value="audiobook_wide">Audiobook (wide)</option>
          </Select></div>
        <div><Lbl>List price ($)</Lbl><Field name="price" type="number" defaultValue={4.99} min={0.99} step="any" /></div>
      </Row2>
      <Row2>
        <div><Lbl>Pages</Lbl><Field name="pages" type="number" defaultValue={120} min={24} max={900} /></div>
        <div><Lbl>eBook size (MB)</Lbl><Field name="file_mb" type="number" defaultValue={2} min={0.1} step="any" /></div>
      </Row2>
      <Check id="color" checked={color} onChange={setColor}>Color interior (print)</Check>
      <Lbl>Check feasibility against research job</Lbl>
      <Select name="research_job_id" defaultValue="">
        <option value="">— none —</option>
        {feasible.map((j) => <option key={j.id} value={j.id}>{j.seed} ({j.marketplace.toUpperCase()})</option>)}
      </Select>
      <Button type="submit" className="mt-6 h-11 w-full rounded-full text-[14px]" disabled={busy}>Calculate</Button>
      <ErrorLine error={error} />
    </form>
  );
}
