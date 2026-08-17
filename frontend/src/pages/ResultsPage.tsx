import { useCallback, useEffect, useState } from "react";
import { useAtomValue } from "jotai";
import {
  ChevronLeft, ChevronRight, Loader2, Save, AlertTriangle, Eye, EyeOff,
  LayoutGrid, Rows,
} from "lucide-react";
import { toast } from "sonner";
import { api, type Block, type Project } from "@/lib/api";
import { jobAtom, jobRunningAtom } from "@/state/atoms";
import { PageGrid, fromJobResults } from "@/components/PageGrid";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { Label } from "@/components/ui/label";

/** Mirrors the backend's needs_review reasons so the UI can say *why*. */
function reviewReasons(block: Block): string[] {
  const reasons: string[] = [];
  const text = block.target_text;

  // Latin words left untranslated. Screams like "AAAAIIIIEEE" reuse very few
  // letters across many characters; real words spend a new letter almost
  // every position -- same rule the backend applies.
  const words = text.match(/(?<![A-Za-z])[A-Za-z]{2,}(?![A-Za-z])/g) ?? [];
  const leftovers = words.filter((w) => {
    if (w.length < 4) return true;
    return new Set(w.toLowerCase()).size / w.length >= 0.5;
  });
  if (/[㐀-鿿]/.test(text) && leftovers.length) {
    reasons.push(`未译：${leftovers.join(" ")}`);
  }

  if (block.source_conf > 0 && block.source_conf < 0.6) {
    reasons.push(`识别置信度低 ${block.source_conf.toFixed(2)}`);
  }
  if (!text.trim()) reasons.push("译文为空");
  return reasons;
}

