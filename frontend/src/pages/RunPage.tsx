import { useCallback, useEffect, useState } from "react";
import {
  Play, Square, Loader2, CheckCircle2, XCircle, AlertTriangle, Search,
} from "lucide-react";
import { useAtom, useSetAtom } from "jotai";
import { toast } from "sonner";
import { api, type JobRequest, type PreviewResponse } from "@/lib/api";
import { formatDuration, formatFinishTime, formatRemaining } from "@/lib/duration";
import {
  activeJobIdAtom,
  inputDirAtom,
  jobAtom,
  limitAtom,
  outputDirAtom,
  outputEditedAtom,
  previewAtom,
  recursiveAtom,
} from "@/state/atoms";
import { Switch } from "@/components/ui/switch";
import { FolderPicker } from "@/components/FolderPicker";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";

const STAGE_LABELS: Record<string, string> = {
  detect: "检测",
  ocr: "识别",
  translate: "翻译",
  erase: "擦除",
  typeset: "排版",
  "loading models": "加载模型",
};

function PreviewPanel({ preview }: { preview: PreviewResponse }) {
  const { summary } = preview;
  return (
    <div className="border-border space-y-2 rounded-lg border p-3 text-sm">
      <div className="flex items-baseline justify-between gap-2">
        <p className="text-muted-foreground text-xs">
          试算结果（还没开始翻译）
        </p>
        <p className="text-muted-foreground text-xs">
          共扫描 {summary.total} 张
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        <span>
          将翻译 <strong className="text-base tabular-nums">{summary.included}</strong> 张
        </span>
        {summary.skipped > 0 && (
          <span className="text-muted-foreground">跳过 {summary.skipped} 张</span>
        )}
        <span className="text-muted-foreground">
          预计耗时 {formatDuration(summary.estimated_seconds)}
        </span>
        {summary.included > 0 && (
          <span className="text-muted-foreground">
            现在开始约 {formatFinishTime(summary.estimated_seconds)} 完成
          </span>
        )}
      </div>

      {Object.keys(summary.reasons).length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {Object.entries(summary.reasons).map(([reason, count]) => (
            <Badge key={reason} variant="secondary" className="font-normal">
              {reason} × {count}
            </Badge>
          ))}
        </div>
      )}

      {summary.folders.length > 0 && (
        <ScrollArea className="max-h-32">
          <div className="space-y-0.5">
            {summary.folders.map((f) => (
              <div key={f.path} className="flex justify-between gap-3 text-xs">
                <span className="text-muted-foreground truncate font-mono" title={f.path}>
                  {f.path}
                </span>
                <span className="shrink-0 tabular-nums">{f.count}</span>
              </div>
            ))}
          </div>
        </ScrollArea>
      )}

      {preview.skipped.length > 0 && (
        <details className="text-xs">
          <summary className="text-muted-foreground cursor-pointer">
            查看被跳过的 {preview.skipped.length} 张
          </summary>
          <ScrollArea className="mt-1.5 max-h-40">
            <div className="space-y-0.5">
              {preview.skipped.map((c) => (
                <div key={c.path} className="flex justify-between gap-3">
                  <span className="text-muted-foreground truncate font-mono" title={c.path}>
                    {c.name}
                  </span>
                  <span className="shrink-0">{c.reason}</span>
                </div>
              ))}
            </div>
          </ScrollArea>
        </details>
      )}
    </div>
  );
}

