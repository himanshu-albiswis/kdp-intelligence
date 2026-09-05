import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  ArrowLeft, Bell, BookOpen, Calculator, Download, FileSearch, FileText, FolderTree, LayoutGrid, MessageSquare, Search, TrendingUp,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { api, postJson, TABS, type Job, type Tab } from "@/lib/api";
import { useJob, useJobs } from "./hooks";
import { Bar, Chip, Empty, Panel, Small } from "./primitives";
import { CalcForm, CategoriesForm, DiscoverForm, ListingForm, ResearchForm, TeardownForm, TrendForm } from "./forms";
import { CategoriesView, CrawlView, DiscoveryView, ResearchView, TeardownView, TrendsView } from "./views";

/* Monochrome admin shell: black frame, sidebar menu with icon tiles, a
   charcoal main surface with the top bar, breadcrumb and a card grid. The
   right column carries the active tool's form and the job list; results
   render as cards on the left. The active tool lives in the URL (?tab=). */

const ICONS: Record<Tab, ReactNode> = {
  research: <LayoutGrid size={18} />, discover: <Search size={18} />, trends: <TrendingUp size={18} />,
  teardown: <FileSearch size={18} />, listing: <FileText size={18} />, categories: <FolderTree size={18} />, calc: <Calculator size={18} />,
};

