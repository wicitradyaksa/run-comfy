import { Check, RotateCcw, ShieldAlert, ShieldCheck, X } from "lucide-react";
import { useRef, useState } from "react";
import { fileUrl, isVideo, money, type Variant } from "../api";
import { cx, Pill } from "./ui";

const STAGE_DOT: Record<string, string> = {
  done: "bg-ok",
  running: "bg-accent animate-pulse-dot",
  failed: "bg-bad",
};

export function Media({ path, autoPlay, controls, className }: { path: string; autoPlay?: boolean; controls?: boolean; className?: string }) {
  const ref = useRef<HTMLVideoElement>(null);
  if (!isVideo(path)) return <img src={fileUrl(path)} alt="" className={cx("size-full object-contain", className)} />;
  return (
    <video
      ref={ref}
      src={fileUrl(path)}
      muted={!controls}
      loop
      playsInline
      controls={controls}
      autoPlay={autoPlay}
      preload="none"
      poster={fileUrl(path.replace(/\.(mp4|webm|mov)$/i, ".jpg"))}
      className={cx("size-full object-contain", className)}
      onMouseEnter={() => !controls && ref.current?.play().catch(() => {})}
      onMouseLeave={() => {
        if (!controls && ref.current) {
          ref.current.pause();
          ref.current.currentTime = 0;
        }
      }}
    />
  );
}

