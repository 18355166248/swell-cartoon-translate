import { useEffect, useState } from "react";
import { Settings, Play, Images, Circle } from "lucide-react";
import { Toaster } from "@/components/ui/sonner";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { TooltipProvider } from "@/components/ui/tooltip";
import { api } from "@/lib/api";
import { ConfigPage } from "@/pages/ConfigPage";
import { RunPage } from "@/pages/RunPage";
import { ResultsPage } from "@/pages/ResultsPage";

export default function App() {
  const [tab, setTab] = useState("run");
  const [projectPath, setProjectPath] = useState("");
  const [online, setOnline] = useState<boolean | null>(null);

  useEffect(() => {
    const ping = () => api.health().then(() => setOnline(true)).catch(() => setOnline(false));
    void ping();
    const timer = setInterval(ping, 10_000);
    return () => clearInterval(timer);
  }, []);

  return (
    <TooltipProvider>
      <div className="mx-auto max-w-[1400px] p-5">
        <header className="mb-5 flex items-center justify-between">
          <div>
            <h1 className="text-xl font-semibold tracking-tight">漫画汉化</h1>
            <p className="text-muted-foreground text-xs">
              检测 → 识别 → 翻译 → 擦除 → 排版
            </p>
          </div>
          <div className="flex items-center gap-1.5 text-xs">
            <Circle
              className={`size-2 ${
                online === null ? "fill-muted-foreground text-muted-foreground"
                : online ? "fill-emerald-500 text-emerald-500"
                : "fill-destructive text-destructive"
              }`}
            />
            <span className="text-muted-foreground">
              {online === null ? "连接中" : online ? "后端已连接" : "后端未启动"}
            </span>
          </div>
        </header>

        {online === false && (
          <div className="border-destructive/40 bg-destructive/10 mb-4 rounded-lg border p-3 text-sm">
            <p className="font-medium">后端未启动</p>
            <p className="text-muted-foreground mt-1 font-mono text-xs">
              cd backend &amp;&amp; python -m uvicorn ctt.server:app --port 8000
            </p>
          </div>
        )}

        <Tabs value={tab} onValueChange={setTab}>
          <TabsList className="mb-4">
            <TabsTrigger value="run"><Play className="mr-1.5 size-3.5" />翻译</TabsTrigger>
            <TabsTrigger value="results"><Images className="mr-1.5 size-3.5" />结果</TabsTrigger>
            <TabsTrigger value="config"><Settings className="mr-1.5 size-3.5" />配置</TabsTrigger>
          </TabsList>

          {/* Kept mounted: a running job's poll loop must survive tab switches,
              and re-opening a project on every visit would refetch the images. */}
          <TabsContent value="run" forceMount hidden={tab !== "run"}>
            <RunPage
              onFinished={(path) => {
                setProjectPath(path);
                setTab("results");
              }}
            />
          </TabsContent>
          <TabsContent value="results" forceMount hidden={tab !== "results"}>
            <ResultsPage projectPath={projectPath} />
          </TabsContent>
          <TabsContent value="config">
            <ConfigPage />
          </TabsContent>
        </Tabs>
      </div>
      <Toaster position="bottom-right" />
    </TooltipProvider>
  );
}
