import { useCallback, useEffect, useState } from "react";
import { api, isFinished, type Job } from "@/lib/api";

/** The job list, refreshed on an interval. React reconciles the list, so
 *  a re-render no longer throws away focus the way innerHTML did. */
export function useJobs(intervalMs = 5000) {
  const [jobs, setJobs] = useState<Job[]>([]);
  const refresh = useCallback(async () => {
    try {
      setJobs(await api<Job[]>("/research"));
    } catch {
      /* the list is decorative; a failed poll is retried next tick */
    }
  }, []);
  useEffect(() => {
    refresh();
    const t = setInterval(refresh, intervalMs);
    return () => clearInterval(t);
  }, [refresh, intervalMs]);
  return { jobs, refresh };
}

/** One job, polled every 2.5s until it finishes. */
export function useJob(id: string | null) {
  const [job, setJob] = useState<Job | null>(null);
  useEffect(() => {
    setJob(null);
    if (!id) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      try {
        const j = await api<Job>("/research/" + id);
        if (!alive) return;
        setJob(j);
        if (!isFinished(j.status)) timer = setTimeout(tick, 2500);
      } catch (e) {
        if (alive)
          setJob({ id, seed: "", status: "failed", pct: 0, marketplace: "", message: String(e) });
      }
    };
    tick();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [id]);
  return job;
}