export function RunPage() {
  // Held in jotai so the form and the running job survive tab switches, and
  // the persisted pieces survive a reload.
  const [inputDir, setInputDir] = useAtom(inputDirAtom);
  const [outputDir, setOutputDir] = useAtom(outputDirAtom);
  const [outputEdited, setOutputEdited] = useAtom(outputEditedAtom);
  const [limit, setLimit] = useAtom(limitAtom);
  const [recursive, setRecursive] = useAtom(recursiveAtom);
  const setActiveJobId = useSetAtom(activeJobIdAtom);
  const [preview, setPreview] = useAtom(previewAtom);
  const [job, setJob] = useAtom(jobAtom);
  const [previewing, setPreviewing] = useState(false);
  const [starting, setStarting] = useState(false);

  const jobRequest = (): JobRequest => {
    const parsed = limit.trim() ? parseInt(limit, 10) : undefined;
    return {
      input_dir: inputDir,
      output_dir: outputDir,
      recursive,
      limit: parsed !== undefined && !Number.isNaN(parsed) ? parsed : undefined,
    };
  };

  const runPreview = async () => {
    setPreviewing(true);
    try {
      setPreview(await api.previewJob(jobRequest()));
    } catch (e) {
      toast.error("试算失败", { description: e instanceof Error ? e.message : String(e) });
    } finally {
      setPreviewing(false);
    }
  };

  // A stale preview describing a different folder is worse than none.
  useEffect(() => setPreview(null), [inputDir, recursive, limit]);

  // Polling, re-attachment and completion toasts live in App via
  // `useJobPolling`, so they keep working while another tab is showing.
  const running = job?.status === "running" || job?.status === "pending";

  const handleFolder = useCallback(
    (path: string) => {
      setInputDir(path);
      // Track the output directory to the input until the user types their own.
      // A plain `prev || default` sticks to whatever folder the picker happened
      // to open on first, so browsing to the real chapter would still have
      // written results next to the home directory.
      if (!outputEdited) setOutputDir(`${path}\\_zh`);
    },
    [outputEdited, setInputDir, setOutputDir],
  );

  const start = async () => {
    setStarting(true);
    try {
      const created = await api.createJob(jobRequest());
      setActiveJobId(created.id);
      setJob(created);
    } catch (e) {
      toast.error("无法启动", { description: e instanceof Error ? e.message : String(e) });
    } finally {
      setStarting(false);
    }
  };

  const percent = job && job.total > 0 ? (job.completed / job.total) * 100 : 0;
  const reviewTotal = job?.results.reduce((sum, r) => sum + r.review_count, 0) ?? 0;

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">输入</CardTitle>
          <CardDescription>选择漫画目录。小于 50KB 的缩略图会自动跳过。</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <FolderPicker value={inputDir} onChange={handleFolder} />

          <div className="space-y-1.5">
            <Label className="text-xs">输出目录</Label>
            <Input
              value={outputDir}
              onChange={(e) => {
                setOutputEdited(true);
                setOutputDir(e.target.value);
              }}
              className="font-mono text-xs"
              placeholder="留空则用 输入目录\_zh"
            />
          </div>

          <div className="flex items-center justify-between gap-4">
            <div>
              <Label className="text-xs">递归子目录</Label>
              <p className="text-muted-foreground mt-0.5 text-xs">
                指向系列文件夹即可一次翻完所有话。输出目录会自动排除。
              </p>
            </div>
            <Switch checked={recursive} onCheckedChange={setRecursive} />
          </div>

          <div className="space-y-1.5">
            <Label className="text-xs">数量限制</Label>
            <div className="flex items-center gap-2">
              <Input
                value={limit}
                onChange={(e) => setLimit(e.target.value.replace(/\D/g, ""))}
                className="w-32"
                placeholder="全部"
                inputMode="numeric"
              />
              {limit.trim() && (
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-8 text-xs"
                  onClick={() => setLimit("")}
                >
                  改为全部
                </Button>
              )}
            </div>
            <p className="text-muted-foreground text-xs">
              {limit.trim()
                ? `只翻译前 ${limit} 张图片，其余跳过。`
                : "默认翻译选中的全部图片。填数字可以先试跑几张确认效果。"}
            </p>
          </div>

          <Separator />

          <div className="space-y-1.5">
            <div className="flex flex-wrap items-center gap-2">
              <Button
                variant="secondary"
                onClick={() => void runPreview()}
                disabled={!inputDir || previewing}
              >
                {previewing ? <Loader2 className="mr-1.5 size-4 animate-spin" />
                            : <Search className="mr-1.5 size-4" />}
                先看会翻哪些
              </Button>
              <Button
                onClick={() => void start()}
                disabled={!inputDir || !outputDir || running || starting}
              >
                {starting ? <Loader2 className="mr-1.5 size-4 animate-spin" />
                          : <Play className="mr-1.5 size-4" />}
                开始翻译
              </Button>
              {running && (
                <Button variant="secondary" onClick={() => job && void api.cancelJob(job.id)}>
                  <Square className="mr-1.5 size-3.5" /> 取消
                </Button>
              )}
            </div>
            <p className="text-muted-foreground text-xs">
              「先看会翻哪些」只是空跑一遍统计，不会真的翻译。
              {recursive && "开了递归之后到底会捞到哪些文件，光看目录是猜不出来的。"}
            </p>
          </div>

          {preview && <PreviewPanel preview={preview} />}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="flex items-center gap-2 text-base">
            进度
            {job && (
              <Badge variant={
                job.status === "done" ? "default"
                : job.status === "failed" ? "destructive"
                : "secondary"
              }>
                {job.status}
              </Badge>
            )}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {!job && (
            <p className="text-muted-foreground text-sm">还没有任务。</p>
          )}

          {job && (
            <>
              <div className="space-y-1.5">
                <div className="flex items-baseline justify-between text-sm">
                  <span className="truncate font-mono text-xs">{job.page_name || "—"}</span>
                  <span className="text-muted-foreground shrink-0 text-xs tabular-nums">
                    {job.completed} / {job.total} 张
                  </span>
                </div>
                <Progress value={percent} />
                <div className="text-muted-foreground flex flex-wrap justify-between gap-x-3 text-xs">
                  <span>已用 {formatDuration(job.elapsed)}</span>
                  {running && <span>{formatRemaining(job.eta)}</span>}
                </div>
              </div>

              {running && job.eta !== null && (
                // The question behind "how long left" is usually "can I go do
                // something else", which a bare countdown does not answer.
                <div className="border-border bg-muted/40 rounded-lg border px-3 py-2">
                  <p className="text-muted-foreground text-xs">预计完成时间</p>
                  <p className="mt-0.5 text-lg font-semibold tabular-nums">
                    {formatFinishTime(job.eta)}
                  </p>
                  <p className="text-muted-foreground mt-0.5 text-xs">
                    还需约 {formatDuration(job.eta)}，按已完成 {job.completed} 张的平均速度推算
                  </p>
                </div>
              )}

              {running && (
                <div className="flex flex-wrap gap-1.5">
                  {job.stages.map((stage) => (
                    <Badge
                      key={stage}
                      variant={job.stage === stage ? "default" : "outline"}
                      className="font-normal"
                    >
                      {STAGE_LABELS[stage] ?? stage}
                    </Badge>
                  ))}
                  {job.stage && !job.stages.includes(job.stage) && (
                    <Badge className="font-normal">
                      {STAGE_LABELS[job.stage] ?? job.stage}
                    </Badge>
                  )}
                </div>
              )}

              {job.status === "done" && (
                <div className="flex items-center gap-2 text-sm">
                  <CheckCircle2 className="text-success size-4" />
                  <span>完成 {job.completed} 页</span>
                  {reviewTotal > 0 && (
                    <span className="text-muted-foreground flex items-center gap-1">
                      <AlertTriangle className="size-3.5" />
                      {reviewTotal} 处待人工确认
                    </span>
                  )}
                </div>
              )}

              {job.status === "failed" && (
                <div className="text-destructive flex items-start gap-2 text-sm">
                  <XCircle className="mt-0.5 size-4 shrink-0" />
                  <span className="font-mono text-xs">{job.error}</span>
                </div>
              )}

              <Separator />

              <ScrollArea className="h-52">
                <div className="space-y-0.5 font-mono text-xs">
                  {job.log.map((line, i) => (
                    <p key={i} className="text-muted-foreground">{line}</p>
                  ))}
                </div>
              </ScrollArea>
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
