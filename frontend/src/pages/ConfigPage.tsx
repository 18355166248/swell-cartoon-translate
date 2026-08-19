import { useEffect, useMemo, useState } from "react";
import { Loader2, Save, RotateCcw, Plus, X, AlertTriangle } from "lucide-react";
import { toast } from "sonner";
import { api, type ConfigField, type ConfigResponse } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Badge } from "@/components/ui/badge";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "@/components/ui/accordion";
import { TYPESET_PATHS } from "@/pages/TypesetPage";

/** Key standing in for the top-level (unprefixed) fields.
 *  Must not be "" -- see the grouping code below. */
const GENERAL = "general";

/** Owned by the 排版 tab -- see the grouping code below. */
const TYPESET_TAB_FIELDS = new Set(Object.values(TYPESET_PATHS));

/** Friendly names for the dotted config sections. */
const SECTION_LABELS: Record<string, string> = {
  [GENERAL]: "通用",
  input: "输入筛选",
  detect: "检测",
  slicing: "长条切片",
  ocr: "文字识别",
  translate: "翻译",
  "translate.llamacpp": "翻译 · 本地 LLM",
  "translate.llm": "翻译 · 远程 OpenAI 兼容",
  "translate.nllb": "翻译 · NLLB（成人素材不适用）",
  typeset: "排版 · 其余",
  erase: "擦除",
};

