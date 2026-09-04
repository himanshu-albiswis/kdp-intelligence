import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { api, fmt, type Any, type Job } from "@/lib/api";
import { cn } from "@/lib/utils";
import {
  Empty, ExportBtn, Kpi, Kpis, Md, NoData, PageHead, Progress, Q, Rule, Small, TableWrap, TagChip, Warn, Warnings,
} from "../primitives";

const COLUMNS: [string, string][] = [
  ["keyword", "Keyword"], ["demand_index", "Demand"], ["total_results", "Results"], ["median_reviews", "Med rev"],
  ["pct_under_100", "<100"], ["ku_share", "KU%"], ["phrase_in_titles", "In titles"], ["opportunity", "Score"], ["verdict", "Verdict"],
];

export function ResearchView({ job }: { job: Job }) {
  const [sortKey, setSortKey] = useState("opportunity");
  const [sortDir, setSortDir] = useState(-1);
  const [brief, setBrief] = useState<Any | null>(job.result?.brief ?? null);
  const [briefState, setBriefState] = useState<"idle" | "loading" | { error: string }>("idle");
  useEffect(() => setBrief(job.result?.brief ?? null), [job.id, job.result?.brief]);

  if (job.status === "failed") return <Warn><b>Job failed.</b> {job.message}</Warn>;
  if (!job.result) return <Progress job={job} />;

  const r = job.result, s = r.summary || {}, cur: string = r.currency || "$";
  const kws: Any[] = [...(r.keywords || [])].sort((a, b) => ((a[sortKey] ?? -1) < (b[sortKey] ?? -1) ? sortDir : -sortDir));
  const money: Any[] = (r.book_intel || []).filter((b: Any) => b.est_monthly_royalty).sort((a: Any, b: Any) => b.est_monthly_royalty - a.est_monthly_royalty);
  const priceOk = r.currency_ok !== false;
  const setSort = (k: string) => { setSortDir(sortKey === k ? -sortDir : -1); setSortKey(k); };

  const makeBrief = async () => {
    setBriefState("loading");
    try {
      setBrief(await api(`/research/${job.id}/brief?goal_month=1000`, { method: "POST" }));
      setBriefState("idle");
    } catch (e) {
      setBriefState({ error: (e as Error).message });
    }
  };

  return (
    <>
      <PageHead
        title={<>“{r.seed}”</>}
        meta={<>{r.marketplace.toUpperCase()} · {r.store || ""} · focus “{r.focus_keyword}”</>}
        actions={
          <>
            <ExportBtn jobId={job.id} table="keywords" label="Keywords CSV" />
            <ExportBtn jobId={job.id} table="book_intel" label="Money CSV" />
            <ExportBtn jobId={job.id} table="categories" label="Categories CSV" />
            <Button size="sm" onClick={makeBrief} disabled={briefState === "loading"}>Generate Niche Brief</Button>
          </>
        }
      />
      <Warnings items={r.warnings} />
      {briefState === "loading" && <Empty>Generating brief…</Empty>}
      {typeof briefState === "object" && <Warn>{briefState.error}</Warn>}
      {brief && briefState === "idle" && <Brief b={brief} cur={cur} />}

      <Kpis>
        <Kpi value={s.total_results != null ? "~" + fmt(s.total_results) : "?"} label="competing books" />
        <IncomeCard inc={r.income} cur={cur} />
        <Kpi value={fmt(s.median_reviews)} label="median reviews" />
        <Kpi value={s.ku_share_pct != null ? s.ku_share_pct + "%" : "?"} label="Kindle Unlimited" />
        <Kpi value={s.avg_buy_price != null ? cur + s.avg_buy_price : "—"} label="avg buy price" />
        <Kpi value={s.velocity_pct_90d != null ? s.velocity_pct_90d + "%" : "?"} label="top sellers <90d old" />
      </Kpis>

      <Rule>Keyword opportunities</Rule>
      {!kws.length ? (
        <NoData>
          No keywords were scored. Amazon returned no usable results page for this seed, so nothing could be
          validated — the warnings above say why. Nothing here is a zero; it is an absence of data.
        </NoData>
      ) : (
        <TableWrap>
          <thead>
            <tr>
              {COLUMNS.map(([k, l]) => (
                <th key={k}>
                  <button type="button" className="inline-flex cursor-pointer items-center gap-[2px] hover:text-ink" onClick={() => setSort(k)} aria-label={`Sort by ${l}`}>
                    {l}{sortKey === k ? (sortDir < 0 ? " ↓" : " ↑") : ""}
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {kws.map((k) => (
              <tr key={k.keyword}>
                <td><a href={k.url} target="_blank" rel="noopener">{k.keyword}</a></td>
                <td className="num" title={k.demand_basis || ""}>
                  {k.demand_index != null && k.demand_band ? (
                    <>
                      <b>{k.demand_index}</b>{" "}
                      <span className={cn("ml-1 inline-block rounded-full px-[7px] py-[1px] align-middle text-[9.5px] font-bold uppercase tracking-[0.05em]", `d-${String(k.demand_band).replace(" ", "-")}`)}>
                        {k.demand_band}
                      </span>
                    </>
                  ) : "—"}
                </td>
                <td className="num">{fmt(k.total_results)}</td>
                <td className="num">{Math.round(k.median_reviews)}</td>
                <td className="num">{Math.round(k.pct_under_100)}%</td>
                <td className="num">{Math.round(k.ku_share)}%</td>
                <td className="num">{k.phrase_in_titles}</td>
                <td className="num"><b>{k.opportunity}</b><ScoreBar pct={k.opportunity} /></td>
                <td>{k.verdict}</td>
              </tr>
            ))}
          </tbody>
        </TableWrap>
      )}

      {money.length > 0 && (
        <>
          <Rule>Money proof (BSR → est. royalties)</Rule>
          <TableWrap>
            <thead><tr><th>Book</th><th>BSR</th><th>Price</th><th>Sales/day</th><th>Royalty/mo</th><th>Published</th></tr></thead>
            <tbody>
              {money.map((b) => (
                <tr key={b.asin}>
                  <td><a href={b.url} target="_blank" rel="noopener">{(b.title || "").slice(0, 64)}</a></td>
                  <td className="num">{fmt(b.bsr)}</td>
                  <td className="num">{b.price != null && priceOk ? cur + b.price.toFixed(2) : b.kindle_unlimited ? "KU" : "—"}</td>
                  <td className="num">{b.est_sales_per_day ?? "—"}</td>
                  <td className="num"><b>{cur}{fmt(Math.round(b.est_monthly_royalty))}</b></td>
                  <td className="num">{b.publication_date || "?"}</td>
                </tr>
              ))}
            </tbody>
          </TableWrap>
        </>
      )}

      {r.category_intel?.picks?.length > 0 && (
        <>
          <Rule hint="where the niche lives, and what a badge costs">Category picks</Rule>
          <div className="my-3 grid grid-cols-[repeat(auto-fit,minmax(240px,1fr))] gap-3">
            {r.category_intel.picks.map((p: Any) => (
              <div key={p.category} className="glass-strong flex flex-col gap-2 p-4">
                <div className="flex items-baseline justify-between gap-2">
                  <b>{p.category}</b>
                  <span className={cn("rounded-full border border-stroke px-[7px] py-[2px] text-[9.5px] font-bold uppercase tracking-[0.07em]", p.exact && "border-money/40 text-money")}>
                    {p.exact ? "exact read" : "floor"}
                  </span>
                </div>
                <div className="font-mono text-[15px] font-semibold text-money">
                  {p.entry_sales_day != null ? `${p.entry_sales_day}+ sales/day for #1` : "entry bar unknown"}
                </div>
                <Small>{p.why}</Small>
              </div>
            ))}
          </div>
          <p><Small>{r.category_intel.note || ""}</Small></p>
        </>
      )}

      {r.pricing?.priced ? (
        <>
          <Rule hint="bands follow KDP's 70% window">Pricing on page 1</Rule>
          <Kpis>
            <Kpi mono value={cur + r.pricing.median} label={<>median price · {cur}{r.pricing.q1}–{cur}{r.pricing.q3} middle half</>} />
            <Kpi value={r.pricing.sweet_spot ? r.pricing.sweet_spot.label : "—"} label={<>sweet spot{r.pricing.sweet_spot ? " · " + r.pricing.sweet_spot.why : ""}</>} />
            <Kpi value={r.pricing.price_rank_relation.direction} label={<>price vs rank{r.pricing.price_rank_relation.rho != null ? " · ρ " + r.pricing.price_rank_relation.rho : ""}</>} />
          </Kpis>
          <div className="my-3 grid grid-cols-[repeat(auto-fit,minmax(150px,1fr))] gap-2">
            {r.pricing.bands.map((b: Any) => (
              <div key={b.label} className="glass-strong flex flex-col gap-[2px] p-3 text-[12.5px]">
                <b>{b.label}</b><span className="font-mono">{b.count} book(s)</span>
                <Small>{b.median_bsr ? "median BSR #" + fmt(b.median_bsr) : "—"}</Small>
              </div>
            ))}
          </div>
        </>
      ) : null}

      {r.velocity?.books_measured ? (
        <>
          <Rule>Review velocity</Rule>
          <Small>
            Median <b>{r.velocity.median_per_month}</b> reviews/month across {r.velocity.books_measured} dated books — shelf reads{" "}
            <Q tag={r.velocity.band} />
          </Small>
        </>
      ) : null}

      {r.praise?.length > 0 && (
        <>
          <Rule hint="the features you can't skip">What 4–5★ reviews reward</Rule>
          {r.praise.map((p: Any) => (
            <div key={p.theme} className="note" style={{ borderLeftColor: "var(--money)" }}>
              <div className="mb-1 font-semibold text-money">{p.theme} <Small>· {p.mentions} reviews</Small></div>
              {p.example}
            </div>
          ))}
        </>
      )}

      {r.listing_benchmark?.books ? (
        <>
          <Rule>Listing polish on page 1</Rule>
          <Small>{r.listing_benchmark.read}</Small>
        </>
      ) : null}

      {r.also_viewed?.neighbours?.length > 0 && (
        <>
          <Rule hint="customers also viewed">Where buyers drift</Rule>
          <div className="mb-2"><Small>{r.also_viewed.read}</Small></div>
          <div>
            {r.also_viewed.neighbours.slice(0, 16).map((n: Any) => (
              <TagChip key={n.asin} href={n.url} offpage={!n.on_page_1}>
                {n.title || n.asin} <Small>×{n.named_by}</Small>
              </TagChip>
            ))}
          </div>
        </>
      )}

      {r.title_gaps?.length > 0 && (
        <>
          <Rule>Title gaps</Rule>
          <div>{r.title_gaps.map((g: string) => <TagChip key={g}>💡 {g}</TagChip>)}</div>
        </>
      )}

      {r.title_ngrams?.length > 0 && (
        <>
          <Rule>What page-1 titles are made of</Rule>
          <div>{r.title_ngrams.map(([g, c]: [string, number]) => <TagChip key={g}>{g} ×{c}</TagChip>)}</div>
        </>
      )}

      {r.complaints?.length > 0 && (
        <>
          <Rule>What buyers complain about</Rule>
          {r.complaints.slice(0, 10).map((c: Any, i: number) => (
            <div key={i} className="note">
              <div className="mb-1 font-semibold text-risk">
                {"★".repeat(Math.round(c.rating))} {c.title || "(no title)"} <Small>— “{c.book_title}”</Small>
              </div>
              {c.body}
            </div>
          ))}
        </>
      )}

      {r.books?.length > 0 && (
        <>
          <Rule>Books in the niche ({r.books.length})</Rule>
          <div className="grid grid-cols-[repeat(auto-fill,minmax(280px,1fr))] gap-2">
            {r.books.slice(0, 40).map((b: Any) => (
              <a key={b.asin || b.url} className="glass-strong flex gap-3 p-2 text-inherit no-underline transition-[border-color,transform] hover:-translate-y-[2px] hover:border-gilt" href={b.url} target="_blank" rel="noopener">
                {b.image_url ? <img loading="lazy" src={b.image_url} alt="" className="h-[72px] w-12 shrink-0 rounded-md bg-stroke object-cover" /> : <div className="h-[74px] w-[50px]" />}
                <div>
                  <div className="mb-1 text-[12px] font-semibold leading-[1.35]">{(b.title || "").slice(0, 72)}</div>
                  <div className="font-mono text-[11px] text-muted-foreground">
                    {b.kindle_unlimited && <span className="mr-1 rounded bg-gilt px-[5px] py-[1px] text-[8.5px] font-bold tracking-[0.05em] text-gilt-ink">KU</span>}
                    {b.price != null && priceOk ? cur + b.price.toFixed(2) : b.kindle_unlimited ? "KU only" : "—"} · {b.rating ?? "—"}★ · {fmt(b.reviews)} rev
                  </div>
                </div>
              </a>
            ))}
          </div>
        </>
      )}
    </>
  );
}

function ScoreBar({ pct }: { pct: number }) {
  return (
    <span className="ml-2 inline-block h-1 w-11 overflow-hidden rounded-full bg-stroke align-middle">
      <i className="block h-full rounded-full bg-money" style={{ width: `${pct}%` }} />
    </span>
  );
}

/* The signature element: income is a measured span, not a point value.
   The bar shows low..high on a shared axis with the midpoint marked, so the
   width of the uncertainty is visible rather than hidden behind a rounded
   number. Confidence is named, never implied. */
function IncomeCard({ inc, cur }: { inc: Any; cur: string }) {
  if (!inc || !inc.books_counted) {
    return (
      <div className="glass-strong col-span-2 flex flex-col justify-end gap-1 p-4 max-sm:col-span-1">
        <div className="font-mono text-[21px] font-medium">—</div>
        <div className="text-[11px] text-muted-foreground">Estimated income / month</div>
        <div className="text-[10.5px] tracking-[0.03em] text-faint">{inc?.basis || "not enough priced, ranked books"}</div>
      </div>
    );
  }
  const axis = inc.total_high * 1.15 || 1;
  const pct = (v: number) => Math.max(0, Math.min(100, (v / axis) * 100));
  const left = pct(inc.total_low), right = pct(inc.total_high);
  const conf: string = inc.confidence || "none";
  return (
    <div className="glass-strong col-span-2 flex flex-col justify-end gap-1 p-4 max-sm:col-span-1">
      <div className="font-mono text-[21px] font-medium tracking-tight tabular-nums">
        {cur}{fmt(Math.round(inc.total_low))} – {cur}{fmt(Math.round(inc.total_high))}
      </div>
      <div className="text-[11px] text-muted-foreground">
        Estimated income / month{inc.ku_mid ? ` · includes ${cur}${fmt(Math.round(inc.ku_mid))} Kindle Unlimited` : ""}
      </div>
      <div className="relative my-2 h-2 rounded-full bg-stroke">
        <span className="absolute top-0 h-full rounded-full" style={{ left: `${left}%`, width: `${Math.max(2, right - left)}%`, background: "linear-gradient(90deg, color-mix(in srgb, var(--money) 45%, transparent), var(--money))" }} />
        <i className="absolute -top-[3px] h-[14px] w-[2px] rounded-[1px] bg-ink opacity-75" style={{ left: `${pct(inc.total_mid)}%` }} />
      </div>
      <div className="flex items-center justify-between gap-2 text-[10.5px] tracking-[0.03em] text-faint">
        <span>{cur}0</span>
        <span className={cn("rounded-full px-[7px] py-[1px] font-bold uppercase tracking-[0.08em]", `q-${conf}`)}>{conf} confidence</span>
        <span>{cur}{fmt(Math.round(axis))}</span>
      </div>
    </div>
  );
}

function Brief({ b, cur }: { b: Any; cur: string }) {
  const be = b.breakeven || {}, feas = be.feasibility || {};
  const verdictColor = { VALIDATE: "text-money", BORDERLINE: "text-gold", SKIP: "text-risk" }[b.verdict as string] || "";
  return (
    <div className="my-4 rounded-2xl border border-stroke-strong bg-glass-strong p-6 shadow-[var(--shadow)]">
      <div className={cn("display text-[38px] leading-none", verdictColor)}>{b.verdict}</div>
      <Small>
        {b.verdict_reason} · goal {cur}{fmt(b.goal_month)}/mo · generated from live data
        {b.narrative_source && !String(b.narrative_source).startsWith("none") ? ` + AI narrative (${b.narrative_source})` : ""}
      </Small>
      <div className="mt-[10px]">
        {(b.gates || []).map((g: Any) => (
          <div key={g.gate} className="flex items-baseline gap-3 border-b border-stroke py-2 text-[13px]">
            <b className={cn("min-w-[64px] text-[10px] uppercase tracking-[0.08em]", { PASS: "text-money", FAIL: "text-risk" }[g.status as string] || "text-faint")}>{g.status}</b>
            <b>{g.gate}</b> <span>{g.detail}</span>
          </div>
        ))}
      </div>
      {be.royalty_per_sale ? (
        <div className="my-4 rounded-[10px] border border-stroke bg-glass p-4 text-[13.5px] leading-[1.7]">
          <b>Break-even at the niche's price ({cur}{be.price}):</b> {cur}{be.royalty_per_sale}/sale ({be.plan}) → <b>{be.sales_needed_per_day} sales/day</b> ({fmt(be.sales_needed_per_month)}/mo) → <b>BSR ≈ {fmt(be.bsr_needed)}</b> needed.
          <br />{feas.read || ""} {be.ku_note && <><br />{be.ku_note}</>}
        </div>
      ) : null}
      {b.ku_read && <div className="mt-[6px]"><Small>📖 {b.ku_read}</Small></div>}
      {b.differentiation?.title_gaps?.length > 0 && (
        <div className="mt-2"><b>Angles:</b> {b.differentiation.title_gaps.map((g: string) => <TagChip key={g}>💡 {g}</TagChip>)}</div>
      )}
      {b.narrative ? (
        <div className="mt-4 border-t border-stroke pt-4 text-[13.5px] leading-[1.65]"><Md text={b.narrative} /></div>
      ) : (
        <div className="mt-2"><Small>🧠 {b.narrative_source || ""}</Small></div>
      )}
    </div>
  );
}
