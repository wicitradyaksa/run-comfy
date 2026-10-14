import { Plus, Save, Sparkles, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { api, type BatchView, type Plan } from "../api";
import { Button, Card, cx, inputCls, useToast } from "./ui";

const LISTS: { key: keyof Plan; label: string; hint: string; recipes: string[]; multiline?: boolean }[] = [
  { key: "hooks", label: "Hooks", hint: "First 2 seconds. Each is a different angle.", recipes: ["ugc", "clip"] },
  { key: "bodies", label: "Scripts", hint: "Spoken by the presenter after the hook.", recipes: ["ugc"], multiline: true },
  { key: "ctas", label: "Calls to action", hint: "Closing line and end card.", recipes: ["ugc", "clip", "product"] },
  { key: "headlines", label: "Headlines", hint: "On-image text for statics.", recipes: ["product"] },
  { key: "presenter_prompts", label: "AI presenters", hint: "Used when no presenter photos are uploaded.", recipes: ["ugc"], multiline: true },
  { key: "scene_styles", label: "Scenes & styles", hint: "Backgrounds for product shots, b-roll and clips.", recipes: ["ugc", "product", "clip"], multiline: true },
];

export function PlanEditor({ view, onSaved }: { view: BatchView; onSaved: (v: BatchView) => void }) {
  const toast = useToast();
  const [plan, setPlan] = useState<Plan | null>(view.plan && "hooks" in view.plan ? (view.plan as Plan) : null);
  const [busy, setBusy] = useState<"write" | "save" | null>(null);
  const locked = view.variants.some((v) => v.status !== "pending");

  useEffect(() => {
    if (view.plan && "hooks" in view.plan) setPlan(view.plan as Plan);
  }, [view.plan]);

  async function write() {
    setBusy("write");
    try {
      onSaved(await api.writePlan(view.id));
      toast("Plan written");
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(null);
    }
  }

  async function save() {
    if (!plan) return;
    setBusy("save");
    try {
      onSaved(await api.setPlan(view.id, plan));
      toast("Plan saved - now choose what to test");
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(null);
    }
  }

  if (!plan)
    return (
      <Card className="flex flex-col items-center gap-3 p-10 text-center">
        <Sparkles className="size-6 text-accent-2" />
        <p className="text-sm">No plan yet.</p>
        <Button variant="primary" loading={busy === "write"} onClick={write}>
          Write hooks, scripts and visuals
        </Button>
      </Card>
    );

  const setList = (key: keyof Plan, items: string[]) => setPlan({ ...plan, [key]: items });

  return (
    <div className="space-y-4">
      <Card className="flex flex-wrap items-center gap-3 p-4">
        <div className="min-w-0 flex-1">
          <div className="text-[11px] uppercase tracking-wide text-muted">Angle</div>
          <input className={cx(inputCls, "mt-1")} value={plan.angle} disabled={locked} onChange={(e) => setPlan({ ...plan, angle: e.target.value })} />
        </div>
        <div className="flex gap-2">
          <Button loading={busy === "write"} disabled={locked} icon={<Sparkles className="size-4" />} onClick={write}>
            Rewrite
          </Button>
          <Button variant="primary" loading={busy === "save"} disabled={locked} icon={<Save className="size-4" />} onClick={save}>
            Save plan
          </Button>
        </div>
        {locked && <p className="w-full text-xs text-warn">Variants have been rendered - the plan is locked. Create a new batch to change it.</p>}
      </Card>
      <div className="grid gap-4 lg:grid-cols-2">
        {LISTS.filter((l) => l.recipes.includes(view.recipe)).map((l) => (
          <ListEditor
            key={l.key}
            label={l.label}
            hint={l.hint}
            items={plan[l.key] as string[]}
            multiline={l.multiline}
            disabled={locked}
            onChange={(items) => setList(l.key, items)}
          />
        ))}
        {view.recipe === "clip" && (
          <Card className="space-y-3 p-4 lg:col-span-2">
            <div className="text-sm font-medium">Scenes</div>
            {plan.scenes.map((s, i) => (
              <div key={i} className="grid gap-2 rounded-lg border border-line p-3 md:grid-cols-3">
                {(["visual", "motion", "line"] as const).map((k) => (
                  <textarea
                    key={k}
                    aria-label={`Scene ${i + 1} ${k}`}
                    className={cx(inputCls, "min-h-16 text-xs")}
                    value={s[k]}
                    disabled={locked}
                    placeholder={k}
                    onChange={(e) => {
                      const scenes = plan.scenes.map((x, j) => (j === i ? { ...x, [k]: e.target.value } : x));
                      setPlan({ ...plan, scenes });
                    }}
                  />
                ))}
              </div>
            ))}
          </Card>
        )}
      </div>
    </div>
  );
}

function ListEditor({
  label,
  hint,
  items,
  multiline,
  disabled,
  onChange,
}: {
  label: string;
  hint: string;
  items: string[];
  multiline?: boolean;
  disabled?: boolean;
  onChange: (v: string[]) => void;
}) {
  return (
    <Card className="space-y-2 p-4">
      <div className="flex items-center justify-between">
        <div>
          <div className="text-sm font-medium">
            {label} <span className="text-muted">({items.length})</span>
          </div>
          <div className="text-[11px] text-muted">{hint}</div>
        </div>
        <Button size="sm" variant="ghost" disabled={disabled} icon={<Plus className="size-3.5" />} onClick={() => onChange([...items, ""])}>
          Add
        </Button>
      </div>
      {items.map((it, i) => (
        <div key={i} className="group flex items-start gap-2">
          <span className="mt-2 w-5 shrink-0 text-right text-[11px] tabular-nums text-muted">{i + 1}</span>
          {multiline ? (
            <textarea
              aria-label={`${label} ${i + 1}`}
              className={cx(inputCls, "min-h-16")}
              value={it}
              disabled={disabled}
              onChange={(e) => onChange(items.map((x, j) => (j === i ? e.target.value : x)))}
            />
          ) : (
            <input
              aria-label={`${label} ${i + 1}`}
              className={inputCls}
              value={it}
              disabled={disabled}
              onChange={(e) => onChange(items.map((x, j) => (j === i ? e.target.value : x)))}
            />
          )}
          <button
            type="button"
            disabled={disabled}
            aria-label={`Remove ${label} ${i + 1}`}
            onClick={() => onChange(items.filter((_, j) => j !== i))}
            className="mt-2 text-muted opacity-0 hover:text-bad group-hover:opacity-100 disabled:hidden"
          >
            <Trash2 className="size-3.5" />
          </button>
        </div>
      ))}
    </Card>
  );
}
