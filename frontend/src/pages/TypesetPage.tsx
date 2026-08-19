/** Typeset settings, with a live preview of what they do to real text.
 *
 * Split out of the generic settings form because these are the only settings
 * you cannot judge by reading them. `bubble_inset = 0.1` means nothing until
 * you see the balloon; `min_size` is invisible until some line stops shrinking
 * and gets flagged instead.
 *
 * The preview is rendered by the backend through the actual layout engine, not
 * mocked up in CSS. The searched font size, the shape-aware wrapping and the
 * Chinese line-break rules have no browser equivalent, so a CSS approximation
 * would look convincing while the real output still overflowed.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, Save, RotateCcw, AlertTriangle, Type, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { api, type FontEntry, type TypesetFact, type TypesetSettings } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Slider } from "@/components/ui/slider";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";

/** Config paths, so saving can reuse the generic config endpoint.
 *  Exported because ConfigPage hides exactly these -- deriving the two from
 *  one list is what stops a new knob here from leaving a second, blind
 *  control for the same setting over there. */
export const TYPESET_PATHS: Record<keyof Required<TypesetSettings>, string> = {
  font: "typeset.font",
  line_spacing: "typeset.line_spacing",
  align: "typeset.align",
  min_size: "typeset.min_size",
  bubble_inset: "typeset.bubble_inset",
};

const ALIGN_LABELS: Record<string, string> = {
  center: "居中",
  left: "左对齐",
  right: "右对齐",
};

function Knob({
  label,
  hint,
  value,
  min,
  max,
  step,
  format,
  onChange,
}: {
  label: string;
  hint: string;
  value: number;
  min: number;
  max: number;
  step: number;
  format: (v: number) => string;
  onChange: (v: number) => void;
}) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-baseline justify-between gap-2">
        <Label className="text-xs font-medium">{label}</Label>
        <span className="text-muted-foreground font-mono text-xs tabular-nums">{format(value)}</span>
      </div>
      <Slider
        value={[value]}
        min={min}
        max={max}
        step={step}
        onValueChange={([v]) => onChange(v)}
      />
      <p className="text-muted-foreground text-[11px] leading-snug">{hint}</p>
    </div>
  );
}

