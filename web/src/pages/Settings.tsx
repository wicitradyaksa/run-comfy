import { CircleCheck, CircleX } from "lucide-react";
import type { ReactNode } from "react";
import type { Status } from "../api";
import { Card } from "../components/ui";

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
          {status.serverless_endpoint ? "Configured." : "Run `python -m run_comfy serverless-setup` once."}
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
    </div>
  );
}
