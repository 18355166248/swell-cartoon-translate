import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle, Check, Loader2, ChevronLeft, ChevronRight,
} from "lucide-react";
import { api, type JobPageResult } from "@/lib/api";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Button } from "@/components/ui/button";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";

export interface GridItem {
  index: number;
  name: string;
  path: string;
  reviewCount: number;
  reused: boolean;
  pending?: boolean;
}

export function fromJobResults(results: JobPageResult[], total: number): GridItem[] {
  const done: GridItem[] = results.map((r) => ({
    index: r.index,
    name: r.name,
    path: r.output_path,
    reviewCount: r.review_count,
    reused: r.reused,
  }));
  // Placeholders for what has not been reached yet, so the grid keeps its
  // shape while a run fills in and does not reflow on every completed page.
  for (let i = done.length; i < total; i++) {
    done.push({ index: i, name: "", path: "", reviewCount: 0, reused: false, pending: true });
  }
  return done;
}

const PAGE_SIZES = [30, 60, 120, 240];
const DEFAULT_PAGE_SIZE = 60;

export function PageGrid({
  items,
  selected,
  onSelect,
  thumbSize = 200,
}: {
  items: GridItem[];
  selected: number;
  onSelect: (index: number) => void;
  thumbSize?: number;
}) {
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
  const [page, setPage] = useState(0);

  const pageCount = Math.max(1, Math.ceil(items.length / pageSize));

  // Follow the selection across pages, so switching back from the single-page
  // view lands on the batch containing the page you were reading.
  useEffect(() => {
    if (selected < 0) return;
    const target = Math.floor(selected / pageSize);
    if (target !== page && target < pageCount) setPage(target);
    // Only when the selection or the page size changes -- not on every render,
    // which would fight the user's own paging.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected, pageSize]);

  useEffect(() => {
    if (page >= pageCount) setPage(Math.max(0, pageCount - 1));
  }, [page, pageCount]);

  // Only the current batch is built. Chapters run to hundreds of pages, and
  // `loading="lazy"` defers the *fetches* but not the elements -- the browser
  // still lays out every node, which is what makes a full grid crawl.
  const visible = useMemo(
    () => items.slice(page * pageSize, page * pageSize + pageSize),
    [items, page, pageSize],
  );

  const first = items.length === 0 ? 0 : page * pageSize + 1;
  const last = Math.min((page + 1) * pageSize, items.length);

  return (
    <div className="flex h-full flex-col">
      <ScrollArea className="min-h-0 flex-1">
        <div
          className="grid gap-2 p-2"
          style={{ gridTemplateColumns: `repeat(auto-fill, minmax(${thumbSize}px, 1fr))` }}
        >
          {visible.map((item) => (
            <button
              key={item.index}
              disabled={item.pending}
              onClick={() => onSelect(item.index)}
              className={cn(
                "group border-border bg-viewer relative overflow-hidden rounded-md border text-left transition",
                "focus-visible:ring-ring focus-visible:ring-2 focus-visible:outline-none",
                selected === item.index && "ring-primary ring-2",
                item.pending ? "cursor-default opacity-40" : "hover:border-primary/60",
              )}
              style={{ aspectRatio: "3 / 2" }}
            >
              {item.pending ? (
                <div className="text-muted-foreground flex h-full items-center justify-center">
                  <Loader2 className="size-4 animate-spin opacity-50" />
                </div>
              ) : (
                <img
                  loading="lazy"
                  decoding="async"
                  src={api.thumbnailUrl(item.path, thumbSize * 2)}
                  alt={item.name}
                  className="h-full w-full object-contain"
                />
              )}

              <div className="absolute inset-x-0 bottom-0 flex items-center justify-between gap-1 bg-black/55 px-1.5 py-0.5 text-[10px] text-white">
                <span className="truncate">{item.index + 1}</span>
                <span className="flex shrink-0 items-center gap-1">
                  {item.reused && <span className="opacity-70">已有</span>}
                  {item.reviewCount > 0 ? (
                    <span className="flex items-center gap-0.5 text-amber-300">
                      <AlertTriangle className="size-2.5" />
                      {item.reviewCount}
                    </span>
                  ) : (
                    !item.pending && <Check className="size-2.5 text-emerald-300" />
                  )}
                </span>
              </div>
            </button>
          ))}
        </div>
      </ScrollArea>

      {items.length > PAGE_SIZES[0] && (
        <div className="border-border flex shrink-0 flex-wrap items-center justify-between gap-2 border-t px-3 py-2 text-xs">
          <span className="text-muted-foreground tabular-nums">
            第 {first}–{last} 张，共 {items.length} 张
          </span>

          <div className="flex items-center gap-2">
            <Select
              value={String(pageSize)}
              onValueChange={(v) => {
                setPageSize(Number(v));
                setPage(0);
              }}
            >
              <SelectTrigger className="h-7 w-24 text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {PAGE_SIZES.map((n) => (
                  <SelectItem key={n} value={String(n)}>
                    每页 {n}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            <Button
              variant="secondary" size="icon" className="size-7"
              disabled={page === 0}
              onClick={() => setPage((p) => p - 1)}
            >
              <ChevronLeft className="size-3.5" />
            </Button>
            <span className="tabular-nums">
              {page + 1} / {pageCount}
            </span>
            <Button
              variant="secondary" size="icon" className="size-7"
              disabled={page >= pageCount - 1}
              onClick={() => setPage((p) => p + 1)}
            >
              <ChevronRight className="size-3.5" />
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