function FieldControl({
  field,
  value,
  onChange,
}: {
  field: ConfigField;
  value: unknown;
  onChange: (v: unknown) => void;
}) {
  if (field.type === "bool") {
    return (
      <Switch checked={Boolean(value)} onCheckedChange={onChange} />
    );
  }

  if (field.choices) {
    return (
      <Select value={String(value)} onValueChange={onChange}>
        <SelectTrigger className="w-56"><SelectValue /></SelectTrigger>
        <SelectContent>
          {field.choices.map((c) => (
            <SelectItem key={c} value={c}>{c}</SelectItem>
          ))}
        </SelectContent>
      </Select>
    );
  }

  if (field.type === "list") {
    // Comma-separated is enough here: every list in this config is a short
    // ordered list of identifiers (backend names, language codes).
    return (
      <Input
        className="w-72 font-mono text-xs"
        value={Array.isArray(value) ? value.join(", ") : String(value)}
        onChange={(e) =>
          onChange(e.target.value.split(",").map((s) => s.trim()).filter(Boolean))
        }
      />
    );
  }

  if (field.type === "int" || field.type === "float") {
    return (
      <Input
        type="number"
        step={field.type === "float" ? "0.01" : "1"}
        className="w-40"
        value={String(value)}
        onChange={(e) => {
          const raw = e.target.value;
          if (raw === "") return onChange(raw);
          const parsed = field.type === "int" ? parseInt(raw, 10) : parseFloat(raw);
          onChange(Number.isNaN(parsed) ? raw : parsed);
        }}
      />
    );
  }

  return (
    <Input
      className="w-72 font-mono text-xs"
      value={String(value ?? "")}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

export function ConfigPage() {
  const [config, setConfig] = useState<ConfigResponse | null>(null);
  const [edits, setEdits] = useState<Record<string, unknown>>({});
  const [glossary, setGlossary] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);

  const reload = async () => {
    const data = await api.getConfig();
    setConfig(data);
    setGlossary(data.glossary);
    setEdits({});
  };

  useEffect(() => {
    void reload().catch((e) => toast.error("读取配置失败", { description: String(e) }));
  }, []);

  const sections = useMemo(() => {
    if (!config) return [];
    const grouped = new Map<string, ConfigField[]>();
    for (const field of config.fields) {
      // Skip only what the 排版 tab owns, where these are edited beside a
      // live render -- two controls for one setting, one of them blind, is
      // worse than one. The rest of `[typeset]` stays here: `free_text_inset`
      // applies to text outside balloons, which the preview does not draw, so
      // a slider for it would sit next to a picture that never moves.
      if (TYPESET_TAB_FIELDS.has(field.path)) continue;
      // Top-level fields have an empty section. Radix Accordion treats an
      // empty string as "no item", so that panel silently refused to open --
      // give it a real key instead.
      const key = field.section || GENERAL;
      const list = grouped.get(key) ?? [];
      list.push(field);
      grouped.set(key, list);
    }
    return [...grouped.entries()];
  }, [config]);

  const dirty = Object.keys(edits).length > 0 ||
    JSON.stringify(glossary) !== JSON.stringify(config?.glossary ?? {});

  const save = async () => {
    setSaving(true);
    try {
      const result = await api.putConfig(edits, glossary);
      toast.success("已保存", { description: result.saved });
      await reload();
    } catch (e) {
      toast.error("保存失败", { description: e instanceof Error ? e.message : String(e) });
    } finally {
      setSaving(false);
    }
  };

  if (!config) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Loader2 className="text-muted-foreground size-6 animate-spin" />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-lg font-semibold">配置</h2>
          <p className="text-muted-foreground mt-0.5 font-mono text-xs">
            {config.source ?? `未找到配置文件，将新建于 ${config.default_path}`}
          </p>
        </div>
        <div className="flex shrink-0 gap-2">
          <Button variant="ghost" size="sm" onClick={() => void reload()} disabled={!dirty}>
            <RotateCcw className="mr-1.5 size-3.5" /> 撤销
          </Button>
          <Button size="sm" onClick={() => void save()} disabled={!dirty || saving}>
            {saving ? <Loader2 className="mr-1.5 size-3.5 animate-spin" />
                    : <Save className="mr-1.5 size-3.5" />}
            保存到 ctt.toml
          </Button>
        </div>
      </div>

      {config.warnings.length > 0 && (
        <Card className="border-destructive/40">
          <CardContent className="flex gap-2 py-3 text-sm">
            <AlertTriangle className="text-destructive mt-0.5 size-4 shrink-0" />
            <div>
              {config.warnings.map((w) => <p key={w}>{w}</p>)}
            </div>
          </CardContent>
        </Card>
      )}

      <Accordion type="multiple" defaultValue={[GENERAL, "translate.llamacpp"]} className="space-y-2">
        {sections.map(([section, fields]) => (
          <AccordionItem
            key={section}
            value={section}
            className="border-border bg-card rounded-lg border px-4"
          >
            <AccordionTrigger className="hover:no-underline">
              <span className="flex items-center gap-2">
                {SECTION_LABELS[section] ?? section}
                <Badge variant="secondary" className="font-normal">{fields.length}</Badge>
              </span>
            </AccordionTrigger>
            <AccordionContent className="space-y-3 pb-4">
              {fields.map((field) => {
                const current = field.path in edits ? edits[field.path] : field.value;
                return (
                  <div key={field.path} className="flex items-start justify-between gap-6">
                    <div className="min-w-0 flex-1">
                      <Label className="font-mono text-xs">{field.name}</Label>
                      {field.doc && (
                        <p className="text-muted-foreground mt-0.5 text-xs">{field.doc}</p>
                      )}
                    </div>
                    <div className="shrink-0">
                      <FieldControl
                        field={field}
                        value={current}
                        onChange={(v) => setEdits((prev) => ({ ...prev, [field.path]: v }))}
                      />
                    </div>
                  </div>
                );
              })}
            </AccordionContent>
          </AccordionItem>
        ))}
      </Accordion>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">术语表</CardTitle>
          <CardDescription>
            跨页保持人名与称谓一致。只在本地 LLM 和远程 LLM 档生效（注入 prompt）。
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-2">
          {Object.entries(glossary).map(([term, translation]) => (
            <div key={term} className="flex items-center gap-2">
              <Input value={term} readOnly className="w-48 font-mono text-xs" />
              <span className="text-muted-foreground">→</span>
              <Input
                value={translation}
                onChange={(e) =>
                  setGlossary((prev) => ({ ...prev, [term]: e.target.value }))
                }
                className="w-48"
              />
              <Button
                variant="ghost"
                size="icon"
                className="size-8"
                onClick={() =>
                  setGlossary((prev) => {
                    const next = { ...prev };
                    delete next[term];
                    return next;
                  })
                }
              >
                <X className="size-4" />
              </Button>
            </div>
          ))}
          <GlossaryAdd
            onAdd={(term, translation) =>
              setGlossary((prev) => ({ ...prev, [term]: translation }))
            }
          />
        </CardContent>
      </Card>
    </div>
  );
}

function GlossaryAdd({ onAdd }: { onAdd: (term: string, translation: string) => void }) {
  const [term, setTerm] = useState("");
  const [translation, setTranslation] = useState("");

  const submit = () => {
    if (!term.trim() || !translation.trim()) return;
    onAdd(term.trim().toUpperCase(), translation.trim());
    setTerm("");
    setTranslation("");
  };

  return (
    <div className="flex items-center gap-2 pt-1">
      <Input
        placeholder="LIAM"
        value={term}
        onChange={(e) => setTerm(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && submit()}
        className="w-48 font-mono text-xs"
      />
      <span className="text-muted-foreground">→</span>
      <Input
        placeholder="利亚姆"
        value={translation}
        onChange={(e) => setTranslation(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && submit()}
        className="w-48"
      />
      <Button variant="secondary" size="icon" className="size-8" onClick={submit}>
        <Plus className="size-4" />
      </Button>
    </div>
  );
}
