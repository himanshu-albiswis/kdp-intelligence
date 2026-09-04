import type { ComponentProps, ReactNode } from "react";
import { cn } from "@/lib/utils";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import type { Any, Job } from "@/lib/api";

/* Building blocks shared by every dashboard view. They carry the product's
   glass language (Instrument Serif display, Plex Mono numbers, uppercase
   section rules) on top of shadcn's primitives. */

export function Rule({ children, hint }: { children: ReactNode; hint?: ReactNode }) {
  return (
    <h3 className="rule">
      {children}
      {hint && <Small className="normal-case tracking-normal">({hint})</Small>}
    </h3>
  );
}

export function Small({ children, className }: { children: ReactNode; className?: string }) {
  return <span className={cn("text-[12.5px] font-normal text-muted-foreground", className)}>{children}</span>;
}

export function Warn({ children }: { children: ReactNode }) {
  return <div className="warnbox">⚠️ {children}</div>;
}

export function Warnings({ items }: { items?: string[] }) {
  return (
    <>
      {(items || []).map((w, i) => (
        <Warn key={i}>{w}</Warn>
      ))}
    </>
  );
}

export function Kpi({ value, label, bad, mono, className }: {
  value: ReactNode; label: ReactNode; bad?: boolean; mono?: boolean; className?: string;
}) {
  return (
    <div className={cn("glass-strong flex flex-col justify-end gap-1 p-4 transition-colors hover:border-stroke-strong", className)}>
      <div className={cn("display text-[27px] leading-[1.05] tabular-nums", mono && "font-mono text-[21px] font-medium tracking-tight", bad && "text-risk")}>
        {value}
      </div>
      <div className="text-[11px] leading-[1.4] tracking-[0.02em] text-muted-foreground">{label}</div>
    </div>
  );
}

export function Kpis({ children }: { children: ReactNode }) {
  return <div className="my-4 grid grid-cols-[repeat(auto-fit,minmax(168px,1fr))] items-stretch gap-3">{children}</div>;
}

export function TagChip({ children, href, offpage }: { children: ReactNode; href?: string; offpage?: boolean }) {
  const cls = cn("tagchip", offpage && "offpage");
  return href ? (
    <a className={cls} href={href} target="_blank" rel="noopener">{children}</a>
  ) : (
    <span className={cls}>{children}</span>
  );
}

export function Q({ tag, children }: { tag: string; children?: ReactNode }) {
  return <span className={cn("q", `q-${tag}`)}>{children ?? tag}</span>;
}

export function Chip({ status, children }: { status: string; children: ReactNode }) {
  return <span className={cn("chip", `st-${status}`)}>{children}</span>;
}

export function TableWrap({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className="overflow-x-auto rounded-[10px] border border-stroke bg-glass-strong">
      <table className={cn("datatable", className)}>{children}</table>
    </div>
  );
}

export function NoData({ children }: { children: ReactNode }) {
  return <div className="nodata">{children}</div>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="px-6 py-[14vh] text-center text-[14px] text-muted-foreground">{children}</div>;
}

export function Bar({ pct, className }: { pct: number; className?: string }) {
  return (
    <div className={cn("mt-2 h-1 overflow-hidden rounded-full bg-stroke", className)}>
      <i className="block h-full rounded-full bg-gilt transition-[width] duration-300" style={{ width: `${pct}%` }} />
    </div>
  );
}

export function Progress({ job }: { job: Job }) {
  return (
    <Empty>
      <b>{job.seed}</b> — {job.stage || "queued"}…
      <Bar pct={job.pct} className="mx-auto my-4 max-w-[340px]" />
      <Small>{job.message}</Small>
    </Empty>
  );
}

export function ExportBtn({ jobId, table, label }: { jobId: string; table: string; label: string }) {
  return (
    <a
      className="inline-flex h-8 items-center rounded-[10px] border border-stroke-strong px-3 text-[12px] font-semibold text-gilt no-underline hover:border-gilt"
      href={`/api/research/${jobId}/csv?table=${table}`}
      download
    >
      ⬇ {label}
    </a>
  );
}

