import { Columns2, Download, Loader2, Play, Square } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { api, money, type BatchEvent, type BatchView } from "../api";
import { MatrixBuilder } from "../components/MatrixBuilder";
import { PlanEditor } from "../components/PlanEditor";
import { Button, Card, cx, Modal, Pill, Stat, useToast } from "../components/ui";
import { Media, VariantCard } from "../components/VariantCard";

type Tab = "plan" | "matrix" | "variants";
type Progress = Record<number, { stage: string; progress: number; message?: string }>;

export default function BatchDetail() {
  const id = Number(useParams().id);
  const toast = useToast();
  const [view, setView] = useState<BatchView | null>(null);
  const [search] = useSearchParams();
  const [tab, setTab] = useState<Tab | null>((search.get("tab") as Tab) || null);
  const [progress, setProgress] = useState<Progress>({});
  const [log, setLog] = useState<BatchEvent[]>([]);
  const [filter, setFilter] = useState("all");
  const [compare, setCompare] = useState<number[]>([]);
  const [open, setOpen] = useState<number | null>(null);
  const [showCompare, setShowCompare] = useState(false);
  const [exporting, setExporting] = useState(false);
  const refetchTimer = useRef<number | undefined>(undefined);

  const load = useCallback(async () => {
    const v = await api.batch(id);
    setView(v);
    setTab((t) => t ?? (!v.plan || !("hooks" in v.plan) ? "plan" : v.variants.some((x) => x.status !== "pending") ? "variants" : "matrix"));
  }, [id]);

  useEffect(() => {
    load().catch((e) => toast((e as Error).message, "error"));
  }, [load, toast]);

  // live updates over server-sent events
  useEffect(() => {
    const es = new EventSource(`/api/batches/${id}/events`);
    es.onmessage = (m) => {
      const e = JSON.parse(m.data) as BatchEvent;
      if (e.type === "progress" && e.variant_id) {
        setProgress((p) => ({ ...p, [e.variant_id!]: { stage: e.stage!, progress: e.progress ?? 0, message: e.message } }));
        return;
      }
      if (e.type === "stage" && e.variant_id && e.status === "running") {
        setProgress((p) => ({ ...p, [e.variant_id!]: { stage: e.stage!, progress: 0 } }));
      }
      setLog((l) => [e, ...l].slice(0, 200));
      window.clearTimeout(refetchTimer.current);
      refetchTimer.current = window.setTimeout(() => load().catch(() => {}), e.type === "batch" ? 0 : 400);
      if (e.type === "batch" && e.status && e.status !== "running") {
        toast(`Batch ${e.status}${e.error ? `: ${e.error}` : ""}`, e.status === "done" ? "ok" : "error");
      }
    };
    return () => es.close();
  }, [id, load, toast]);

  const counts = useMemo(() => {
    const vs = view?.variants ?? [];
    return {
      total: vs.length,
      done: vs.filter((v) => v.status === "done").length,
      failed: vs.filter((v) => v.status === "failed").length,
      approved: vs.filter((v) => v.review === "approved").length,
      running: vs.filter((v) => v.status === "running").length,
    };
  }, [view]);

  if (!view)
    return (
      <div className="grid place-items-center py-32 text-muted">
        <Loader2 className="size-5 animate-spin" />
      </div>
    );

  const running = view.status === "running" || view.status === "provisioning";
  const budget = Number(view.settings.max_spend_usd ?? 25);
  const pending = view.variants.filter((v) => v.status !== "done").length;
  const visible = view.variants.filter((v) =>
    filter === "all" ? true : filter === "approved" ? v.review === "approved" : filter === "rejected" ? v.review === "rejected" : v.status === filter,
  );

  async function run() {
    try {
      await api.run(id);
      setTab("variants");
      setView((v) => (v ? { ...v, status: "running" } : v));
    } catch (e) {
      toast((e as Error).message, "error");
    }
  }

  async function exportZip(approvedOnly: boolean) {
    setExporting(true);
    try {
      const r = await api.exportBatch(id, approvedOnly);
      if (!r.files) toast(approvedOnly ? "Approve some variants first" : "Nothing rendered yet", "error");
      else {
        toast(`Exported ${r.files} files`);
        window.location.href = r.download;
      }
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setExporting(false);
    }
  }

  const opened = view.variants.find((v) => v.id === open);

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-xl font-semibold">{view.name}</h1>
            <Pill status={view.status} />
          </div>
          <p className="mt-1 text-sm text-muted">
            {view.recipe_info.title} · {view.brand.name} · renders on <span className="text-text">{String(view.settings.backend ?? "default")}</span>
          </p>
          {view.error && <p className="mt-1 text-xs text-bad">{view.error}</p>}
        </div>
        <div className="flex flex-wrap gap-2">
          {running ? (
            <Button variant="danger" icon={<Square className="size-4" />} onClick={() => api.cancel(id)}>
              Stop
            </Button>
          ) : (
            pending > 0 &&
            view.variants.some((v) => v.status !== "pending") && (
              <Button icon={<Play className="size-4" />} onClick={run}>
                Render remaining {pending}
              </Button>
            )
          )}
          <Button loading={exporting} disabled={!counts.done} icon={<Download className="size-4" />} onClick={() => exportZip(counts.approved > 0)}>
            Export {counts.approved > 0 ? `${counts.approved} approved` : "all"}
          </Button>
        </div>
      </header>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Rendered" value={`${counts.done}/${counts.total}`} sub={counts.running ? `${counts.running} in progress` : counts.failed ? `${counts.failed} failed` : undefined} />
        <Stat label="Approved" value={counts.approved} />
        <Stat
          label="GPU spend"
          value={money(view.cost_usd)}
          sub={
            <div className="mt-1 h-1 overflow-hidden rounded-full bg-line">
              <div className={cx("h-full", view.cost_usd / budget > 0.8 ? "bg-warn" : "bg-accent")} style={{ width: `${Math.min(100, (view.cost_usd / budget) * 100)}%` }} />
            </div>
          }
        />
        <Stat label="Budget" value={money(budget)} sub="hard stop" />
      </div>

      <div className="flex gap-1 border-b border-line">
        {(["plan", "matrix", "variants"] as Tab[]).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={cx("-mb-px border-b-2 px-4 py-2 text-sm capitalize", tab === t ? "border-accent text-text" : "border-transparent text-muted hover:text-text")}
          >
            {t === "plan" ? "1 · Plan" : t === "matrix" ? "2 · Test matrix" : `3 · Variants (${counts.total})`}
          </button>
        ))}
      </div>

      {tab === "plan" && (
        <PlanEditor
          view={view}
          onSaved={(v) => {
            setView(v);
            setTab("matrix");
          }}
        />
      )}
      {tab === "matrix" &&
        (view.plan && "hooks" in view.plan ? (
          <MatrixBuilder view={view} onChange={setView} onRun={run} />
        ) : (
          <Card className="p-8 text-center text-sm text-muted">Write and save the plan first.</Card>
        ))}
      {tab === "variants" && (
        <div className="grid gap-6 xl:grid-cols-[1fr_260px]">
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-2">
              {["all", "done", "running", "failed", "approved", "rejected"].map((f) => (
                <button
                  key={f}
                  onClick={() => setFilter(f)}
                  className={cx("rounded-full border px-3 py-1 text-xs capitalize", filter === f ? "border-accent bg-accent/10" : "border-line text-muted hover:text-text")}
                >
                  {f}
                </button>
              ))}
              <div className="ml-auto">
                <Button size="sm" disabled={compare.length < 2} icon={<Columns2 className="size-3.5" />} onClick={() => setShowCompare(true)}>
                  Compare {compare.length || ""}
                </Button>
              </div>
            </div>
            {visible.length === 0 ? (
              <Card className="p-10 text-center text-sm text-muted">Nothing here yet.</Card>
            ) : (
              <div className="grid grid-cols-2 gap-3 md:grid-cols-3 2xl:grid-cols-4">
                {visible.map((v) => (
                  <VariantCard
                    key={v.id}
                    v={v}
                    progress={progress[v.id]}
                    selected={compare.includes(v.id)}
                    onSelect={() => setCompare((c) => (c.includes(v.id) ? c.filter((x) => x !== v.id) : [...c, v.id].slice(-4)))}
                    onOpen={() => setOpen(v.id)}
                    onReview={async (d) => {
                      await api.review(v.id, d);
                      load();
                    }}
                    onRegen={async (stage) => {
                      try {
                        await api.regenerate(v.id, stage);
                        toast(`Re-rendering ${stage} for ${v.code}`);
                      } catch (e) {
                        toast((e as Error).message, "error");
                      }
                    }}
                  />
                ))}
              </div>
            )}
          </div>
          <Card className="h-fit max-h-[70vh] overflow-auto p-3 scroll-thin">
            <div className="mb-2 text-xs font-medium text-muted">Activity</div>
            {log.length === 0 && <p className="text-xs text-muted">Live events appear here while the batch renders.</p>}
            <ul className="space-y-1.5">
              {log.map((e, i) => (
                <li key={i} className="text-[11px] leading-snug">
                  <span className="mr-1.5 tabular-nums text-muted">{new Date(e.ts * 1000).toLocaleTimeString()}</span>
                  {e.type === "setup" ? (
                    <span className="text-accent-2">setup: {e.message}</span>
                  ) : e.type === "stage" ? (
                    <>
                      <span className="font-mono">{e.stage}</span>{" "}
                      <span className={e.status === "failed" ? "text-bad" : e.cached ? "text-accent-2" : "text-muted"}>
                        {e.cached ? "reused" : e.status}
                      </span>
                    </>
                  ) : e.type === "variant" ? (
                    <>
                      <span className="font-mono">{e.code}</span>{" "}
                      <span className={e.status === "failed" ? "text-bad" : e.status === "done" ? "text-ok" : "text-muted"}>{e.message ?? e.status}</span>
                    </>
                  ) : (
                    <span className="font-medium">batch {e.status}</span>
                  )}
                </li>
              ))}
            </ul>
          </Card>
        </div>
      )}

      <Modal open={!!opened} onClose={() => setOpen(null)} title={opened ? `${opened.code}` : ""} wide>
        {opened && (
          <div className="grid gap-6 md:grid-cols-[minmax(0,1fr)_300px]">
            <div className="grid gap-3" style={{ gridTemplateColumns: `repeat(${Math.max(1, Object.keys(opened.outputs).length)}, minmax(0, 1fr))` }}>
              {Object.entries(opened.outputs).map(([a, p]) => (
                <div key={a}>
                  <div className="mb-1 text-[11px] text-muted">{a}</div>
                  <div className="overflow-hidden rounded-lg bg-black">
                    <Media path={p} controls />
                  </div>
                </div>
              ))}
            </div>
            <div className="space-y-4 text-sm">
              {Object.entries(opened.labels).map(([k, l]) => (
                <div key={k}>
                  <div className="text-[11px] uppercase tracking-wide text-muted">{k}</div>
                  <div>{l}</div>
                </div>
              ))}
              {opened.qa && (opened.qa.summary || opened.qa.technical?.length || opened.qa.issues?.length) ? (
                <div className="rounded-lg border border-line p-3 text-xs">
                  <div className="mb-1 font-medium">QA {opened.qa.score != null ? `${opened.qa.score}/10` : ""}</div>
                  {opened.qa.summary && <p className="text-muted">{opened.qa.summary}</p>}
                  {[...(opened.qa.technical ?? []), ...(opened.qa.issues ?? []), ...(opened.qa.brand_violations ?? [])].map((x) => (
                    <p key={x} className="text-warn">
                      · {x}
                    </p>
                  ))}
                </div>
              ) : null}
            </div>
          </div>
        )}
      </Modal>

      <Modal open={showCompare} onClose={() => setShowCompare(false)} title="Compare variants" wide>
        <div className="grid gap-4" style={{ gridTemplateColumns: `repeat(${compare.length}, minmax(0, 1fr))` }}>
          {compare
            .map((cid) => view.variants.find((v) => v.id === cid))
            .filter(Boolean)
            .map((v) => {
              const p = Object.values(v!.outputs)[0];
              return (
                <div key={v!.id} className="space-y-2">
                  <div className="font-mono text-xs">{v!.code}</div>
                  <div className="aspect-[9/16] overflow-hidden rounded-lg bg-black">{p && <Media path={p} autoPlay />}</div>
                  {Object.entries(v!.labels).map(([k, l]) => (
                    <div key={k} className="text-[11px]">
                      <span className="text-muted">{k}: </span>
                      {l}
                    </div>
                  ))}
                </div>
              );
            })}
        </div>
      </Modal>
    </div>
  );
}
