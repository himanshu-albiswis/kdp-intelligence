import { fmt, type Any, type Job } from "@/lib/api";
import { Note, PageHead, Progress, Q, Rule, Small, SourceHealth, TableWrap, TagChip, Warn, Warnings } from "../primitives";

export function TrendsView({ job }: { job: Job }) {
  if (job.status === "failed") return <Warn><b>Scan failed.</b> {job.message}</Warn>;
  if (!job.result) return <Progress job={job} />;
  const r = job.result, src: Record<string, Any> = r.sources || {};
  const scoreBar = (pct: number) => (
    <span className="ml-2 inline-block h-1 w-11 overflow-hidden rounded-full bg-stroke align-middle">
      <i className="block h-full rounded-full bg-money" style={{ width: `${pct}%` }} />
    </span>
  );
  return (
    <>
      <PageHead
        title={<>📡 Trend Radar: {r.topic ? `“${r.topic}”` : "all categories"}</>}
        meta={<>Amazon {r.marketplace.toUpperCase()} cross-check · {r.generated_at}</>}
      />
      <Warnings items={r.warnings} />
      <Rule hint="demand breadth × live Amazon shelf">Validated opportunities</Rule>
      <TableWrap>
        <thead><tr><th>Quadrant</th><th>Phrase</th><th>Sources</th><th>Amazon results</th><th>Med rev</th><th>In titles</th><th>Score</th></tr></thead>
        <tbody>
          {(r.validated || []).map((v: Any) => {
            const q = v.quadrant.split(" —")[0];
            return (
              <tr key={v.phrase}>
                <td><Q tag={q} /><div><Small>{v.quadrant.split("— ")[1] || ""}</Small></div></td>
                <td>{v.url ? <a href={v.url} target="_blank" rel="noopener">{v.phrase}</a> : v.phrase}</td>
                <td>{v.sources.map((s: string) => <TagChip key={s}>{s}</TagChip>)}</td>
                <td className="num">{fmt(v.total_results)}</td>
                <td className="num">{v.median_reviews != null ? Math.round(v.median_reviews) : "—"}</td>
                <td className="num">{v.phrase_in_titles ?? "—"}</td>
                <td className="num">{v.opportunity != null ? <><b>{v.opportunity}</b>{scoreBar(v.opportunity)}</> : "—"}</td>
              </tr>
            );
          })}
        </tbody>
      </TableWrap>

      <Rule>All mined candidates ({(r.candidates || []).length})</Rule>
      <div>{(r.candidates || []).map((c: Any) => <TagChip key={c.phrase}>{c.phrase} <Small>×{c.breadth}</Small></TagChip>)}</div>

      <Rule hint="what answered, what did not, and why">Source health</Rule>
      <SourceHealth sources={src} transport />

      <Rule>Raw source signals</Rule>
      <div className="grid grid-cols-2 gap-3 max-md:grid-cols-1">
        <div><b>Google asks</b><div><Small>{(src.google?.items || []).slice(0, 15).map((i: Any) => i.phrase).join(" · ") || "—"}</Small></div></div>
        <div><b>YouTube asks</b><div><Small>{(src.youtube?.items || []).slice(0, 15).map((i: Any) => i.phrase).join(" · ") || "—"}</Small></div></div>
      </div>

      {src.openlibrary?.meta?.total_works != null && (
        <>
          <Rule hint="OpenLibrary — independent of Amazon">Catalogue supply</Rule>
          <Small>{fmt(src.openlibrary.meta.total_works)} catalogued works · published {src.openlibrary.meta.oldest_year || "?"}–{src.openlibrary.meta.newest_year || "?"}</Small>
        </>
      )}

      {src.reddit?.items?.length > 0 && (
        <>
          <Rule hint={src.reddit.transport || ""}>Reddit threads</Rule>
          {src.reddit.items.slice(0, 8).map((p: Any, i: number) => (
            <Note key={i} tone="info">
              {p.url ? <a href={p.url} target="_blank" rel="noopener">{p.title}</a> : p.title}
              <div><Small>r/{p.subreddit || "?"} · {p.score != null ? `${fmt(p.score)} upvotes · ${fmt(p.comments)} comments` : "engagement not available over RSS"}</Small></div>
            </Note>
          ))}
        </>
      )}

      <Rule>Deliberately excluded</Rule>
      {Object.values(src).filter((x: Any) => x.status === "excluded").map((x: Any) => (
        <Warn key={x.name}><b>{x.name}</b> — {x.detail}</Warn>
      ))}
    </>
  );
}