export function PageHead({ title, meta, actions }: { title: ReactNode; meta?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-4 flex flex-wrap items-start justify-between gap-4">
      <h2 className="display m-0 flex-[1_1_320px] text-[26px] leading-[1.2]">
        {title} {meta && <Small>· {meta}</Small>}
      </h2>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

/* ---- form controls in the glass style ------------------------------- */

export function Field(props: ComponentProps<typeof Input>) {
  return (
    <Input
      {...props}
      className={cn(
        "h-[38px] rounded-[10px] border-stroke-strong bg-glass-inset text-[13.5px] focus-visible:border-gilt focus-visible:ring-[3px] focus-visible:ring-gilt/20",
        props.className,
      )}
    />
  );
}

export function Area(props: ComponentProps<typeof Textarea>) {
  return (
    <Textarea
      {...props}
      className={cn(
        "rounded-[10px] border-stroke-strong bg-glass-inset font-mono text-[13px] leading-relaxed focus-visible:border-gilt focus-visible:ring-[3px] focus-visible:ring-gilt/20",
        props.className,
      )}
    />
  );
}

export function Select(props: ComponentProps<"select">) {
  return <select {...props} className={cn("field", props.className)} />;
}

export function Lbl({ children, hint, htmlFor }: { children: ReactNode; hint?: ReactNode; htmlFor?: string }) {
  return (
    <label className="lbl" htmlFor={htmlFor}>
      {children} {hint && <Small>{hint}</Small>}
    </label>
  );
}

export function Row2({ children }: { children: ReactNode }) {
  return <div className="grid grid-cols-2 gap-3">{children}</div>;
}

/* ---- result blocks ---------------------------------------------------- */

export function Stat({ label, children }: { label: ReactNode; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-[2px]">
      <span className="text-[10px] uppercase tracking-[0.08em] text-faint">{label}</span>
      <b className="font-mono text-[17px]">{children}</b>
    </div>
  );
}

export function Stats({ children }: { children: ReactNode }) {
  return (
    <div className="my-3 grid grid-cols-[repeat(auto-fit,minmax(120px,1fr))] gap-3 border-y border-stroke py-3">
      {children}
    </div>
  );
}

export function OppCard({ children }: { children: ReactNode }) {
  return <div className="mb-3 rounded-2xl border border-stroke bg-glass-strong p-4 shadow-[inset_0_1px_0_var(--glass-inset)]">{children}</div>;
}

export function OppHead({ title, sub, right }: { title: ReactNode; sub?: ReactNode; right?: ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-3">
      <div>
        <div className="display text-[20px] leading-[1.25]">{title}</div>
        {sub}
      </div>
      {right}
    </div>
  );
}

export function Note({ children, tone = "risk", className }: { children: ReactNode; tone?: "risk" | "money" | "info"; className?: string }) {
  return (
    <div className={cn("note", className)} style={{ borderLeftColor: `var(--${tone})` }}>
      {children}
    </div>
  );
}

export function KV({ rows }: { rows: [ReactNode, ReactNode][] }) {
  return (
    <table className="kv">
      <tbody>
        {rows.map(([k, v], i) => (
          <tr key={i}>
            <th>{k}</th>
            <td>{v}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function Cell({ v }: { v: unknown }) {
  return v == null || v === "" ? <span className="text-faint">—</span> : <>{String(v)}</>;
}

/** Source health cards, shared by Trend Radar and Discovery. */
export function SourceHealth({ sources, transport }: { sources: Record<string, Any>; transport?: boolean }) {
  return (
    <div className="my-3 grid grid-cols-[repeat(auto-fit,minmax(230px,1fr))] gap-3">
      {Object.values(sources || {}).map((x: Any, i) => (
        <div
          key={i}
          className="glass-strong flex flex-col gap-1 px-4 py-3"
          style={{ boxShadow: `inset 3px 0 0 var(--${{ ok: "money", unavailable: "risk", not_configured: "info" }[x.status as string] || "stroke-strong"})` }}
        >
          <div className="flex items-center justify-between gap-2 text-[13px]">
            <b>{x.name}</b> <Q tag={x.status}>{x.status}</Q>
          </div>
          {transport && (
            <Small>
              {x.transport ? x.transport + " · " : ""}
              {x.item_count} item(s)
            </Small>
          )}
          <Small>{x.detail || ""}</Small>
        </div>
      ))}
    </div>
  );
}

/** The narrative comes back as light markdown: headings, bold, paragraphs. */
export function Md({ text }: { text: string }) {
  const blocks = text.split(/\n\n+/);
  return (
    <>
      {blocks.map((block, i) => {
        const heading = block.match(/^#{1,3}\s+(.+)$/m);
        if (heading && block.trim().startsWith("#")) {
          const rest = block.replace(/^#{1,3}\s+.+$/m, "").trim();
          return (
            <div key={i}>
              <h4 className="mb-1 mt-4 text-[12px] uppercase tracking-[0.09em] text-faint">{heading[1]}</h4>
              {rest && <p className="m-0">{bold(rest)}</p>}
            </div>
          );
        }
        return <p key={i} className="my-2">{bold(block)}</p>;
      })}
    </>
  );
}

function bold(s: string): ReactNode[] {
  return s.split(/\*\*(.+?)\*\*/g).map((part, i) => (i % 2 ? <b key={i}>{part}</b> : <span key={i}>{part}</span>));
}
