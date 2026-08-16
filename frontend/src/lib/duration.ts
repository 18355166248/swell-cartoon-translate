/** Duration and clock-time formatting for job progress. */

/**
 * A span of seconds as "1小时23分" / "23分" / "45秒".
 *
 * Hours only appear when there are hours -- a run that has 12 minutes left
 * should not read "0小时12分". Seconds only appear below a minute, where they
 * are the only thing that changes.
 */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return "—";
  const total = Math.max(0, Math.round(seconds));

  if (total < 60) return `${total}秒`;

  const hours = Math.floor(total / 3600);
  const minutes = Math.round((total % 3600) / 60);

  if (hours === 0) return `${minutes}分`;
  // Rounding can push minutes to 60; carry it rather than print "1小时60分".
  if (minutes === 60) return `${hours + 1}小时`;
  return minutes === 0 ? `${hours}小时` : `${hours}小时${minutes}分`;
}

/**
 * Wall-clock time when a run with `remaining` seconds left will finish.
 *
 * Answers the question people actually have -- "can I go do something else?"
 * -- which a bare countdown does not. The day marker matters: a three-hour
 * job started at 23:00 finishing at "02:00" is confusing without it.
 */
export function formatFinishTime(remaining: number | null | undefined, now = new Date()): string {
  if (remaining === null || remaining === undefined || !Number.isFinite(remaining)) return "—";

  const finish = new Date(now.getTime() + Math.max(0, remaining) * 1000);
  const clock = finish.toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });

  const days = dayDifference(now, finish);
  if (days === 0) return clock;
  if (days === 1) return `明天 ${clock}`;
  return `${days}天后 ${clock}`;
}

function dayDifference(from: Date, to: Date): number {
  const a = new Date(from.getFullYear(), from.getMonth(), from.getDate());
  const b = new Date(to.getFullYear(), to.getMonth(), to.getDate());
  return Math.round((b.getTime() - a.getTime()) / 86_400_000);
}

/** "还需 1小时23分 · 预计 15:42 完成" */
export function formatRemaining(remaining: number | null | undefined, now = new Date()): string {
  if (remaining === null || remaining === undefined || !Number.isFinite(remaining)) {
    return "预计时间计算中";
  }
  return `还需 ${formatDuration(remaining)} · 预计 ${formatFinishTime(remaining, now)} 完成`;
}