export function TypesetPage() {
  const [saved, setSaved] = useState<TypesetSettings | null>(null);
  const [draft, setDraft] = useState<TypesetSettings | null>(null);
  const [fonts, setFonts] = useState<FontEntry[]>([]);
  const [customText, setCustomText] = useState("");
  const [preview, setPreview] = useState<{ url: string; facts: TypesetFact[] } | null>(null);
  const [rendering, setRendering] = useState(false);
  const [dark, setDark] = useState(() => document.documentElement.classList.contains("dark"));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  // Held in a ref rather than state: revoking is cleanup, and putting the URL
  // in the dependency list would revoke the image the DOM is still showing.
  const objectUrl = useRef<string>("");

  const load = useCallback(async () => {
    const [config, fontList] = await Promise.all([api.getConfig(), api.typesetFonts()]);
    const current: TypesetSettings = {};
    for (const [key, path] of Object.entries(TYPESET_PATHS)) {
      const field = config.fields.find((f) => f.path === path);
      if (field) (current as Record<string, unknown>)[key] = field.value;
    }
    setSaved(current);
    setDraft(current);
    setFonts(fontList.fonts);
  }, []);

  useEffect(() => {
    void load().catch((e) => toast.error("读取排版配置失败", { description: String(e) }));
  }, [load]);

  // The preview is a bitmap the backend drew, so it cannot follow the theme
  // the way CSS does -- it has to be re-rendered. Watching the class the theme
  // toggle sets covers the OS flipping at sunset too, which fires no click.
  useEffect(() => {
    const target = document.documentElement;
    const observer = new MutationObserver(() => setDark(target.classList.contains("dark")));
    observer.observe(target, { attributes: true, attributeFilter: ["class"] });
    return () => observer.disconnect();
  }, []);

  // Re-render whenever a knob moves. Debounced because dragging a slider fires
  // continuously and each render is a real layout pass on the backend; aborted
  // on the way out so a slow render cannot land after a newer one.
  useEffect(() => {
    if (!draft) return;
    const controller = new AbortController();
    const texts = customText.split("\n").map((t) => t.trim()).filter(Boolean);
    const timer = setTimeout(() => {
      setRendering(true);
      api
        .typesetPreview(
          {
            ...draft,
            ...(texts.length ? { texts } : {}),
            dark,
          },
          controller.signal,
        )
        .then((next) => {
          // A response can still land after teardown if it was already on the
          // wire when the abort went out. Its object URL is allocated by then,
          // so dropping it on the floor would leak the blob.
          if (controller.signal.aborted) {
            URL.revokeObjectURL(next.url);
            return;
          }
          if (objectUrl.current) URL.revokeObjectURL(objectUrl.current);
          objectUrl.current = next.url;
          setPreview(next);
          setError("");
        })
        .catch((e) => {
          if (controller.signal.aborted) return;
          setError(e instanceof Error ? e.message : String(e));
        })
        .finally(() => {
          if (!controller.signal.aborted) setRendering(false);
        });
    }, 220);
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [draft, customText, dark]);

  useEffect(() => () => {
    if (objectUrl.current) URL.revokeObjectURL(objectUrl.current);
  }, []);

  if (!draft || !saved) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Loader2 className="text-muted-foreground size-6 animate-spin" />
      </div>
    );
  }

  const set = (patch: Partial<TypesetSettings>) => setDraft({ ...draft, ...patch });
  const dirty = JSON.stringify(draft) !== JSON.stringify(saved);

  const save = async () => {
    setSaving(true);
    try {
      const fields = Object.fromEntries(
        Object.entries(TYPESET_PATHS).map(([key, path]) => [path, draft[key as keyof TypesetSettings]]),
      );
      const result = await api.putConfig(fields);
      toast.success("已保存", { description: result.saved });
      await load();
    } catch (e) {
      toast.error("保存失败", { description: e instanceof Error ? e.message : String(e) });
    } finally {
      setSaving(false);
    }
  };

  const missingFont = fonts.find((f) => f.name === draft.font && !f.available);
  const overflowing = preview?.facts.filter((f) => f.overflow) ?? [];

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-lg font-semibold">排版预览</h2>
          <p className="text-muted-foreground mt-0.5 text-xs">
            用真实排版引擎渲染样例气泡，所见即成品效果
          </p>
        </div>
        <div className="flex shrink-0 gap-2">
          <Button variant="ghost" size="sm" onClick={() => setDraft(saved)} disabled={!dirty}>
            <RotateCcw className="mr-1.5 size-3.5" /> 撤销
          </Button>
          <Button size="sm" onClick={() => void save()} disabled={!dirty || saving}>
            {saving ? <Loader2 className="mr-1.5 size-3.5 animate-spin" />
                    : <Save className="mr-1.5 size-3.5" />}
            保存到 ctt.toml
          </Button>
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-[300px_1fr]">
        {/* -------------------------------------------------------- 控件 --- */}
        <Card className="lg:sticky lg:top-0 lg:self-start">
          <CardHeader className="pb-3">
            <CardTitle className="text-sm">参数</CardTitle>
            <CardDescription className="text-xs">改动即时生效于右侧预览</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-1.5">
              <Label className="text-xs font-medium">字体</Label>
              <Select value={draft.font} onValueChange={(font) => set({ font })}>
                <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {fonts.map((f) => (
                    <SelectItem key={f.name} value={f.name}>
                      <span className="flex items-center gap-2">
                        {f.name}
                        {!f.available && (
                          <Badge variant="destructive" className="font-normal">缺失</Badge>
                        )}
                      </span>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-muted-foreground truncate font-mono text-[11px]">
                {fonts.find((f) => f.name === draft.font)?.file ?? "—"}
              </p>
            </div>

            <div className="space-y-1.5">
              <Label className="text-xs font-medium">对齐</Label>
              <Select value={draft.align} onValueChange={(align) => set({ align })}>
                <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {Object.entries(ALIGN_LABELS).map(([value, label]) => (
                    <SelectItem key={value} value={value}>{label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <Knob
              label="行距"
              hint="行高相对字号的倍数。调大更透气，但字号会被迫变小。"
              value={draft.line_spacing ?? 1.15}
              min={0.9}
              max={2.2}
              step={0.05}
              format={(v) => v.toFixed(2)}
              onChange={(line_spacing) => set({ line_spacing })}
            />

            <Knob
              label="气泡内边距"
              hint="占气泡短边的比例。留白太少会顶到气泡描边，太多则字号被压得很小。"
              value={draft.bubble_inset ?? 0.1}
              min={0}
              max={0.35}
              step={0.01}
              format={(v) => `${(v * 100).toFixed(0)}%`}
              onChange={(bubble_inset) => set({ bubble_inset })}
            />

            <Knob
              label="最小字号"
              hint="缩到这个字号仍放不下就不再缩小，改为标记待复核——宁可标出来，也不要糊成一团。"
              value={draft.min_size ?? 9}
              min={6}
              max={48}
              step={1}
              format={(v) => `${v} px`}
              onChange={(min_size) => set({ min_size })}
            />
          </CardContent>
        </Card>

        {/* -------------------------------------------------------- 预览 --- */}
        <div className="min-w-0 space-y-4">
          {missingFont && (
            <Card className="border-destructive/40">
              <CardContent className="flex gap-2 py-3 text-sm">
                <AlertTriangle className="text-destructive mt-0.5 size-4 shrink-0" />
                <div>
                  <p className="font-medium">字体 {missingFont.name} 在本机找不到</p>
                  <p className="text-muted-foreground text-xs">
                    渲染时每个字都会变成方框。换一个未标「缺失」的字体。
                  </p>
                </div>
              </CardContent>
            </Card>
          )}

          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="flex items-center gap-2 text-sm">
                <Type className="size-4" /> 样例气泡
                {rendering && <Loader2 className="text-muted-foreground size-3.5 animate-spin" />}
              </CardTitle>
              <CardDescription className="text-xs">
                从一个词到塞不下的长句，覆盖各参数起作用的区间
              </CardDescription>
              {/* `CardAction` rather than a flex row: CardHeader is a grid and
                  only gives up a second column for this slot. */}
              <CardAction>
                <Button variant="ghost" size="sm" onClick={() => setDraft({ ...draft })}>
                  <RefreshCw className="mr-1.5 size-3.5" /> 重渲染
                </Button>
              </CardAction>
            </CardHeader>
            <CardContent className="space-y-3">
              {error && (
                <p className="text-destructive text-xs">渲染失败：{error}</p>
              )}
              {/* Horizontal scroll stays inside this box. The strip grows with
                  the number of samples and must never widen the page. */}
              <div className="border-border overflow-x-auto rounded-md border">
                {preview
                  ? <img src={preview.url} alt="排版预览" className="max-w-none" />
                  : <div className="h-[220px]" />}
              </div>

              {/* The numbers are the point. Whether a line fits, and at what
                  size, is exactly what these settings decide -- and neither is
                  reliably readable off the picture. */}
              <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
                {preview?.facts.map((fact, i) => (
                  <div
                    key={`${fact.label}-${i}`}
                    className={`rounded-md border px-2.5 py-2 text-xs ${
                      fact.overflow ? "border-destructive/50 bg-destructive/5" : "border-border"
                    }`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-medium">{fact.label}</span>
                      <span className="text-muted-foreground font-mono tabular-nums">
                        {fact.size}px · {fact.lines}行
                      </span>
                    </div>
                    <p className="text-muted-foreground mt-1 line-clamp-2 leading-snug">
                      {fact.text}
                    </p>
                    {fact.overflow && (
                      <p className="text-destructive mt-1">放不下，会进待复核</p>
                    )}
                  </div>
                ))}
              </div>

              {overflowing.length > 0 && (
                <p className="text-muted-foreground text-xs">
                  {overflowing.length} 个样例触到最小字号。正片里这些会被标记待复核，
                  而不是压成看不清的小字。
                </p>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-sm">自定义文本</CardTitle>
              <CardDescription className="text-xs">
                一行一个气泡，留空则用内置样例。用来试真正让你头疼的那几句。
              </CardDescription>
            </CardHeader>
            <CardContent>
              <textarea
                value={customText}
                rows={3}
                spellCheck={false}
                placeholder={`住手！
我回来了。你还好吗？`}
                onChange={(e) => setCustomText(e.target.value)}
                className="border-input bg-transparent placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-ring/50 w-full resize-y rounded-md border px-3 py-2 text-sm shadow-xs outline-none focus-visible:ring-[3px]"
              />
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
