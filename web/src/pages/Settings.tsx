import { CircleCheck, CircleX, Loader2, Server } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { api, money, type InfraStatus, type Status } from "../api";
import { Button, Card, cx, Modal, useToast } from "../components/ui";

function Row({ ok, label, children }: { ok: boolean; label: string; children: ReactNode }) {
  return (
    <div className="flex items-start gap-3 border-b border-line py-3 last:border-0">
      {ok ? <CircleCheck className="mt-0.5 size-4 text-ok" /> : <CircleX className="mt-0.5 size-4 text-muted" />}
      <div>
        <div className="text-sm">{label}</div>
        <div className="text-xs text-muted">{children}</div>
      </div>
    </div>
  );
}

export default function SettingsPage({ status }: { status: Status | null }) {
  if (!status) return <p className="text-sm text-muted">API offline - start it with `python -m run_comfy serve`.</p>;
  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-xl font-semibold">Settings</h1>
        <p className="mt-1 text-sm text-muted">
          Configured in <code className="text-text">config.toml</code> and <code className="text-text">.env</code>; restart the server after changes.
        </p>
      </header>
      <Card className="px-5">
        <Row ok={status.keys.anthropic} label="Claude writer & QA">
          {status.keys.anthropic ? `Using ${status.model}.` : "ANTHROPIC_API_KEY not set - using the demo writer and technical-only QA."}
        </Row>
        <Row ok={status.keys.runpod} label="RunPod">
          {status.keys.runpod ? "API key present." : "RUNPOD_API_KEY not set - only demo and existing-ComfyUI backends work."}
        </Row>
        <Row ok={status.serverless_endpoint} label="Serverless endpoint">
          {status.serverless_endpoint ? "Configured." : "Created automatically by RunPod setup below, or by your first serverless batch."}
        </Row>
        <Row ok={status.keys.elevenlabs} label="ElevenLabs voices">
          Voice provider: {status.voice_provider}. {status.keys.elevenlabs ? "Key present." : "ELEVENLABS_API_KEY not set."}
        </Row>
        <Row ok={status.storage} label="S3 / R2 export upload">
          {status.storage ? "Exports are uploaded and linked." : "Exports stay local ([storage] in config.toml)."}
        </Row>
        <Row ok={status.webhooks} label="Webhooks">
          {status.webhooks ? "Signed batch/variant events are delivered." : "Set [webhooks].url to get Standard-Webhooks events."}
        </Row>
        <Row ok label="Default backend">
          {status.backend} · {status.workflows} workflows registered · default budget ${status.budget_default}
        </Row>
      </Card>
      <InfraCard defaultBackend={status.backend === "pod" ? "pod" : "serverless"} hasKey={status.keys.runpod} />
    </div>
  );
}

function InfraCard({ defaultBackend, hasKey }: { defaultBackend: string; hasKey: boolean }) {
  const toast = useToast();
  const [backend, setBackend] = useState(defaultBackend);
  const [st, setSt] = useState<InfraStatus | null>(null);
  const [confirm, setConfirm] = useState(false);

  useEffect(() => {
    let alive = true;
    const load = () => api.infra(backend).then((x) => alive && setSt(x)).catch(() => {});
    load();
    const t = setInterval(load, st?.running ? 1500 : 6000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [backend, st?.running]);

  if (!st) return null;
  const p = st.plan;
  const start = async () => {
    setConfirm(false);
    try {
      await api.setupInfra(backend, false);
      setSt({ ...st, running: true, log: [], error: "", actions: [] });
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };

  return (
    <Card className="space-y-4 p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-center gap-2">
          <Server className="size-4 text-accent-2" />
          <div>
            <div className="text-sm font-medium">RunPod infrastructure</div>
            <div className="text-xs text-muted">Paid batches set this up automatically; you can also do it ahead of time.</div>
          </div>
        </div>
        <div className="flex gap-2">
          {["serverless", "pod"].map((b) => (
            <button
              key={b}
              onClick={() => setBackend(b)}
              className={cx("rounded-md border px-2.5 py-1 text-xs capitalize", backend === b ? "border-accent bg-accent/10" : "border-line text-muted")}
            >
              {b}
            </button>
          ))}
        </div>
      </div>
      <div>
        <Row ok={!p.creates_volume} label="Model volume">
          {p.creates_volume ? `Will create ${p.volume_gb} GB in ${p.data_center} (~${money(p.volume_monthly_usd)}/month).` : `${p.volume_id} in ${p.data_center}`}
        </Row>
        <Row ok={p.missing_models.length === 0} label="Models">
          {p.missing_models.length
            ? `${p.missing_models.length} files to download (~${p.download_gb} GB, ~${p.est_minutes} min, ~${money(p.est_usd)}).`
            : "Everything the workflows need is on the volume."}
        </Row>
        <Row ok={!!p.image} label="Worker image">
          {p.image || "Needs [serverless].custom_image_repo"}
          {p.custom_nodes.length > 0 && ` · ${p.custom_nodes.length} custom nodes (built by the build-worker GitHub Action)`}
        </Row>
        {backend === "serverless" && (
          <Row ok={!p.creates_endpoint} label="Serverless endpoint">
            {p.creates_endpoint ? "Will be created (scales to zero, no idle cost)." : "Configured."}
          </Row>
        )}
      </div>
      {p.warnings.map((w) => (
        <p key={w} className="text-xs text-warn">
          ! {w}
        </p>
      ))}
      {(st.running || st.log.length > 0 || st.error || st.actions.length > 0) && (
        <div className="space-y-1 rounded-lg border border-line bg-bg p-3 text-xs">
          {st.log.map((l, i) => (
            <div key={i} className="text-muted">
              {l.message}
            </div>
          ))}
          {st.running && (
            <div className="flex items-center gap-2 text-accent-2">
              <Loader2 className="size-3.5 animate-spin" /> working…
            </div>
          )}
          {st.actions.map((a) => (
            <div key={a} className="text-ok">
              ✓ {a}
            </div>
          ))}
          {st.error && <div className="text-bad">{st.error}</div>}
        </div>
      )}
      <div className="flex justify-end">
        <Button variant="primary" disabled={p.ready || st.running || !hasKey} onClick={() => setConfirm(true)}>
          {p.ready ? "Ready" : st.running ? "Setting up…" : "Set up now"}
        </Button>
      </div>
      <Modal open={confirm} onClose={() => setConfirm(false)} title="Set up RunPod">
        <div className="space-y-3 text-sm">
          <p className="text-muted">This creates paid resources in your RunPod account:</p>
          <ul className="list-disc space-y-1 pl-5 text-xs">
            {p.creates_volume && <li>{p.volume_gb} GB network volume (~{money(p.volume_monthly_usd)}/month while it exists)</li>}
            {p.missing_models.length > 0 && <li>a temporary download pod for ~{p.est_minutes} min (~{money(p.est_usd)})</li>}
            {backend === "serverless" && p.creates_endpoint && <li>a serverless endpoint (free while idle)</li>}
          </ul>
          <div className="flex justify-end gap-2">
            <Button onClick={() => setConfirm(false)}>Cancel</Button>
            <Button variant="primary" onClick={start}>
              Create
            </Button>
          </div>
        </div>
      </Modal>
    </Card>
  );
}
