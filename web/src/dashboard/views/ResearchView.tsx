import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { api, fmt, type Any, type Job } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Bars, Donut, Line } from "../charts";
import { Empty, ExportBtn, Md, NoData, Panel, Progress, Q, Small, StatCard, TableWrap, TagChip, Warn, Warnings } from "../primitives";

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

  if (job.status === "failed") return <Panel><Warn><b>Job failed.</b> {job.message}</Warn></Panel>;
  if (!job.result) return <Panel><Progress job={job} /></Panel>;

  const r = job.result, s = r.summary || {}, cur: string = r.currency || "$";
  const kws: Any[] = [...(r.keywords || [])].sort((a, b) => ((a[sortKey] ?? -1) < (b[sortKey] ?? -1) ? sortDir : -sortDir));
  const money: Any[] = (r.book_intel || []).filter((b: Any) => b.est_monthly_royalty).sort((a: Any, b: Any) => b.est_monthly_royalty - a.est_monthly_royalty);
  const priceOk = r.currency_ok !== false;
  const inc = r.income;
  const focus = (r.keywords || []).find((k: Any) => k.keyword === r.focus_keyword) || (r.keywords || [])[0];
  const setSort = (k: string) => { setSortDir(sortKey === k ? -sortDir : -1); setSortKey(k); };

  // Sales/day of the deep-dived books in shelf order: the "revenue line".
  const salesLine = (r.book_intel || [])
    .filter((b: Any) => b.est_sales_per_day != null && b.bsr)
    .sort((a: Any, b: Any) => a.bsr - b.bsr)
    .slice(0, 8)
    .map((b: Any, i: number) => ({ label: `#${i + 1}`, value: b.est_sales_per_day, hint: `${b.est_sales_per_day}/day · ${(b.title || "").slice(0, 22)}` }));
  const bestSales = salesLine.length ? Math.max(...salesLine.map((p: Any) => p.value)) : null;
  const scoreBars = [...(r.keywords || [])]
    .sort((a: Any, b: Any) => b.opportunity - a.opportunity)
    .slice(0, 8)
    .map((k: Any) => ({ label: k.keyword.replace(r.seed, "").trim() || k.keyword, value: k.opportunity, hint: `${k.keyword}: ${k.opportunity}` }));

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
    <div className="flex flex-col gap-5">
      {(r.warnings || []).length > 0 && <Panel kebab={false}><Warnings items={r.warnings} /></Panel>}

      {/* ---- row 1: revenue-style line + two stat cards ---- */}
      <div className="grid grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)] gap-5 max-lg:grid-cols-1">
        <Panel
          title="Shelf demand"
          right={<Small>{r.marketplace.toUpperCase()} · {r.store || ""} · focus “{r.focus_keyword}”</Small>}
        >
          <div className="mb-2 flex items-start justify-between gap-3">
            <div className="flex items-center gap-4">
              <span className="circ">↗</span>
              <span className="stat">{bestSales != null ? `${bestSales} /day` : "—"}</span>
            </div>
            <Small className="text-right">best-selling page-1 book<br />est. sales per day, from BSR</Small>
          </div>
          {salesLine.length ? <Line data={salesLine} /> : <NoData>No deep-dived books carry a BSR yet.</NoData>}
        </Panel>
        <div className="grid grid-cols-1 gap-5">
          <StatCard
            title="Competing books"
            value={s.total_results != null ? "~" + fmt(s.total_results) : "?"}
            caption={<>{fmt(s.median_reviews)} median reviews · {s.books_scanned ?? (r.books || []).length} scanned</>}
          />
          <StatCard
            title="Income / month"
            value={inc && inc.books_counted ? `${cur}${fmt(Math.round(inc.total_low))}–${fmt(Math.round(inc.total_high))}` : "—"}
            caption={inc && inc.books_counted ? <>{inc.confidence} confidence{inc.ku_mid ? ` · includes ${cur}${fmt(Math.round(inc.ku_mid))} Kindle Unlimited` : ""}</> : inc?.basis || "not enough priced, ranked books"}
            down={!(inc && inc.books_counted)}
          />
        </div>
      </div>

      {/* ---- row 2: keyword score bars + donuts ---- */}
      <div className="grid grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)] gap-5 max-lg:grid-cols-1">
        <Panel title="Keyword opportunity" right={<ExportBtn jobId={job.id} table="keywords" label="Keywords CSV" />}>
          {scoreBars.length ? <Bars data={scoreBars} format={(v) => String(Math.round(v))} /> : <NoData>No keywords were scored.</NoData>}
        </Panel>
        <Panel title="Shelf shape">
          <div className="grid grid-cols-3 gap-2 max-sm:grid-cols-1">
            <Donut pct={s.ku_share_pct ?? null} label="Kindle Unlimited" size={96} />
            <Donut pct={focus?.pct_under_100 ?? null} label="Under 100 reviews" size={96} />
            <Donut pct={s.velocity_pct_90d ?? null} label="New in 90 days" size={96} />
          </div>
          <div className="mt-5 flex items-end justify-between gap-3">
            <div>
              <div className="text-[16px] font-medium text-ink">Niche brief</div>
              <Small>Gates, break-even and an AI narrative for “{r.seed}”.</Small>
            </div>
            <Button onClick={makeBrief} disabled={briefState === "loading"} className="h-11 rounded-full px-5">Generate</Button>
          </div>
        </Panel>
      </div>

      {briefState === "loading" && <Panel><Empty>Generating brief…</Empty></Panel>}
      {typeof briefState === "object" && <Panel><Warn>{briefState.error}</Warn></Panel>}
      {brief && briefState === "idle" && <Brief b={brief} cur={cur} />}

      {/* ---- keyword table ---- */}
      <Panel title="Keyword opportunities" right={<Small>{kws.length} phrases</Small>}>
        {!kws.length ? (
          <NoData>No keywords were scored. Amazon returned no usable results page for this seed, so nothing could be validated — the warnings above say why. Nothing here is a zero; it is an absence of data.</NoData>
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
                      <><b>{k.demand_index}</b>{" "}
                        <span className={cn("ml-1 inline-block rounded-full px-[7px] py-[1px] align-middle text-[9.5px] font-semibold uppercase tracking-[0.05em]", `d-${String(k.demand_band).replace(" ", "-")}`)}>{k.demand_band}</span></>
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
      </Panel>

      {money.length > 0 && (
        <Panel title="Money proof" right={<ExportBtn jobId={job.id} table="book_intel" label="Money CSV" />}>
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
        </Panel>
      )}

      {r.category_intel?.picks?.length > 0 && (
        <Panel title="Category picks" right={<ExportBtn jobId={job.id} table="categories" label="Categories CSV" />}>
          <div className="grid grid-cols-[repeat(auto-fit,minmax(220px,1fr))] gap-3">
            {r.category_intel.picks.map((p: Any) => (
              <div key={p.category} className="flex flex-col gap-2 rounded-[16px] bg-card-2 p-4">
                <div className="flex items-baseline justify-between gap-2">
                  <b className="font-medium">{p.category}</b>
                  <span className="chip">{p.exact ? "exact read" : "floor"}</span>
                </div>
                <div className="text-[20px] font-medium tabular-nums text-ink">{p.entry_sales_day != null ? `${p.entry_sales_day}+ sales/day for #1` : "entry bar unknown"}</div>
                <Small>{p.why}</Small>
              </div>
            ))}
          </div>
          <div className="mt-3"><Small>{r.category_intel.note || ""}</Small></div>
        </Panel>
      )}

      {r.pricing?.priced ? (
        <Panel title="Pricing on page 1" right={<Small>bands follow KDP's 70% window</Small>}>
          <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)] gap-5 max-lg:grid-cols-1">
            <div className="grid grid-cols-1 gap-3">
              <div className="rounded-[16px] bg-card-2 p-4"><div className="stat text-[28px]">{cur}{r.pricing.median}</div><Small>median price · {cur}{r.pricing.q1}–{cur}{r.pricing.q3} middle half</Small></div>
              <div className="rounded-[16px] bg-card-2 p-4"><div className="text-[20px] font-medium">{r.pricing.sweet_spot ? r.pricing.sweet_spot.label : "—"}</div><Small>sweet spot{r.pricing.sweet_spot ? " · " + r.pricing.sweet_spot.why : ""}</Small></div>
              <div className="rounded-[16px] bg-card-2 p-4"><div className="text-[20px] font-medium">{r.pricing.price_rank_relation.direction}</div><Small>price vs rank{r.pricing.price_rank_relation.rho != null ? " · ρ " + r.pricing.price_rank_relation.rho : ""}</Small></div>
            </div>
            <Bars data={r.pricing.bands.map((b: Any) => ({ label: b.label, value: b.count, hint: `${b.label}: ${b.count} book(s)${b.median_bsr ? " · median BSR #" + fmt(b.median_bsr) : ""}` }))} format={(v) => String(Math.round(v))} height={230} />
          </div>
        </Panel>
      ) : null}

      {(r.velocity?.books_measured || r.listing_benchmark?.books) ? (
        <div className="grid grid-cols-2 gap-5 max-lg:grid-cols-1">
          {r.velocity?.books_measured ? (
            <StatCard title="Review velocity" value={<>{r.velocity.median_per_month}<span className="text-[16px] text-muted-foreground"> /month</span></>}
              caption={<>median across {r.velocity.books_measured} dated books · shelf reads <Q tag={r.velocity.band} /></>} />
          ) : null}
          {r.listing_benchmark?.books ? (
            <StatCard title="Listing polish" value={<>{r.listing_benchmark.avg_score}<span className="text-[16px] text-muted-foreground"> /100</span></>} caption={r.listing_benchmark.read} />
          ) : null}
        </div>
      ) : null}

      {r.praise?.length > 0 && (
        <Panel title="What 4–5★ reviews reward" right={<Small>the features you can't skip</Small>}>
          {r.praise.map((p: Any) => (
            <div key={p.theme} className="note" style={{ borderLeftColor: "var(--bar-light)" }}>
              <div className="mb-1 font-medium text-ink">{p.theme} <Small>· {p.mentions} reviews</Small></div>
              {p.example}
            </div>
          ))}
        </Panel>
      )}

      {r.also_viewed?.neighbours?.length > 0 && (
        <Panel title="Where buyers drift" right={<Small>customers also viewed</Small>}>
          <div className="mb-3"><Small>{r.also_viewed.read}</Small></div>
          <div>
            {r.also_viewed.neighbours.slice(0, 16).map((n: Any) => (
              <TagChip key={n.asin} href={n.url} offpage={!n.on_page_1}>{n.title || n.asin} <Small>×{n.named_by}</Small></TagChip>
            ))}
          </div>
        </Panel>
      )}

      {(r.title_gaps?.length > 0 || r.title_ngrams?.length > 0) && (
        <Panel title="Titles on page 1">
          {r.title_gaps?.length > 0 && <><div className="rule mt-0">Title gaps</div><div>{r.title_gaps.map((g: string) => <TagChip key={g}>💡 {g}</TagChip>)}</div></>}
          {r.title_ngrams?.length > 0 && <><div className="rule">What titles are made of</div><div>{r.title_ngrams.map(([g, c]: [string, number]) => <TagChip key={g}>{g} ×{c}</TagChip>)}</div></>}
        </Panel>
      )}

      {r.complaints?.length > 0 && (
        <Panel title="What buyers complain about">
          {r.complaints.slice(0, 10).map((c: Any, i: number) => (
            <div key={i} className="note">
              <div className="mb-1 font-medium text-ink">{"★".repeat(Math.round(c.rating))} {c.title || "(no title)"} <Small>— “{c.book_title}”</Small></div>
              {c.body}
            </div>
          ))}
        </Panel>
      )}

      {r.books?.length > 0 && (
        <Panel title={`Books in the niche (${r.books.length})`}>
          <div className="grid grid-cols-[repeat(auto-fill,minmax(260px,1fr))] gap-3">
            {r.books.slice(0, 40).map((b: Any) => (
              <a key={b.asin || b.url} className="flex gap-3 rounded-[16px] bg-card-2 p-3 text-inherit no-underline transition-colors hover:bg-card-3" href={b.url} target="_blank" rel="noopener">
                {b.image_url ? <img loading="lazy" src={b.image_url} alt="" className="h-[72px] w-12 shrink-0 rounded-md bg-card-3 object-cover grayscale" /> : <div className="h-[74px] w-[50px]" />}
                <div>
                  <div className="mb-1 text-[12.5px] font-medium leading-[1.35]">{(b.title || "").slice(0, 72)}</div>
                  <div className="text-[11.5px] text-muted-foreground">
                    {b.kindle_unlimited && <span className="mr-1 rounded bg-white px-[5px] py-[1px] text-[8.5px] font-bold text-black">KU</span>}
                    {b.price != null && priceOk ? cur + b.price.toFixed(2) : b.kindle_unlimited ? "KU only" : "—"} · {b.rating ?? "—"}★ · {fmt(b.reviews)} rev
                  </div>
                </div>
              </a>
            ))}
          </div>
        </Panel>
      )}
    </div>
  );
}