export function ResultsPage({ projectPath }: { projectPath: string }) {
  const [project, setProject] = useState<Project | null>(null);
  const [pageIndex, setPageIndex] = useState(0);
  const [nonce, setNonce] = useState(0);
  const [showOriginal, setShowOriginal] = useState(false);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [path, setPath] = useState(projectPath);
  const [gridView, setGridView] = useState(true);
  const job = useAtomValue(jobAtom);
  const running = useAtomValue(jobRunningAtom);

  const open = useCallback(async (target: string) => {
    if (!target) return;
    setBusy(true);
    try {
      const loaded = await api.openProject(target);
      setProject(loaded);
      setPageIndex(0);
      setDrafts({});
      setNonce((n) => n + 1);
    } catch (e) {
      toast.error("打开失败", { description: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    setPath(projectPath);
    if (projectPath) void open(projectPath);
  }, [projectPath, open]);

  const page = project?.pages[pageIndex];
  const blocks = page?.blocks.filter((b) => b.kind === "text_bubble" && b.source_text.trim()) ?? [];

  const applyEdit = async (block: Block) => {
    const next = drafts[block.id];
    if (next === undefined || next === block.target_text) return;
    setBusy(true);
    try {
      await api.updateBlock(pageIndex, block.id, { target_text: next });
      block.target_text = next;
      block.edited = true;
      // The rendered image is disposable; re-fetch rather than patch it.
      setNonce((n) => n + 1);
      setDrafts((prev) => {
        const copy = { ...prev };
        delete copy[block.id];
        return copy;
      });
    } catch (e) {
      toast.error("保存失败", { description: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(false);
    }
  };

  const saveProject = async () => {
    try {
      const result = await api.saveProject();
      toast.success("已保存", { description: result.saved });
    } catch (e) {
      toast.error("保存失败", { description: e instanceof Error ? e.message : String(e) });
    }
  };

  // While a run is going there is no project yet, but its finished pages are
  // already on disk. Showing them as they land is the difference between
  // watching a progress bar and watching the work.
  if (!project && job && job.results.length > 0) {
    const items = fromJobResults(job.results, job.total);
    return (
      <div className="flex h-full flex-col">
        <div className="border-border flex shrink-0 items-center justify-between gap-2 border-b px-5 py-2.5">
          <div className="flex items-center gap-2 text-sm">
            <Loader2 className={running ? "size-4 animate-spin" : "hidden"} />
            <span>
              翻译中 <span className="tabular-nums">{job.completed} / {job.total}</span>
            </span>
            <span className="text-muted-foreground text-xs">完成的页会陆续出现</span>
          </div>
        </div>
        <div className="min-h-0 flex-1">
          <PageGrid items={items} selected={-1} onSelect={() => {}} />
        </div>
      </div>
    );
  }

  if (!project) {
    return (
      <div className="h-full overflow-y-auto">
        <div className="mx-auto max-w-[1400px] p-5">
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base">打开项目</CardTitle>
            </CardHeader>
            <CardContent className="flex gap-2">
              <Input
                value={path}
                onChange={(e) => setPath(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && void open(path)}
                placeholder="out\project.cttproj"
                className="font-mono text-xs"
              />
              <Button onClick={() => void open(path)} disabled={busy || !path}>
                {busy ? <Loader2 className="size-4 animate-spin" /> : "打开"}
              </Button>
            </CardContent>
          </Card>
        </div>
      </div>
    );
  }

  const reviewCount = blocks.filter((b) => reviewReasons(b).length > 0).length;

  return (
    <div className="flex h-full flex-col">
      {/* Toolbar stays put; only the two panes below it scroll. */}
      <div className="border-border flex shrink-0 flex-wrap items-center justify-between gap-2 border-b px-5 py-2.5">
        <div className="flex items-center gap-2">
          <Button
            variant="secondary" size="icon" className="size-8"
            disabled={pageIndex === 0}
            onClick={() => { setPageIndex((i) => i - 1); setNonce((n) => n + 1); }}
          >
            <ChevronLeft className="size-4" />
          </Button>
          <span className="text-sm tabular-nums">
            {pageIndex + 1} / {project.pages.length}
          </span>
          <Button
            variant="secondary" size="icon" className="size-8"
            disabled={pageIndex >= project.pages.length - 1}
            onClick={() => { setPageIndex((i) => i + 1); setNonce((n) => n + 1); }}
          >
            <ChevronRight className="size-4" />
          </Button>
          {reviewCount > 0 && (
            <Badge variant="secondary" className="ml-1">
              <AlertTriangle className="mr-1 size-3" />
              {reviewCount} 处待确认
            </Badge>
          )}
        </div>

        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" onClick={() => setGridView((v) => !v)}>
            {gridView ? <Rows className="mr-1.5 size-3.5" /> : <LayoutGrid className="mr-1.5 size-3.5" />}
            {gridView ? "单页" : "缩略图"}
          </Button>
          <Button variant="ghost" size="sm" onClick={() => setShowOriginal((v) => !v)}>
            {showOriginal ? <Eye className="mr-1.5 size-3.5" /> : <EyeOff className="mr-1.5 size-3.5" />}
            {showOriginal ? "看成品" : "看原图"}
          </Button>
          <Button size="sm" variant="secondary" onClick={() => void saveProject()}>
            <Save className="mr-1.5 size-3.5" /> 保存项目
          </Button>
        </div>
      </div>

      {gridView && (
        <div className="min-h-0 flex-1">
          <PageGrid
            items={project.pages.map((p, i) => ({
              index: i,
              name: p.image_path.split(/[\\/]/).pop() ?? "",
              // The grid shows finished output, which for an opened project
              // means re-rendering; the render endpoint is per index.
              path: p.image_path,
              reviewCount: p.blocks.filter(
                (b) => b.kind === "text_bubble" && reviewReasons(b).length > 0,
              ).length,
              reused: false,
            }))}
            selected={pageIndex}
            onSelect={(i) => {
              setPageIndex(i);
              setNonce((n) => n + 1);
              setGridView(false);
            }}
          />
        </div>
      )}

      <div className={cn("grid min-h-0 flex-1 gap-3 p-3 lg:grid-cols-[1fr_380px]", gridView && "hidden")}>
        {/* Its own scroller: a long webtoon strip is 14000px tall, so the
            image has to scroll without dragging the whole layout with it. */}
        <Card className="bg-viewer min-h-0 overflow-auto py-0">
          <img
            key={`${pageIndex}-${showOriginal}-${nonce}`}
            src={api.renderUrl(pageIndex, showOriginal, nonce)}
            alt={`第 ${pageIndex + 1} 页`}
            className="mx-auto block max-w-full"
          />
        </Card>

        <Card className="flex min-h-0 flex-col">
          <CardHeader className="shrink-0 pb-2">
            <CardTitle className="text-base">
              对白 <span className="text-muted-foreground font-normal">({blocks.length})</span>
            </CardTitle>
          </CardHeader>
          <CardContent className="min-h-0 flex-1 p-0">
            <ScrollArea className="h-full">
              <div className="space-y-3 p-4 pt-0">
                {blocks.map((block) => {
                  const reasons = reviewReasons(block);
                  const draft = drafts[block.id] ?? block.target_text;
                  const changed = draft !== block.target_text;
                  return (
                    <div key={block.id} className="space-y-1.5">
                      <div className="flex items-center gap-1.5">
                        <Label className="text-muted-foreground font-mono text-[10px]">
                          {block.id}
                        </Label>
                        {block.edited && (
                          <Badge variant="outline" className="h-4 px-1 text-[10px] font-normal">
                            已改
                          </Badge>
                        )}
                        {reasons.map((r) => (
                          <Badge key={r} variant="secondary" className="h-4 px-1 text-[10px] font-normal">
                            {r}
                          </Badge>
                        ))}
                      </div>
                      <p className="text-muted-foreground text-xs leading-snug">
                        {block.source_text}
                      </p>
                      <textarea
                        value={draft}
                        onChange={(e) =>
                          setDrafts((prev) => ({ ...prev, [block.id]: e.target.value }))
                        }
                        onBlur={() => void applyEdit(block)}
                        rows={2}
                        className={`border-input bg-background w-full resize-y rounded-md border px-2 py-1.5 text-sm ${
                          reasons.length ? "border-warning/60" : ""
                        }`}
                      />
                      {changed && (
                        <Button
                          size="sm" variant="secondary" className="h-6 text-xs"
                          onClick={() => void applyEdit(block)}
                        >
                          应用并重出片
                        </Button>
                      )}
                      <Separator className="mt-2" />
                    </div>
                  );
                })}
              </div>
            </ScrollArea>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
