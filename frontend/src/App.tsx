import { useCallback, useEffect } from "react";
import { useAtom } from "jotai";
import { Settings, Play, Images, Circle } from "lucide-react";
import { activeTabAtom, backendOnlineAtom, projectPathAtom } from "@/state/atoms";
import { useJobPolling } from "@/hooks/useJobPolling";
import { Toaster } from "@/components/ui/sonner";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { TooltipProvider } from "@/components/ui/tooltip";
import { ThemeToggle } from "@/components/ThemeToggle";
import { api } from "@/lib/api";
import { ConfigPage } from "@/pages/ConfigPage";
import { RunPage } from "@/pages/RunPage";
import { ResultsPage } from "@/pages/ResultsPage";

/** Scroll container for the document-like tabs. */
function Scrollable({ children }: { children: React.ReactNode }) {
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-[1400px] p-5">{children}</div>
    </div>
  );
}

export default function App() {
  // Persisted, so a reload lands back on the tab and project you were using.
  const [tab, setTab] = useAtom(activeTabAtom);
  const [projectPath, setProjectPath] = useAtom(projectPathAtom);
  const [online, setOnline] = useAtom(backendOnlineAtom);

  useEffect(() => {
    const ping = () => api.health().then(() => setOnline(true)).catch(() => setOnline(false));
    void ping();
    const timer = setInterval(ping, 10_000);
    return () => clearInterval(timer);
  }, [setOnline]);

  // Mounted here rather than in RunPage so progress keeps advancing while any
  // tab is showing, and so a reload re-attaches to a run already in flight.
  useJobPolling(
    useCallback(
      (path: string) => {
        setProjectPath(path);
        setTab("results");
      },
      [setProjectPath, setTab],
    ),
  );

  return (
    <TooltipProvider>
      {/* The shell fills the viewport and never scrolls itself. `min-h-0` on
          the scrolling child is what actually lets it shrink -- a flex item
          defaults to min-height:auto and refuses to be smaller than its
          content, which pushes the overflow onto the page instead. */}
      <div className="flex h-full flex-col">
        <header className="border-border bg-background flex shrink-0 items-center justify-between border-b px-5 py-3">
          <div>
            <h1 className="text-lg font-semibold tracking-tight">漫画汉化</h1>
            <p className="text-muted-foreground text-xs">
              检测 → 识别 → 翻译 → 擦除 → 排版
            </p>
          </div>
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-1.5 text-xs">
              <Circle
                className={`size-2 ${
                  online === null ? "fill-muted-foreground text-muted-foreground"
                  : online ? "fill-success text-success"
                  : "fill-destructive text-destructive"
                }`}
              />
              <span className="text-muted-foreground">
                {online === null ? "连接中" : online ? "后端已连接" : "后端未启动"}
              </span>
            </div>
            <ThemeToggle />
          </div>
        </header>

        <Tabs value={tab} onValueChange={setTab} className="flex min-h-0 flex-1 flex-col gap-0">
          <div className="border-border bg-background shrink-0 border-b px-5 py-2">
            <TabsList>
              <TabsTrigger value="run"><Play className="mr-1.5 size-3.5" />翻译</TabsTrigger>
              <TabsTrigger value="results"><Images className="mr-1.5 size-3.5" />结果</TabsTrigger>
              <TabsTrigger value="config"><Settings className="mr-1.5 size-3.5" />配置</TabsTrigger>
            </TabsList>
          </div>

          {/* No overflow here on purpose. Document-like tabs scroll their own
              body; the results viewer instead fills the height and scrolls its
              two panes separately, which a shared scroller cannot express. */}
          <div className="relative min-h-0 flex-1">
            {online === false && (
              <div className="border-destructive/40 bg-destructive/10 absolute inset-x-5 top-4 z-10 rounded-lg border p-3 text-sm">
                <p className="font-medium">后端未启动</p>
                <p className="text-muted-foreground mt-1 font-mono text-xs">
                  powershell -File scripts\dev.ps1
                </p>
              </div>
            )}

            {/* No `forceMount` needed any more: the state these pages care
                about lives in jotai and the poll loop lives above, so they
                can unmount and come back to exactly where they were. */}
            <TabsContent value="run" className="h-full">
              <Scrollable>
                <RunPage />
              </Scrollable>
            </TabsContent>
            <TabsContent value="results" className="h-full">
              <ResultsPage projectPath={projectPath} />
            </TabsContent>
            <TabsContent value="config" className="h-full">
              <Scrollable>
                <ConfigPage />
              </Scrollable>
            </TabsContent>
          </div>
        </Tabs>
      </div>
      <Toaster position="bottom-right" />
    </TooltipProvider>
  );
}
