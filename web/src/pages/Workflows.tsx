import { CircleCheck, CircleDashed, ShieldAlert } from "lucide-react";
import { useEffect, useState } from "react";
import { api, type Workflow } from "../api";
import { Card } from "../components/ui";

export default function Workflows() {
  const [items, setItems] = useState<Workflow[]>([]);
  useEffect(() => {
    api.workflows().then(setItems);
  }, []);

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-xl font-semibold">Workflows</h1>
        <p className="mt-1 text-sm text-muted">
          Versioned ComfyUI graphs with typed inputs. Verify on a live GPU with <code className="text-text">run_comfy workflows check --url …</code>
        </p>
      </header>
      <div className="grid gap-3 md:grid-cols-2">
        {items.map((w) => (
          <Card key={w.name} className="space-y-3 p-4">
            <div className="flex items-start justify-between gap-3">
              <div>
                <div className="font-mono text-sm">{w.name}</div>
                <div className="mt-0.5 text-xs text-muted">{w.description.split(". ")[0]}</div>
              </div>
              <span className="shrink-0 rounded-md bg-panel-2 px-2 py-0.5 text-[11px] text-muted">{w.kind}</span>
            </div>
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-muted">
              <span className="flex items-center gap-1">
                {w.verified ? <CircleCheck className="size-3.5 text-ok" /> : <CircleDashed className="size-3.5 text-warn" />}
                {w.verified ? "verified on GPU" : "not yet verified"}
              </span>
              <span>v{w.version}</span>
              <span>≥{w.min_vram_gb} GB VRAM</span>
              <span>~{w.est_seconds}s / run</span>
              {!w.commercial_ok && (
                <span className="flex items-center gap-1 text-warn">
                  <ShieldAlert className="size-3.5" /> licence
                </span>
              )}
            </div>
            <div className="flex flex-wrap gap-1.5">
              {w.inputs.map((i) => (
                <span key={i.name} className="rounded-md border border-line px-1.5 py-0.5 font-mono text-[10px]" title={i.node ?? ""}>
                  {i.name}
                  <span className="text-muted">:{i.type}</span>
                  {i.derive && <span className="text-accent-2"> ←{i.derive.split(":")[1]}</span>}
                </span>
              ))}
            </div>
            <div className="text-[11px] text-muted" title={w.license}>
              {w.models.length} models · {w.custom_nodes.length} custom nodes · {w.license}
            </div>
          </Card>
        ))}
      </div>
    </div>
  );
}
