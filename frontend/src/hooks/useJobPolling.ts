import { useEffect, useRef } from "react";
import { useAtom, useSetAtom } from "jotai";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { activeJobIdAtom, jobAtom, projectPathAtom } from "@/state/atoms";

/**
 * Keeps the active job in sync, mounted once at app level.
 *
 * Deliberately not inside RunPage: a poll loop that lives in a tab stops the
 * moment you switch away, so progress would freeze while you were reading the
 * config page and jump on your return. Hoisting it also means the tabs no
 * longer need `forceMount` to stay alive.
 *
 * Polls once a second. That is far finer than the ~36 seconds a page takes,
 * and the endpoint is a dictionary lookup.
 */
export function useJobPolling(onFinished?: (projectPath: string) => void) {
  const [job, setJob] = useAtom(jobAtom);
  const [activeJobId, setActiveJobId] = useAtom(activeJobIdAtom);
  const setProjectPath = useSetAtom(projectPathAtom);
  const notified = useRef("");

  const running = job?.status === "running" || job?.status === "pending";

  // Re-attach after a reload: the run lives on the backend, so a refresh
  // should rejoin it. Prefer the stored id, then fall back to whatever is
  // still running -- the id may be stale if the backend was restarted.
  useEffect(() => {
    if (job) return;
    let cancelled = false;

    void (async () => {
      try {
        if (activeJobId) {
          const stored = await api.getJob(activeJobId).catch(() => null);
          if (stored && !cancelled) {
            // A run that already finished must not re-fire its toast on every
            // reload, so mark it as already announced.
            notified.current =
              stored.status === "running" || stored.status === "pending" ? "" : stored.id;
            setJob(stored);
            return;
          }
        }
        const jobs = await api.listJobs();
        const live = jobs.find((j) => j.status === "running" || j.status === "pending");
        if (live && !cancelled) {
          notified.current = "";
          setActiveJobId(live.id);
          setJob(live);
        }
      } catch {
        /* backend unreachable; the banner already says so */
      }
    })();

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!job || !running) return;
    const timer = setInterval(async () => {
      try {
        setJob(await api.getJob(job.id));
      } catch {
        /* transient; the next tick retries */
      }
    }, 1000);
    return () => clearInterval(timer);
  }, [job, running, setJob]);

  // Completion side effects, fired once per job rather than on every poll.
  useEffect(() => {
    if (!job || running || notified.current === job.id) return;
    notified.current = job.id;

    if (job.status === "done") {
      toast.success(`翻译完成：${job.completed} 张`, { description: job.output_dir });
    } else if (job.status === "failed") {
      toast.error("任务失败", { description: job.error });
    } else if (job.status === "cancelled") {
      toast.info(`已取消，完成 ${job.completed} 张`);
    }

    if (job.project_path && job.status !== "failed") {
      setProjectPath(job.project_path);
      onFinished?.(job.project_path);
    }
  }, [job, running, onFinished, setProjectPath]);

  return { job, running };
}