function ScoreBar({ pct }: { pct: number }) {
  return (
    <span className="ml-2 inline-block h-1.5 w-11 overflow-hidden rounded-full bg-card-3 align-middle">
      <i className="block h-full rounded-full bg-bar-light" style={{ width: `${pct}%` }} />
    </span>
  );
}

function Brief({ b, cur }: { b: Any; cur: string }) {
  const be = b.breakeven || {}, feas = be.feasibility || {};
  return (
    <Panel title="Niche brief" right={<Q tag={{ VALIDATE: "GO", BORDERLINE: "ANGLE", SKIP: "AVOID" }[b.verdict as string] || "VERIFY"}>{b.verdict}</Q>}>
      <Small>
        {b.verdict_reason} · goal {cur}{fmt(b.goal_month)}/mo · generated from live data
        {b.narrative_source && !String(b.narrative_source).startsWith("none") ? ` + AI narrative (${b.narrative_source})` : ""}
      </Small>
      <div className="mt-3">
        {(b.gates || []).map((g: Any) => (
          <div key={g.gate} className="flex items-baseline gap-3 border-b border-line py-2 text-[13px]">
            <span className="min-w-[64px]"><Q tag={{ PASS: "GO", FAIL: "AVOID" }[g.status as string] || "VERIFY"}>{g.status}</Q></span>
            <b className="font-medium">{g.gate}</b> <span className="text-muted-foreground">{g.detail}</span>
          </div>
        ))}
      </div>
      {be.royalty_per_sale ? (
        <div className="my-4 rounded-[16px] bg-card-2 p-4 text-[13.5px] leading-[1.7]">
          <b>Break-even at the niche's price ({cur}{be.price}):</b> {cur}{be.royalty_per_sale}/sale ({be.plan}) → <b>{be.sales_needed_per_day} sales/day</b> ({fmt(be.sales_needed_per_month)}/mo) → <b>BSR ≈ {fmt(be.bsr_needed)}</b> needed.
          <br />{feas.read || ""} {be.ku_note && <><br />{be.ku_note}</>}
        </div>
      ) : null}
      {b.ku_read && <div className="mt-[6px]"><Small>📖 {b.ku_read}</Small></div>}
      {b.differentiation?.title_gaps?.length > 0 && (
        <div className="mt-3"><b className="font-medium">Angles:</b> {b.differentiation.title_gaps.map((g: string) => <TagChip key={g}>💡 {g}</TagChip>)}</div>
      )}
      {b.narrative ? (
        <div className="mt-4 border-t border-line pt-4 text-[13.5px] leading-[1.65]"><Md text={b.narrative} /></div>
      ) : (
        <div className="mt-2"><Small>🧠 {b.narrative_source || ""}</Small></div>
      )}
    </Panel>
  );
}
