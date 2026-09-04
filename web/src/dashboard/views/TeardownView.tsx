import type { ReactNode } from "react";
import { fmt, type Any, type Job } from "@/lib/api";
import { Cell, ExportBtn, KV, OppCard, OppHead, PageHead, Progress, Q, Small, Stat, Stats, TagChip, Warn, Warnings } from "../primitives";

export function TeardownView({ job }: { job: Job }) {
  if (job.status === "failed") return <Warn><b>Teardown failed.</b> {job.message}</Warn>;
  if (!job.result) return <Progress job={job} />;
  const r = job.result, rows: Any[] = r.rows || [];
  return (
    <>
      <PageHead
        title="Reverse-ASIN teardown"
        meta={<>{rows.length} book(s) · {r.generated_at}</>}
        actions={<ExportBtn jobId={job.id} table="teardown" label="Export CSV" />}
      />
      <Warnings items={r.warnings} />
      {rows.map((b, i) => {
        const c = b.crowdedness || {}, tag = (c.verdict || "VERIFY").split(" ")[0];
        return (
          <OppCard key={b.asin || i}>
            <OppHead
              title={<Cell v={b.title} />}
              sub={
                <>
                  <div><Small><Cell v={b.subtitle} /></Small></div>
                  <div><Small><Cell v={b.author} />{b.credential ? <> · <b>{b.credential}</b></> : null}</Small></div>
                </>
              }
              right={<Q tag={tag} />}
            />
            {b.error && <Warn>{b.error}</Warn>}
            <Stats>
              <Stat label="BSR (Kindle)">{b.bsr ? fmt(b.bsr) : "—"}</Stat>
              <Stat label="Reviews">{b.reviews != null ? fmt(b.reviews) : "—"}</Stat>
              <Stat label="Rating">{b.rating != null ? b.rating + "★" : "—"}</Stat>
              <Stat label="Pages">{b.pages != null ? fmt(b.pages) : "—"}</Stat>
              <Stat label="Year"><Cell v={b.year} /></Stat>
              <Stat label="Competing books">{c.competing_books != null ? fmt(c.competing_books) : "—"}</Stat>
            </Stats>
            <KV
              rows={[
                ...(b.author_profile
                  ? [["Author", <>{b.author_profile.read}{b.author_profile.bio && <div className="mt-1"><Small>{b.author_profile.bio.slice(0, 240)}</Small></div>}</>] as [string, ReactNode]]
                  : []),
                ...(b.quality && b.quality.score != null
                  ? [["Listing polish", <><b className="font-mono">{b.quality.score}</b>/100 · {b.quality.aplus ? "A+ content" : "no A+"} · {fmt(b.quality.description_chars)} chars description{b.quality.series && b.quality.series.of > 1 ? ` · series (${b.quality.series.book} of ${b.quality.series.of})` : ""}</>] as [string, ReactNode]]
                  : []),
                ["Sub-niche", <Cell v={b.sub_niche} />],
                ["Positioning", <Cell v={b.positioning} />],
                ["Crowdedness", <><Cell v={c.verdict} />{c.phrase && <Small> — judged on “{c.phrase}”</Small>}</>],
                ["BSR status", <Small><Cell v={b.bsr_status} /></Small>],
                ["Formats", (b.formats || []).length ? (b.formats as string[]).map((f) => <TagChip key={f}>{f}</TagChip>) : <Cell v={null} />],
                ["Kindle ASIN", <span className="font-mono"><Cell v={b.asin} /></span>],
                ["Paperback ISBN", <span className="font-mono"><Cell v={b.paperback_isbn} /></span>],
                ["Source", <a className="border-b border-stroke-strong text-ink no-underline hover:text-gilt" href={b.source_url} target="_blank" rel="noopener">{b.source_url}</a>],
              ]}
            />
          </OppCard>
        );
      })}
    </>
  );
}
