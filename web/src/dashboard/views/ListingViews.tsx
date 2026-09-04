import { cn } from "@/lib/utils";
import type { Any } from "@/lib/api";
import { fmt } from "@/lib/api";
import { Kpi, Kpis, KV, OppCard, OppHead, PageHead, Q, Small, TagChip, Warnings } from "../primitives";

export function Issues({ g }: { g: Any }) {
  return (
    <>
      <Kpis>
        <Kpi
          bad={!g.passed}
          value={<>{g.score}<span className="text-[11px] text-muted-foreground"> /100</span></>}
          label={<>{g.passed ? "passes KDP's metadata rules" : `${g.errors} error(s) KDP will reject`} · {g.warnings} warning(s)</>}
        />
      </Kpis>
      {g.issues.length ? (
        <table className="kv">
          <tbody>
            {g.issues.map((i: Any, n: number) => (
              <tr key={n}>
                <th className={cn(i.severity === "error" ? "text-risk" : "text-gold")}>{i.severity} · {i.field}</th>
                <td>{i.message}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <Small>No issues found.</Small>
      )}
    </>
  );
}

export function ListingCheckView({ g }: { g: Any }) {
  return (
    <>
      <PageHead title="Listing check" />
      <Issues g={g} />
    </>
  );
}

export function TranslateView({ out }: { out: Any }) {
  return (
    <>
      <PageHead title="Translated listings" />
      <Warnings items={out.warnings} />
      {(out.packs || []).map((p: Any) => (
        <OppCard key={p.marketplace}>
          <OppHead
            title={p.title}
            sub={<Small>{p.subtitle || ""}</Small>}
            right={<Q tag={p.translated ? "GO" : "VERIFY"}>{p.marketplace.toUpperCase()} · {p.language}</Q>}
          />
          <KV
            rows={[
              ["Keywords", (p.keywords || []).map((k: string) => <TagChip key={k}>{k}</TagChip>)],
              ["Description", <Small>{String(p.description || "").replace(/<[^>]+>/g, " ").slice(0, 600)}</Small>],
              ["KDP check", <>{p.guidelines.score}/100 · {p.guidelines.errors} error(s), {p.guidelines.warnings} warning(s)</>],
            ]}
          />
        </OppCard>
      ))}
    </>
  );
}

export function CalcView({ r }: { r: Any }) {
  const feas = r.feasibility || {};
  return (
    <>
      <PageHead title={<>Break-even for {r.format} @ ${r.price}</>} />
      <div className="max-w-[600px] rounded-2xl border border-stroke-strong bg-glass-strong p-6 shadow-[var(--shadow)]">
        <div>Royalty per sale <Small>({r.plan}{r.print_cost ? ` · printing $${r.print_cost}` : ""})</Small></div>
        <div className="font-mono text-[30px] font-semibold tracking-tight tabular-nums">${r.royalty_per_sale}</div>
        <div className="mt-[10px]">To earn <b>${fmt(r.goal_month)}/month</b>:</div>
        <div className="font-mono text-[30px] font-semibold tracking-tight tabular-nums">{r.sales_needed_per_day} sales/day</div>
        <Small>{fmt(r.sales_needed_per_month)} sales/month → required rank ≈ <b>BSR {fmt(r.bsr_needed)}</b></Small>
        {feas.read && (
          <div className="mt-3 rounded-[10px] border border-stroke bg-glass p-4 text-[13.5px] leading-[1.7]">
            {feas.read}<br />
            <Small>Niche BSRs: best {fmt(feas.niche_best_bsr)} · median {fmt(feas.niche_median_bsr)} · worst {fmt(feas.niche_worst_bsr)}</Small>
          </div>
        )}
        {r.ku_note && <div className="mt-2"><Small>📖 {r.ku_note}</Small></div>}
        {(r.notes || []).map((n: string, i: number) => <div key={i}><Small>· {n}</Small></div>)}
      </div>
    </>
  );
}
