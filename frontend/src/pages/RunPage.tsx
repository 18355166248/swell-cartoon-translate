import { useCallback, useEffect, useRef, useState } from "react";
import { Play, Square, Loader2, CheckCircle2, XCircle, AlertTriangle } from "lucide-react";
import { toast } from "sonner";
import { api, type Job } from "@/lib/api";
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

function formatSeconds(value: number | null): string {
  if (value === null) return "—";
  if (value < 60) return `${Math.round(value)}s`;
  const minutes = Math.floor(value / 60);
  return `${minutes}m ${Math.round(value % 60)}s`;
}

export function RunPage({ onFinished }: { onFinished: (projectPath: string) => void }) {
  const [inputDir, setInputDir] = useState("");
  const [imageCount, setImageCount] = useState(0);
  const [outputDir, setOutputDir] = useState("");
  const [limit, setLimit] = useState<string>("10");
  const [job, setJob] = useState<Job | null>(null);
  const [starting, setStarting] = useState(false);
  const notified = useRef<string>("");
  const outputEdited = useRef(false);

  const running = job?.status === "running" || job?.status === "pending";

  // Poll while a job is live. 1s is comfortably finer than the ~36s it takes
  // to finish a page, and the endpoint is a dict lookup.
  useEffect(() => {
    if (!job || !running) return;
    const timer = setInterval(async () => {
      try {
        setJob(await api.getJob(job.id));
      } catch {
        /* transient; next tick retries */
      }
    }, 1000);
    return () => clearInterval(timer);
  }, [job, running]);

  // Fire completion side effects once, not on every poll that follows.
  useEffect(() => {
    if (!job || running || notified.current === job.id) return;
    notified.current = job.id;
    if (job.status === "done") {
      toast.success(`翻译完成：${job.completed} 页`, { description: job.output_dir });
      if (job.project_path) onFinished(job.project_path);
    } else if (job.status === "failed") {
      toast.error("任务失败", { description: job.error });
    } else if (job.status === "cancelled") {
      toast.info(`已取消，完成 ${job.completed} 页`);
      if (job.project_path) onFinished(job.project_path);
    }
  }, [job, running, onFinished]);

  // Resume display if a job is already running (e.g. after a page refresh).
  useEffect(() => {
    void api.listJobs().then((jobs) => {
      const live = jobs.find((j) => j.status === "running" || j.status === "pending");
      if (live) {
        setJob(live);
        notified.current = "";
      }
    }).catch(() => {});
  }, []);

  const handleFolder = useCallback((path: string, images: number) => {
    setInputDir(path);
    setImageCount(images);
    // Track the output directory to the input until the user types their own.
    // A plain `prev || default` sticks to whatever folder the picker happened
    // to open on first, so browsing to the real chapter would still have
    // written results next to the home directory.
    setOutputDir((prev) => (outputEdited.current ? prev : `${path}\\_zh`));
  }, []);

  const start = async () => {
    setStarting(true);
    try {
      const parsedLimit = limit.trim() ? parseInt(limit, 10) : undefined;
      const created = await api.createJob({
        input_dir: inputDir,
        output_dir: outputDir,
        limit: Number.isNaN(parsedLimit!) ? undefined : parsedLimit,
      });
      notified.current = "";
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
                outputEdited.current = true;
                setOutputDir(e.target.value);
              }}
              className="font-mono text-xs"
              placeholder="留空则用 输入目录\_zh"
            />
          </div>

          <div className="space-y-1.5">
            <Label className="text-xs">只跑前 N 页</Label>
            <Input
              value={limit}
              onChange={(e) => setLimit(e.target.value)}
              className="w-32"
              placeholder="留空 = 全部"
            />
            <p className="text-muted-foreground text-xs">
              一页约 36 秒。先跑 10 页确认效果，再决定要不要跑整话。
            </p>
          </div>

          <Separator />

          <div className="flex items-center gap-2">
            <Button
              onClick={() => void start()}
              disabled={!inputDir || !outputDir || imageCount === 0 || running || starting}
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
            {imageCount > 0 && !running && (
              <span className="text-muted-foreground text-xs">
                本目录 {imageCount} 张图
              </span>
            )}
          </div>
          {imageCount === 0 && inputDir && (
            <p className="text-muted-foreground text-xs">
              这个目录里没有图片，进下一层看看。
            </p>
          )}
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
                  <span className="text-muted-foreground shrink-0 text-xs">
                    {job.completed} / {job.total}
                  </span>
                </div>
                <Progress value={percent} />
                <div className="text-muted-foreground flex justify-between text-xs">
                  <span>已用 {formatSeconds(job.elapsed)}</span>
                  <span>剩余 {formatSeconds(job.eta)}</span>
                </div>
              </div>

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
                  <CheckCircle2 className="size-4 text-emerald-500" />
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
