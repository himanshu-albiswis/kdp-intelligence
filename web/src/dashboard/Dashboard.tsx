import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { cn } from "@/lib/utils";
import { api, postJson, TABS, type Job, type Tab } from "@/lib/api";
import { useJob, useJobs } from "./hooks";
import { Bar, Chip, Empty, Small } from "./primitives";
import { CalcForm, CategoriesForm, DiscoverForm, ListingForm, ResearchForm, TeardownForm, TrendForm } from "./forms";
import { CategoriesView, CrawlView, DiscoveryView, ResearchView, TeardownView, TrendsView } from "./views";

/* The dashboard shell: glass header with the tool tabs, a sidebar holding
   the active tool's form plus every job, and a main panel that shows either
   a polled job or a finished result handed over by a form. The active tab
   lives in the URL (?tab=) so the landing page and deep links can open a
   tool directly. */

export default function Dashboard() {
  const [params, setParams] = useSearchParams();
  const tab = (TABS.some((t) => t.id === params.get("tab")) ? params.get("tab") : "research") as Tab;
  const setTab = (t: Tab) => setParams({ tab: t }, { replace: true });

  const [activeJob, setActiveJob] = useState<string | null>(null);
  const [panel, setPanel] = useState<ReactNode | null>(null);
  const [prefill, setPrefill] = useState<{ seed: string; nonce: number } | null>(null);
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

  // A discovered concept handed to the full research pipeline.
  const validateConcept = (phrase: string) => {
    setTab("research");
    setPrefill({ seed: String(phrase || "").slice(0, 120), nonce: Date.now() });
  };

  let main: ReactNode;
  if (panel) main = panel;
  else if (activeJob && !job) main = <Empty>Loading…</Empty>;
  else if (job) main = <JobView job={job} onValidate={validateConcept} />;
  else main = <Empty>Run a research job, scan a trend, or open the calculator.</Empty>;

  return (
    <div className="h-screen overflow-hidden max-lg:h-auto max-lg:overflow-auto">
      <div className="aurora" aria-hidden="true"><span /><span /><span /><span /></div>

      <header className="relative z-20 mx-3 mt-3 flex flex-wrap items-center gap-6 rounded-2xl border border-stroke bg-glass-strong px-6 py-3 shadow-[var(--shadow),inset_0_1px_0_var(--glass-inset)] backdrop-blur-2xl backdrop-saturate-[180%] max-lg:gap-3">
        <Link to="/" className="display m-0 whitespace-nowrap text-[23px] text-ink no-underline max-lg:text-[20px]">📚 KDP Niche Intelligence</Link>
        <nav className="ml-auto flex gap-1 max-lg:ml-0 max-lg:w-full max-lg:gap-2">
          {TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              onClick={() => setTab(t.id)}
              className={cn(
                "h-[34px] cursor-pointer rounded-full border border-transparent px-4 text-[13.5px] font-medium text-muted-foreground transition-colors hover:bg-glass hover:text-ink max-lg:h-auto max-lg:min-h-9 max-lg:flex-1 max-lg:px-3 max-lg:py-2 max-lg:text-[12.5px] max-lg:leading-tight",
                tab === t.id && "bg-gilt font-semibold text-gilt-ink hover:bg-gilt hover:text-gilt-ink",
              )}
            >
              {t.label}
            </button>
          ))}
        </nav>
      </header>

      <div className="grid h-[calc(100vh-70px)] grid-cols-[340px_minmax(0,1fr)] gap-3 p-3 max-lg:h-auto max-lg:grid-cols-1">
        <aside className="glass overflow-y-auto px-4 py-6">
          {tab === "research" && <ResearchForm onJob={openJob} prefill={prefill} />}
          {tab === "discover" && <DiscoverForm onJob={openJob} />}
          {tab === "trends" && <TrendForm onJob={openJob} />}
          {tab === "teardown" && <TeardownForm onJob={openJob} />}
          {tab === "listing" && <ListingForm onShow={show} />}
          {tab === "categories" && <CategoriesForm onJob={openJob} onShow={show} />}
          {tab === "calc" && <CalcForm onShow={show} jobs={jobs} />}

          <h3 className="rule">Jobs</h3>
          <JobList jobs={jobs} active={activeJob} onOpen={openJob} />
        </aside>

        <main className="glass min-w-0 overflow-y-auto px-6 pb-8 pt-6">{main}</main>
      </div>
    </div>
  );
}

function JobView({ job, onValidate }: { job: Job; onValidate: (phrase: string) => void }) {
  switch (job.kind) {
    case "trends": return <TrendsView job={job} />;
    case "discovery": return <DiscoveryView job={job} onValidate={onValidate} />;
    case "teardown": return <TeardownView job={job} />;
    case "crawl": return <CrawlView job={job} />;
    default: return <ResearchView job={job} />;
  }
}

function JobList({ jobs, active, onOpen }: { jobs: Job[]; active: string | null; onOpen: (id: string) => void }) {
  if (!jobs.length) return <Small>No jobs yet.</Small>;
  return (
    <div>
      {jobs.map((j) => (
        <div
          key={j.id}
          role="button"
          tabIndex={0}
          aria-current={j.id === active}
          aria-label={`Open ${j.seed} ${j.kind || "research"} job, ${j.status}`}
          onClick={() => onOpen(j.id)}
          onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onOpen(j.id); } }}
          className={cn(
            "mb-2 cursor-pointer rounded-[10px] border border-stroke bg-glass-strong p-3 transition-[border-color,transform] hover:translate-x-[2px] hover:border-stroke-strong",
            j.id === active && "border-gilt shadow-[inset_3px_0_0_var(--gilt)]",
          )}
        >
          <div className="text-[13.5px] font-semibold leading-[1.35]">{j.seed} {j.demo && <Small>(demo)</Small>}</div>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-[11.5px] text-muted-foreground">
            <span className="chip border-stroke bg-glass text-faint">{j.kind || "research"}</span>
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
