import { useEffect, useState } from "react";
import { Moon, Sun, Monitor } from "lucide-react";
import { Button } from "@/components/ui/button";

export type Theme = "light" | "dark" | "system";

const STORAGE_KEY = "ctt-theme";

function systemPrefersDark() {
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

export function applyTheme(theme: Theme) {
  const dark = theme === "dark" || (theme === "system" && systemPrefersDark());
  document.documentElement.classList.toggle("dark", dark);
}

export function readStoredTheme(): Theme {
  const stored = localStorage.getItem(STORAGE_KEY);
  return stored === "light" || stored === "dark" || stored === "system" ? stored : "system";
}

const ORDER: Theme[] = ["light", "dark", "system"];
const LABELS: Record<Theme, string> = { light: "浅色", dark: "深色", system: "跟随系统" };
const ICONS: Record<Theme, typeof Sun> = { light: Sun, dark: Moon, system: Monitor };

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(readStoredTheme);

  useEffect(() => {
    applyTheme(theme);
    localStorage.setItem(STORAGE_KEY, theme);
  }, [theme]);

  // Follow the OS while set to "system" -- without this the page keeps the
  // colours it had at load time when the OS flips at sunset.
  useEffect(() => {
    if (theme !== "system") return;
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => applyTheme("system");
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, [theme]);

  const Icon = ICONS[theme];

  return (
    <Button
      variant="ghost"
      size="icon"
      className="size-8"
      title={`主题：${LABELS[theme]}`}
      onClick={() => setTheme(ORDER[(ORDER.indexOf(theme) + 1) % ORDER.length])}
    >
      <Icon className="size-4" />
      <span className="sr-only">{LABELS[theme]}</span>
    </Button>
  );
}
