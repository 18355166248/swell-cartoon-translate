import { AlertTriangle, Check, Loader2 } from "lucide-react";
import { api, type JobPageResult } from "@/lib/api";
import { ScrollArea } from "@/components/ui/scroll-area";
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
  return (
    <ScrollArea className="h-full">
      <div
        className="grid gap-2 p-2"
        style={{ gridTemplateColumns: `repeat(auto-fill, minmax(${thumbSize}px, 1fr))` }}
      >
        {items.map((item) => (
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
                // Loads on demand: a chapter is hundreds of pages and eager
                // loading would fetch every thumbnail at once.
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
  );
}