export default function Dashboard() {
  const [params, setParams] = useSearchParams();
  const tab = (TABS.some((t) => t.id === params.get("tab")) ? params.get("tab") : "research") as Tab;
  const tabLabel = TABS.find((t) => t.id === tab)!.label;
  const setTab = (t: Tab) => setParams({ tab: t }, { replace: true });

  const [activeJob, setActiveJob] = useState<string | null>(null);
  const [panel, setPanel] = useState<ReactNode | null>(null);
  const [prefill, setPrefill] = useState<{ seed: string; nonce: number } | null>(null);
  const [query, setQuery] = useState("");
  const { jobs, refresh } = useJobs();
  const job = useJob(activeJob);

  const openJob = (id: string) => { setPanel(null); setActiveJob(id); refresh(); };
  const show = (node: ReactNode) => { setActiveJob(null); setPanel(node); };

  // /app?demo=1 from the landing page: run the bundled sample scan once.
  const demoStarted = useRef(false);
  useEffect(() => {
    if (params.get("demo") !== "1" || demoStarted.current) return;
    demoStarted.current = true;
    postJson("/research", { seed: "air fryer", demo: true }).then((r) => openJob(r.job_id)).catch(() => undefined);
    setParams({ tab: "research" }, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Tabs that open with content rather than an empty panel.
  useEffect(() => {
    if (tab === "categories") api("/categories?q=&limit=200").then((out) => show(<CategoriesView out={out} q="" />)).catch(() => undefined);
    if (tab === "calc") show(<Empty>Fill the calculator to see break-even math.</Empty>);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab]);

  const validateConcept = (phrase: string) => {
    setTab("research");
    setPrefill({ seed: String(phrase || "").slice(0, 120), nonce: Date.now() });
  };

  const running = jobs.filter((j) => ["running", "queued"].includes(j.status)).length;
  const finished = jobs.filter((j) => ["done", "partial"].includes(j.status)).length;
  const visibleJobs = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q ? jobs.filter((j) => `${j.seed} ${j.kind || "research"} ${j.status}`.toLowerCase().includes(q)) : jobs;
  }, [jobs, query]);
  const report = job && (job.kind || "research") === "research" && job.result ? `/api/research/${job.id}/csv?table=keywords` : null;

  let main: ReactNode;
  if (panel) main = <Panel>{panel}</Panel>;
  else if (activeJob && !job) main = <Panel><Empty>Loading…</Empty></Panel>;
  else if (job) main = <JobView job={job} onValidate={validateConcept} />;
  else main = <Panel><Empty>Run a research job, scan a trend, or open the calculator.</Empty></Panel>;

  return (
    <div className="flex min-h-screen bg-background text-foreground">
      {/* ---- sidebar ---- */}
      <aside className="sticky top-0 flex h-screen w-[272px] shrink-0 flex-col px-8 py-10 max-lg:hidden">
        <Link to="/" className="flex items-center gap-3 text-ink no-underline">
          <span className="flex h-12 w-12 items-center justify-center rounded-full bg-white text-black"><BookOpen size={22} /></span>
          <span className="text-[24px] font-medium">KDP Niche</span>
        </Link>
        <div className="mt-14 text-[13px] font-medium tracking-[0.06em] text-muted-foreground">MAIN MENU</div>
        <nav className="mt-6 flex flex-col gap-2">
          {TABS.map((t) => (
            <button key={t.id} type="button" onClick={() => setTab(t.id)}
              className={cn("relative flex h-14 items-center gap-4 rounded-2xl px-2 text-left text-[16px] text-muted-foreground transition-colors hover:text-ink",
                tab === t.id && "text-ink")}>
              <span className={cn("tile", tab === t.id && "bg-card-3")}>{ICONS[t.id]}</span>
              {t.label}
              {tab === t.id && <span className="absolute -right-8 h-2 w-2 rotate-45 rounded-[2px] bg-card-3" aria-hidden="true" />}
            </button>
          ))}
        </nav>
        <div className="mt-auto border-t border-line pt-6 text-[12.5px] leading-relaxed text-muted-foreground">
          <div className="text-[14px] font-medium text-ink">KDP Niche Intelligence</div>
          <div>© 2026 All rights reserved</div>
          <div>Made with ♥ for KDP publishers</div>
        </div>
      </aside>

      {/* ---- main surface ---- */}
      <div className="my-3 mr-3 min-w-0 flex-1 rounded-[28px] bg-surface px-8 py-8 max-lg:m-0 max-lg:rounded-none max-lg:px-4">
        <header className="flex flex-wrap items-center gap-4">
          <Link to="/" className="text-ink" aria-label="Back to landing page"><ArrowLeft size={26} /></Link>
          <h1 className="m-0 text-[28px] font-medium text-ink">{tabLabel}</h1>
          <label className="ml-4 flex h-14 w-[340px] max-w-full items-center gap-3 rounded-full bg-white px-5 text-black max-lg:ml-0">
            <Search size={22} className="shrink-0" />
            <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search scans"
              className="w-full bg-transparent text-[15px] text-black outline-none placeholder:text-neutral-500" aria-label="Search scans" />
          </label>
          {report ? (
            <a href={report} download className="flex h-14 items-center gap-3 rounded-full bg-card-3 px-6 text-[15px] text-ink no-underline hover:bg-card-3/80">
              <Download size={18} /> Get Report
            </a>
          ) : (
            <span className="flex h-14 cursor-not-allowed items-center gap-3 rounded-full bg-card-2 px-6 text-[15px] text-faint" title="Open a finished research scan to export its keywords">
              <Download size={18} /> Get Report
            </span>
          )}
          <div className="ml-auto flex items-center gap-4">
            <Badge icon={<MessageSquare size={20} />} count={running} label="running jobs" />
            <Badge icon={<Bell size={20} />} count={finished} label="finished jobs" />
            <div className="flex items-center gap-3">
              <span className="flex h-14 w-14 items-center justify-center rounded-full bg-card-3 text-[22px]">📚</span>
              <div className="leading-tight">
                <div className="text-[16px] font-medium text-ink">Publisher</div>
                <div className="text-[12.5px] text-muted-foreground">KDP Niche Intelligence</div>
              </div>
            </div>
          </div>
        </header>

        <div className="mt-6 text-[17px] text-muted-foreground">
          <button type="button" onClick={() => setTab(tab)} className="text-faint hover:text-ink">{tabLabel}</button>
          <span className="mx-2">/</span>
          <span className="text-ink">{job ? job.seed : panel ? "Result" : "Overview"}</span>
        </div>

        {/* mobile tool switcher */}
        <nav className="mt-4 flex gap-2 overflow-x-auto lg:hidden">
          {TABS.map((t) => (
            <button key={t.id} type="button" onClick={() => setTab(t.id)}
              className={cn("h-10 shrink-0 rounded-full bg-card-2 px-4 text-[13px] text-muted-foreground", tab === t.id && "bg-white text-black")}>
              {t.label}
            </button>
          ))}
        </nav>

        <div className="mt-6 grid grid-cols-[minmax(0,1fr)_360px] gap-5 max-xl:grid-cols-1">
          <div className="min-w-0">{main}</div>
          <div className="flex flex-col gap-5">
            <Panel kebab={false}>
              {tab === "research" && <ResearchForm onJob={openJob} prefill={prefill} />}
              {tab === "discover" && <DiscoverForm onJob={openJob} />}
              {tab === "trends" && <TrendForm onJob={openJob} />}
              {tab === "teardown" && <TeardownForm onJob={openJob} />}
              {tab === "listing" && <ListingForm onShow={show} />}
              {tab === "categories" && <CategoriesForm onJob={openJob} onShow={show} />}
              {tab === "calc" && <CalcForm onShow={show} jobs={jobs} />}
            </Panel>
            <Panel title="Recent scans" right={<Small>{visibleJobs.length}</Small>}>
              <JobList jobs={visibleJobs} active={activeJob} onOpen={openJob} />
            </Panel>
          </div>
        </div>
      </div>
    </div>
  );
}

function Badge({ icon, count, label }: { icon: ReactNode; count: number; label: string }) {
  return (
    <span className="iconbtn relative" title={`${count} ${label}`} aria-label={`${count} ${label}`}>
      {icon}
      <span className="absolute -right-1 -top-1 flex h-5 min-w-5 items-center justify-center rounded-full bg-card-3 px-1 text-[10px] font-medium text-ink">{count}</span>
    </span>
  );
}

function JobView({ job, onValidate }: { job: Job; onValidate: (phrase: string) => void }) {
  switch (job.kind) {
    case "trends": return <Panel><TrendsView job={job} /></Panel>;
    case "discovery": return <Panel><DiscoveryView job={job} onValidate={onValidate} /></Panel>;
    case "teardown": return <Panel><TeardownView job={job} /></Panel>;
    case "crawl": return <Panel><CrawlView job={job} /></Panel>;
    default: return <ResearchView job={job} />;
  }
}

function JobList({ jobs, active, onOpen }: { jobs: Job[]; active: string | null; onOpen: (id: string) => void }) {
  if (!jobs.length) return <Small>No scans yet.</Small>;
  return (
    <div className="max-h-[60vh] overflow-y-auto pr-1">
      {jobs.map((j) => (
        <div key={j.id} role="button" tabIndex={0} aria-current={j.id === active}
          aria-label={`Open ${j.seed} ${j.kind || "research"} job, ${j.status}`}
          onClick={() => onOpen(j.id)}
          onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onOpen(j.id); } }}
          className={cn("mb-2 cursor-pointer rounded-[16px] bg-card-2 p-3 transition-colors hover:bg-card-3", j.id === active && "bg-card-3 ring-1 ring-white/30")}>
          <div className="text-[14px] font-medium leading-[1.35] text-ink">{j.seed} {j.demo && <Small>(demo)</Small>}</div>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-[11.5px] text-muted-foreground">
            <span className="chip">{j.kind || "research"}</span>
            {j.marketplace.toUpperCase()} · {(j.created_at || "").slice(0, 16).replace("T", " ")}
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-[11.5px] text-muted-foreground">
            <Chip status={j.status}>{j.status}</Chip> {j.status === "running" ? j.stage : ""}
          </div>
          {["running", "queued"].includes(j.status) && <Bar pct={j.pct} />}
        </div>
      ))}
    </div>
  );
}
