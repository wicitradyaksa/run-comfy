import { AlertTriangle, Play, Zap } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api, money, type BatchView, type Estimate } from "../api";
import { Button, Card, cx, Stat, useToast } from "./ui";

const MODES = [
  { id: "pairwise", label: "Pairwise", hint: "Every pair of choices appears together at least once - the efficient default." },
  { id: "full", label: "Every combination", hint: "Full grid. Grows fast." },
  { id: "random", label: "Random sample", hint: "N random combinations." },
];

export function MatrixBuilder({ view, onChange, onRun }: { view: BatchView; onChange: (v: BatchView) => void; onRun: () => void }) {
  const toast = useToast();
  const axes = view.recipe_info.axes;
  const [selected, setSelected] = useState<Record<string, number[]>>(
    () => view.matrix.selected ?? Object.fromEntries(axes.map((a) => [a.key, (view.options[a.key] ?? []).map((_, i) => i)])),
  );
  const [mode, setMode] = useState(view.matrix.mode ?? "pairwise");
  const [n, setN] = useState<number>(view.matrix.n ?? 8);
  const [est, setEst] = useState<Estimate | null>(view.estimate ?? null);
  const [busy, setBusy] = useState(false);
  const first = useRef(true);
  const locked = view.variants.some((v) => v.status !== "pending");

  useEffect(() => {
    if (locked) {
      api.estimate(view.id).then(setEst).catch(() => {});
      return;
    }
    const t = setTimeout(
      async () => {
        setBusy(true);
        try {
          const v = await api.matrix(view.id, selected, mode, mode === "random" ? n : null);
          setEst(v.estimate ?? null);
          onChange(v);
        } catch (e) {
          toast((e as Error).message, "error");
        } finally {
          setBusy(false);
        }
      },
      first.current ? 0 : 350,
    );
    first.current = false;
    return () => clearTimeout(t);
  }, [selected, mode, n, locked]);

  const toggle = (axis: string, i: number) =>
    setSelected((s) => {
      const cur = s[axis] ?? [];
      return { ...s, [axis]: cur.includes(i) ? cur.filter((x) => x !== i) : [...cur, i].sort((a, b) => a - b) };
    });

  const saved = est ? est.usd_without_cache - est.usd : 0;
  return (
    <div className="grid gap-6 lg:grid-cols-[1fr_320px]">
      <div className="space-y-4">
        <Card className="p-4">
          <div className="mb-3 text-sm font-medium">How to combine</div>
          <div className="grid gap-2 sm:grid-cols-3">
            {MODES.map((m) => (
              <button
                key={m.id}
                type="button"
                disabled={locked}
                onClick={() => setMode(m.id)}
                className={cx("rounded-lg border p-3 text-left disabled:opacity-60", mode === m.id ? "border-accent bg-accent/10" : "border-line hover:border-muted/50")}
              >
                <div className="text-xs font-medium">{m.label}</div>
                <div className="mt-0.5 text-[11px] text-muted">{m.hint}</div>
              </button>
            ))}
          </div>
          {mode === "random" && (
            <label className="mt-3 flex items-center gap-3 text-xs text-muted">
              Variants
              <input type="range" min={1} max={50} value={n} disabled={locked} onChange={(e) => setN(Number(e.target.value))} className="flex-1 accent-violet-500" />
              <span className="w-6 text-right tabular-nums text-text">{n}</span>
            </label>
          )}
        </Card>
        {axes.map((a) => (
          <Card key={a.key} className="p-4">
            <div className="mb-2 flex items-center justify-between">
              <div className="text-sm font-medium">{a.label}</div>
              <div className="text-[11px] text-muted">
                {(selected[a.key] ?? []).length}/{(view.options[a.key] ?? []).length} selected
              </div>
            </div>
            <div className="space-y-1.5">
              {(view.options[a.key] ?? []).map((label, i) => {
                const on = (selected[a.key] ?? []).includes(i);
                return (
                  <label key={i} className={cx("flex cursor-pointer items-start gap-3 rounded-lg border px-3 py-2 text-sm", on ? "border-accent/50 bg-accent/5" : "border-line")}>
                    <input type="checkbox" className="mt-1 accent-violet-500" checked={on} disabled={locked} onChange={() => toggle(a.key, i)} />
                    <span className="w-5 shrink-0 text-[11px] tabular-nums text-muted">{i + 1}</span>
                    <span className="min-w-0 flex-1">{label}</span>
                  </label>
                );
              })}
              {(view.options[a.key] ?? []).length === 0 && <p className="text-xs text-muted">No options - edit the plan or upload assets.</p>}
            </div>
          </Card>
        ))}
      </div>

      <div className="space-y-3 lg:sticky lg:top-6 lg:self-start">
        <div className="grid grid-cols-2 gap-3">
          <Stat label="Variants" value={busy ? "…" : (est?.variants ?? view.variants.length)} />
          <Stat label="Est. cost" value={est ? money(est.usd) : "…"} sub={est?.backend} />
        </div>
        {est && (
          <Card className="space-y-3 p-4">
            <div className="text-sm font-medium">GPU renders</div>
            <div className="space-y-1.5">
              {Object.entries(est.renders).map(([wf, count]) => (
                <div key={wf} className="flex justify-between text-xs">
                  <span className="font-mono text-muted">{wf}</span>
                  <span className="tabular-nums">{count}×</span>
                </div>
              ))}
            </div>
            <div className="border-t border-line pt-3 text-xs text-muted">
              ~{Math.round(est.gpu_seconds / 60)} GPU-min
              {est.gpu_seconds_without_cache > est.gpu_seconds && (
                <div className="mt-1 flex items-center gap-1.5 text-ok">
                  <Zap className="size-3.5" />
                  Stage reuse saves {Math.round((1 - est.gpu_seconds / est.gpu_seconds_without_cache) * 100)}%
                  {saved > 0 && ` (${money(saved)})`}
                </div>
              )}
            </div>
            {est.warnings.length > 0 && (
              <div className="space-y-1.5 border-t border-line pt-3">
                {est.warnings.map((w) => (
                  <div key={w} className="flex gap-2 text-[11px] text-warn">
                    <AlertTriangle className="mt-0.5 size-3 shrink-0" />
                    {w}
                  </div>
                ))}
              </div>
            )}
          </Card>
        )}
        <Button
          variant="primary"
          className="w-full"
          disabled={busy || !est?.variants || locked}
          icon={<Play className="size-4" />}
          onClick={onRun}
        >
          Render {est?.variants ?? ""} variants
        </Button>
      </div>
    </div>
  );
}
