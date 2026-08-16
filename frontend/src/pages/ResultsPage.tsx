import { useCallback, useEffect, useState } from "react";
import {
  ChevronLeft, ChevronRight, Loader2, Save, AlertTriangle, Eye, EyeOff,
} from "lucide-react";
import { toast } from "sonner";
import { api, type Block, type Project } from "@/lib/api";
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

  if (!project) {
    return (
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
    );
  }

  const reviewCount = blocks.filter((b) => reviewReasons(b).length > 0).length;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
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
          <Button variant="ghost" size="sm" onClick={() => setShowOriginal((v) => !v)}>
            {showOriginal ? <Eye className="mr-1.5 size-3.5" /> : <EyeOff className="mr-1.5 size-3.5" />}
            {showOriginal ? "看成品" : "看原图"}
          </Button>
          <Button size="sm" variant="secondary" onClick={() => void saveProject()}>
            <Save className="mr-1.5 size-3.5" /> 保存项目
          </Button>
        </div>
      </div>

      <div className="grid gap-3 lg:grid-cols-[1fr_380px]">
        <Card className="overflow-hidden py-0">
          <div className="bg-black/40">
            <img
              key={`${pageIndex}-${showOriginal}-${nonce}`}
              src={api.renderUrl(pageIndex, showOriginal, nonce)}
              alt={`第 ${pageIndex + 1} 页`}
              className="max-h-[75vh] w-full object-contain"
            />
          </div>
        </Card>

        <Card className="flex flex-col">
          <CardHeader className="pb-2">
            <CardTitle className="text-base">
              对白 <span className="text-muted-foreground font-normal">({blocks.length})</span>
            </CardTitle>
          </CardHeader>
          <CardContent className="flex-1 p-0">
            <ScrollArea className="h-[68vh]">
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
                          reasons.length ? "border-amber-500/50" : ""
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
