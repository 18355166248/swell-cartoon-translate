import { useEffect, useState } from "react";
import { Cpu, Gamepad2, Zap, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { api, type RuntimeInfo } from "@/lib/api";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";

const LABELS: Record<string, string> = {
  performance: "全速 + GPU",
  balanced: "均衡",
  background: "后台（玩游戏）",
};

const ICONS: Record<string, typeof Cpu> = {
  performance: Zap,
  balanced: Cpu,
  background: Gamepad2,
};

/**
 * Picks how much of the machine a run may take.
 *
 * Lives next to the start button rather than in the settings page because it
 * is a per-run decision -- "am I going to use this machine for the next few
 * hours?" -- not a preference you set once.
 */
export function ProfileSwitch() {
  const [info, setInfo] = useState<RuntimeInfo | null>(null);
  const [current, setCurrent] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    void (async () => {
      try {
        const [list, config] = await Promise.all([api.runtimeProfiles(), api.getConfig()]);
        setInfo(list);
        const field = config.fields.find((f) => f.path === "runtime.profile");
        setCurrent(String(field?.value ?? "balanced"));
      } catch {
        /* the backend banner already reports being offline */
      }
    })();
  }, []);

  const change = async (name: string) => {
    const previous = current;
    setCurrent(name);
    setSaving(true);
    try {
      await api.putConfig({ "runtime.profile": name });
      // Re-fetch: the layer count is computed from *free* VRAM, so it depends
      // on what else is running right now, not just on the profile.
      setInfo(await api.runtimeProfiles());
    } catch (e) {
      setCurrent(previous);
      toast.error("切换失败", { description: e instanceof Error ? e.message : String(e) });
    } finally {
      setSaving(false);
    }
  };

  const profiles = info?.profiles ?? [];
  const active = profiles.find((p) => p.name === current);
  const Icon = ICONS[current] ?? Cpu;

  return (
    <div className="space-y-1.5">
      <Label className="text-xs">运行档位</Label>
      <div className="flex items-center gap-2">
        <Select value={current} onValueChange={(v) => void change(v)} disabled={saving}>
          <SelectTrigger className="w-48">
            <span className="flex items-center gap-2">
              <Icon className="size-3.5 shrink-0" />
              <SelectValue />
            </span>
          </SelectTrigger>
          <SelectContent>
            {profiles.map((p) => (
              <SelectItem key={p.name} value={p.name}>
                {LABELS[p.name] ?? p.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        {saving && <Loader2 className="text-muted-foreground size-4 animate-spin" />}

        {active && (
          <div className="flex flex-wrap items-center gap-1.5">
            <Badge variant="outline" className="font-normal">
              {active.threads}/{info?.cores ?? 0} 核
            </Badge>
            <Badge variant={active.gpu ? "default" : "outline"} className="font-normal">
              {active.gpu && active.gpu_layers > 0
                ? `GPU ${active.gpu_layers} 层`
                : "不占显存"}
            </Badge>
          </div>
        )}
      </div>

      {active && (
        <p className="text-muted-foreground text-xs">{active.description}</p>
      )}

      {/* The layer count is sized from *free* VRAM, so a job that is using the
          GPU drives it to zero. Reading that as "no GPU" warned exactly when
          the card was working -- hence the split between "CUDA is missing",
          which is a real problem, and "the card is busy with our own run",
          which is not. */}
      {active?.gpu && info && !info.cuda_available && (
        <p className="text-warning text-xs">
          选了 GPU 档，但当前装的是 CPU 版 llama-cpp-python，本次仍走纯 CPU。
          安装方法见 README「GPU 卸载」。
        </p>
      )}
      {active?.gpu && info?.cuda_available && active.gpu_layers === 0 && (
        <p className="text-muted-foreground text-xs">
          {info.job_running
            ? "显存正被当前任务占用，这是正常的——任务结束就会释放。"
            : `空闲显存只剩 ${info.free_vram_mb} MB，不够卸载，本次走纯 CPU。关掉占显存的程序再试。`}
        </p>
      )}
    </div>
  );
}
