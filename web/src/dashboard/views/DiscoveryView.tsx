import { Button } from "@/components/ui/button";
import { fmt, type Any, type Job } from "@/lib/api";
import { ExportBtn, NoData, OppCard, OppHead, PageHead, Progress, Q, Rule, Small, SourceHealth, Stat, Stats, TagChip, Warn, Warnings } from "../primitives";

export function DiscoveryView({ job, onValidate }: { job: Job; onValidate: (phrase: string) => void }) {
  if (job.status === "failed") return <Warn><b>Discovery failed.</b> {job.message}</Warn>;
  if (!job.result) return <Progress job={job} />;
  const r = job.result, cards: Any[] = r.cards || [];
  return (
    <>
      <PageHead
        title="Discovery"
        meta={<>last {r.window} · {(r.categories || []).length} categories · {r.generated_at}</>}
        actions={cards.length ? <ExportBtn jobId={job.id} table="cards" label="Export CSV" /> : undefined}
      />
      <Warnings items={r.warnings} />
      {!cards.length && (
        <NoData>
          No opportunities cleared the bar in this window. The source panel below shows what answered and what did
          not — an empty result here means no demand was found, not that demand is zero.
        </NoData>
      )}
      {cards.map((c) => {
        const g = c.gap || {}, tag = (g.verdict || "VERIFY").split(" ")[0];
        return (
          <OppCard key={c.concept}>
            <OppHead
              title={c.concept}
              sub={
                <Small>
                  {c.audience || "general"} · {c.category || ""} ·{" "}
                  {c.corroborated ? <b>{c.sources.length} independent sources</b> : <span className="text-gold">single source — treat with caution</span>}
                </Small>
              }
              right={<Q tag={tag} />}
            />
            <Stats>
              <Stat label="Demand">{c.demand}</Stat>
              <Stat label="Gap score">{g.score == null ? "—" : g.score}</Stat>
              <Stat label="Competing books">{fmt((g.supply || {}).total_results)}</Stat>
              <Stat label="Median reviews">{fmt((g.supply || {}).median_reviews)}</Stat>
            </Stats>
            <Small>{g.basis || ""}</Small>
            <div className="my-2">{c.sources.map((x: string) => <TagChip key={x}>{x}</TagChip>)}</div>
            <details className="my-2 mb-3">
              <summary className="cursor-pointer text-[12px] tracking-[0.02em] text-muted-foreground hover:text-ink">Receipts ({c.evidence.length})</summary>
              {c.evidence.map((e: Any, i: number) => (
                <div key={i} className="border-t border-stroke py-2 text-[12.5px]">
                  <a className="border-b border-stroke-strong text-ink no-underline hover:text-gilt" href={e.url} target="_blank" rel="noopener">{(e.text || "").slice(0, 140)}</a>{" "}
                  <Small>{e.source}{e.subreddit ? " · r/" + e.subreddit : ""}</Small>
                </div>
              ))}
            </details>
            {/* The phrase lives in React state, never in an onclick attribute:
                concepts come from Amazon titles and Reddit posts. */}
            <Button size="sm" onClick={() => onValidate(c.validate_phrase)}>Run full validation</Button>
          </OppCard>
        );
      })}
      <Rule>Source health</Rule>
      <SourceHealth sources={r.sources || {}} />
      <p><Small>{r.momentum_note || ""}</Small></p>
    </>
  );
}