export function VariantCard({
  v,
  progress,
  selected,
  onSelect,
  onOpen,
  onReview,
  onRegen,
}: {
  v: Variant;
  progress?: { stage: string; progress: number; message?: string };
  selected: boolean;
  onSelect: () => void;
  onOpen: () => void;
  onReview: (d: string) => void;
  onRegen: (stage: string) => void;
}) {
  const aspects = Object.keys(v.outputs);
  const [aspect, setAspect] = useState(aspects[0]);
  const path = v.outputs[aspect ?? ""] ?? v.outputs[aspects[0]];
  const [regenOpen, setRegenOpen] = useState(false);
  const stages = Object.entries(v.stages);

  return (
    <div
      data-testid={`variant-${v.code}`}
      className={cx(
        "group flex flex-col overflow-hidden rounded-xl border bg-panel transition-colors",
        selected ? "border-accent" : v.review === "approved" ? "border-ok/50" : v.review === "rejected" ? "border-bad/40 opacity-60" : "border-line",
      )}
    >
      <div className="relative aspect-[9/12] cursor-pointer bg-black" onClick={path ? onOpen : undefined}>
        {path ? (
          <Media path={path} />
        ) : (
          <div className="flex size-full flex-col items-center justify-center gap-2 px-6 text-center text-xs text-muted">
            <Pill status={v.status} />
            {progress && v.status === "running" && (
              <>
                <span className="font-mono">{progress.stage}</span>
                <div className="h-1 w-full overflow-hidden rounded-full bg-line">
                  <div className="h-full bg-accent transition-all" style={{ width: `${Math.round(progress.progress * 100)}%` }} />
                </div>
                {progress.message && <span className="text-[10px]">{progress.message}</span>}
              </>
            )}
            {v.error && <span className="line-clamp-4 text-bad">{v.error}</span>}
          </div>
        )}
        <label className="absolute left-2 top-2 flex items-center rounded-md bg-black/60 p-1.5 opacity-0 group-hover:opacity-100 has-[:checked]:opacity-100" onClick={(e) => e.stopPropagation()}>
          <input type="checkbox" checked={selected} onChange={onSelect} className="accent-violet-500" aria-label={`Compare ${v.code}`} />
        </label>
        {v.qa?.score != null || v.qa?.technical ? (
          <div
            title={[v.qa.summary, ...(v.qa.issues ?? []), ...(v.qa.technical ?? [])].filter(Boolean).join("\n")}
            className={cx(
              "absolute right-2 top-2 flex items-center gap-1 rounded-md px-1.5 py-1 text-[11px] font-medium backdrop-blur",
              v.qa.passed ? "bg-ok/20 text-ok" : "bg-warn/20 text-warn",
            )}
          >
            {v.qa.passed ? <ShieldCheck className="size-3.5" /> : <ShieldAlert className="size-3.5" />}
            {v.qa.score != null ? `${v.qa.score}/10` : v.qa.passed ? "QA" : "QA!"}
          </div>
        ) : null}
      </div>

      <div className="flex flex-1 flex-col gap-2 p-3">
        <div className="flex items-center justify-between gap-2">
          <span className="font-mono text-xs">{v.code}</span>
          <div className="flex items-center gap-1">
            {aspects.length > 1 &&
              aspects.map((a) => (
                <button
                  key={a}
                  onClick={() => setAspect(a)}
                  className={cx("rounded px-1.5 py-0.5 text-[10px] tabular-nums", a === aspect ? "bg-panel-2 text-text" : "text-muted hover:text-text")}
                >
                  {a}
                </button>
              ))}
          </div>
        </div>
        <div className="space-y-0.5 text-[11px] text-muted">
          {Object.entries(v.labels)
            .slice(0, 3)
            .map(([k, label]) => (
              <div key={k} className="truncate" title={label}>
                <span className="capitalize text-muted/70">{k}:</span> <span className="text-text/90">{label}</span>
              </div>
            ))}
        </div>
        {stages.length > 0 && (
          <div className="flex flex-wrap items-center gap-1" aria-label="pipeline stages">
            {stages.map(([name, s]) => (
              <span
                key={name}
                title={`${name}: ${s.cached ? "reused from cache" : s.status}${s.seconds ? ` · ${s.seconds}s` : ""}${s.error ? ` · ${s.error}` : ""}`}
                className={cx("h-1.5 w-4 rounded-full", s.cached ? "bg-accent-2/40" : (STAGE_DOT[s.status] ?? "bg-line"))}
              />
            ))}
            <span className="ml-auto text-[10px] tabular-nums text-muted">{money(v.cost_usd)}</span>
          </div>
        )}
        <div className="mt-auto flex items-center gap-1.5 pt-1">
          <button
            onClick={() => onReview(v.review === "approved" ? "pending" : "approved")}
            disabled={v.status !== "done"}
            aria-label={`Approve ${v.code}`}
            className={cx(
              "flex h-7 flex-1 items-center justify-center gap-1 rounded-md border text-xs disabled:opacity-40",
              v.review === "approved" ? "border-ok bg-ok/15 text-ok" : "border-line hover:border-ok/60 hover:text-ok",
            )}
          >
            <Check className="size-3.5" /> {v.review === "approved" ? "Approved" : "Approve"}
          </button>
          <button
            onClick={() => onReview(v.review === "rejected" ? "pending" : "rejected")}
            disabled={v.status !== "done"}
            aria-label={`Reject ${v.code}`}
            className={cx(
              "flex size-7 items-center justify-center rounded-md border disabled:opacity-40",
              v.review === "rejected" ? "border-bad bg-bad/15 text-bad" : "border-line hover:border-bad/60 hover:text-bad",
            )}
          >
            <X className="size-3.5" />
          </button>
          <div className="relative">
            <button
              onClick={() => setRegenOpen(!regenOpen)}
              disabled={v.status === "running" || !stages.length}
              aria-label={`Regenerate ${v.code}`}
              className="flex size-7 items-center justify-center rounded-md border border-line hover:border-muted disabled:opacity-40"
            >
              <RotateCcw className="size-3.5" />
            </button>
            {regenOpen && (
              <div className="absolute bottom-8 right-0 z-10 w-44 rounded-lg border border-line bg-panel-2 p-1 shadow-xl">
                <div className="px-2 py-1 text-[10px] uppercase tracking-wide text-muted">Re-render stage</div>
                {stages
                  .filter(([n]) => !["assemble", "compose"].includes(n))
                  .map(([name]) => (
                    <button
                      key={name}
                      onClick={() => {
                        setRegenOpen(false);
                        onRegen(name);
                      }}
                      className="block w-full rounded px-2 py-1.5 text-left font-mono text-xs hover:bg-panel"
                    >
                      {name}
                    </button>
                  ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
