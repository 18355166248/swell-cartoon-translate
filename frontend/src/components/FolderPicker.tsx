import { useCallback, useEffect, useState } from "react";
import { ChevronUp, Folder, FolderOpen, Image as ImageIcon, Loader2 } from "lucide-react";
import { api, type BrowseResponse } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Badge } from "@/components/ui/badge";

/**
 * Filesystem picker served by the backend.
 *
 * A browser's file input hands back a File object, never a path, and the
 * pipeline needs a real directory on the machine running the backend. So the
 * backend does the listing and this walks it.
 */
export function FolderPicker({
  value,
  onChange,
}: {
  value: string;
  onChange: (path: string, imageCount: number) => void;
}) {
  const [listing, setListing] = useState<BrowseResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [picking, setPicking] = useState(false);
  const [error, setError] = useState("");
  const [draft, setDraft] = useState(value);

  const load = useCallback(async (path?: string) => {
    setLoading(true);
    setError("");
    try {
      const result = await api.browse(path);
      setListing(result);
      setDraft(result.path);
      onChange(result.path, result.images);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [onChange]);

  const pickNative = useCallback(async () => {
    setPicking(true);
    setError("");
    try {
      const result = await api.pickFolder(listing?.path ?? value);
      // Cancelling is a normal outcome, not an error -- leave everything as is.
      if (!result.cancelled && result.path) await load(result.path);
    } catch (e) {
      // 501 means no dialog could be shown at all; the in-page browser below
      // still works, so say that rather than just reporting a failure.
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setPicking(false);
    }
  }, [listing?.path, value, load]);

  useEffect(() => {
    void load(value || undefined);
    // Only on mount: afterwards navigation is driven by clicks.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="space-y-2">
      <div className="flex gap-2">
        <Button onClick={() => void pickNative()} disabled={picking || loading}>
          {picking ? <Loader2 className="mr-1.5 size-4 animate-spin" />
                   : <FolderOpen className="mr-1.5 size-4" />}
          选择文件夹
        </Button>
        <Input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && load(draft)}
          placeholder="或粘贴路径"
          className="font-mono text-xs"
        />
        <Button variant="secondary" onClick={() => load(draft)} disabled={loading}>
          {loading ? <Loader2 className="size-4 animate-spin" /> : "打开"}
        </Button>
      </div>

      {error && <p className="text-destructive text-xs">{error}</p>}

      {listing && (
        <div className="border-border rounded-lg border">
          <div className="border-border flex items-center justify-between border-b px-3 py-2">
            <div className="flex min-w-0 items-center gap-2">
              <Button
                variant="ghost"
                size="icon"
                className="size-7 shrink-0"
                disabled={!listing.parent}
                onClick={() => listing.parent && load(listing.parent)}
                title="上一级"
              >
                <ChevronUp className="size-4" />
              </Button>
              <span className="truncate font-mono text-xs" title={listing.path}>
                {listing.path}
              </span>
            </div>
            <div className="flex shrink-0 gap-1.5">
              {listing.images > 0 && (
                <Badge variant="secondary">
                  <ImageIcon className="mr-1 size-3" />
                  本目录 {listing.images}
                </Badge>
              )}
              {/* A series folder holds no images itself, only chapters. Without
                  the recursive count it looks empty and unusable. */}
              {listing.nested_images > listing.images && (
                <Badge variant="outline">含子目录 {listing.nested_images}</Badge>
              )}
            </div>
          </div>

          <ScrollArea className="h-56">
            <div className="p-1">
              {listing.entries.length === 0 && (
                <p className="text-muted-foreground p-3 text-xs">没有子目录</p>
              )}
              {listing.entries.map((entry) => (
                <button
                  key={entry.path}
                  onClick={() => load(entry.path)}
                  className="hover:bg-accent flex w-full items-center justify-between rounded-md px-2 py-1.5 text-left text-sm"
                >
                  <span className="flex min-w-0 items-center gap-2">
                    <Folder className="text-muted-foreground size-4 shrink-0" />
                    <span className="truncate">{entry.name}</span>
                  </span>
                  {entry.nested_images > 0 && (
                    <span className="text-muted-foreground shrink-0 text-xs">
                      {entry.images > 0
                        ? `${entry.images} 张`
                        : `子目录 ${entry.nested_images} 张`}
                    </span>
                  )}
                </button>
              ))}
            </div>
          </ScrollArea>
        </div>
      )}
    </div>
  );
}
