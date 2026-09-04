import { fmt, type Any, type Job } from "@/lib/api";
import { NoData, PageHead, Progress, Small, TableWrap, Warn, Warnings } from "../primitives";

export function CategoryRows({ rows }: { rows: Any[] }) {
  if (!rows.length)
    return <NoData>Nothing here yet. Run a research scan (categories are recorded from every deep-dive) or crawl Amazon's bestseller tree.</NoData>;
  return (
    <TableWrap>
      <thead><tr><th>Category</th><th>Node</th><th>Seen</th><th>Best rank seen</th><th>Entry bar</th><th>From niches</th></tr></thead>
      <tbody>
        {rows.map((c) => (
          <tr key={c.name}>
            <td>{c.name}</td>
            <td className="font-mono">{c.node ? <a href={`https://www.amazon.com/zgbs/digital-text/${c.node}`} target="_blank" rel="noopener">{c.node}</a> : "—"}</td>
            <td className="num">{c.times_seen}</td>
            <td className="num">{c.best_observed_rank != null ? "#" + c.best_observed_rank : "—"}</td>
            <td className="num">{c.entry_sales_day != null ? c.entry_sales_day + "/day" : "—"}</td>
            <td><Small>{String(c.niches || "").split("|").filter(Boolean).slice(0, 3).join(", ")}</Small></td>
          </tr>
        ))}
      </tbody>
    </TableWrap>
  );
}

export function CategoriesView({ out, q }: { out: Any; q: string }) {
  return (
    <>
      <PageHead title="Category catalogue" meta={<>{fmt(out.count)} categories known{q ? ` · matching “${q}”` : ""}</>} />
      <CategoryRows rows={out.results || []} />
    </>
  );
}

export function CrawlView({ job }: { job: Job }) {
  if (job.status === "failed") return <Warn><b>Crawl failed.</b> {job.message}</Warn>;
  if (!job.result) return <Progress job={job} />;
  const r = job.result;
  return (
    <>
      <PageHead title="Category crawl" meta={<>{r.pages_parsed} pages read · catalogue now {fmt(r.catalogue_size)}</>} />
      <Warnings items={r.warnings} />
      <CategoryRows rows={r.rows || []} />
    </>
  );
}
