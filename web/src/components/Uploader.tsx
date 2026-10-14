import { ImagePlus, Loader2 } from "lucide-react";
import { useRef, useState } from "react";
import { api, type Asset } from "../api";
import { cx, useToast } from "./ui";

export function Uploader({
  kind,
  accept = "image/*",
  label,
  onUploaded,
  multiple,
  compact,
}: {
  kind: string;
  accept?: string;
  label: string;
  onUploaded: (a: Asset) => void;
  multiple?: boolean;
  compact?: boolean;
}) {
  const ref = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [over, setOver] = useState(false);
  const toast = useToast();

  async function handle(files: FileList | null) {
    if (!files?.length) return;
    setBusy(true);
    try {
      for (const f of Array.from(files)) onUploaded(await api.upload(kind, f));
    } catch (e) {
      toast(`Upload failed: ${(e as Error).message}`, "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
    <button
      type="button"
      onClick={() => ref.current?.click()}
      onDragOver={(e) => {
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        handle(e.dataTransfer.files);
      }}
      className={cx(
        "flex w-full items-center justify-center gap-2 rounded-lg border border-dashed text-xs text-muted transition-colors hover:border-accent hover:text-text",
        compact ? "h-20" : "h-28 flex-col",
        over ? "border-accent bg-accent/5" : "border-line",
      )}
    >
      {busy ? <Loader2 className="size-4 animate-spin" /> : <ImagePlus className="size-4" />}
      {label}
    </button>
    <input
      ref={ref}
      type="file"
      accept={accept}
      multiple={multiple}
      hidden
      data-testid={`upload-${kind}`}
      onChange={(e) => handle(e.target.files)}
    />
    </>
  );
}
