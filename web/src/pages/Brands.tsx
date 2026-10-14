import { Palette, Plus, Save } from "lucide-react";
import { useEffect, useState } from "react";
import { api, type Asset, type Brand } from "../api";
import { Uploader } from "../components/Uploader";
import { Button, Card, cx, Empty, Field, inputCls, useToast } from "../components/ui";

const BLANK: Brand = {
  name: "New brand",
  product: "",
  audience: "",
  facts: [],
  banned: [],
  tone: "friendly, direct, conversational UGC",
  language: "English",
  colors: ["#111111", "#ffffff", "#8b5cf6"],
  cta_default: "Shop now",
  logo_asset_id: null,
  endcard_asset_id: null,
  music_asset_id: null,
  caption_style: null,
  aspects: ["9:16", "1:1", "16:9"],
};

export default function Brands() {
  const toast = useToast();
  const [brands, setBrands] = useState<Brand[]>([]);
  const [edit, setEdit] = useState<Brand | null>(null);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.brands().then((b) => {
      setBrands(b);
      if (b.length) setEdit(b[0]);
    });
    api.assets().then(setAssets);
  }, []);

  async function save() {
    if (!edit) return;
    setBusy(true);
    try {
      const saved = await api.saveBrand(edit);
      const list = await api.brands();
      setBrands(list);
      setEdit(list.find((b) => b.id === saved.id) ?? null);
      toast("Brand kit saved");
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  }

  const assetName = (id: number | null) => assets.find((a) => a.id === id);

  return (
    <div className="space-y-6">
      <header className="flex items-end justify-between">
        <div>
          <h1 className="text-xl font-semibold">Brand kits</h1>
          <p className="mt-1 text-sm text-muted">Facts the writer may use, claims it must never make, and the look of every ad.</p>
        </div>
        <Button icon={<Plus className="size-4" />} onClick={() => setEdit({ ...BLANK })}>
          New kit
        </Button>
      </header>
      <div className="grid gap-6 lg:grid-cols-[220px_1fr]">
        <div className="space-y-1">
          {brands.map((b) => (
            <button
              key={b.id}
              onClick={() => setEdit(b)}
              className={cx("flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm", edit?.id === b.id ? "bg-panel-2" : "text-muted hover:bg-panel-2/60")}
            >
              <span className="flex">
                {b.colors.slice(0, 3).map((c) => (
                  <span key={c} className="-ml-1 size-3 rounded-full border border-bg first:ml-0" style={{ background: c }} />
                ))}
              </span>
              <span className="truncate">{b.name}</span>
            </button>
          ))}
        </div>
        {!edit ? (
          <Empty icon={<Palette className="size-6" />} title="No brand kit yet">
            Create one so every batch stays on-brand.
          </Empty>
        ) : (
          <Card className="space-y-5 p-5">
            <div className="grid gap-4 md:grid-cols-2">
              <Field label="Brand name">
                <input className={inputCls} value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} />
              </Field>
              <Field label="Product (one line)">
                <input className={inputCls} value={edit.product} onChange={(e) => setEdit({ ...edit, product: e.target.value })} />
              </Field>
              <Field label="Audience">
                <input className={inputCls} value={edit.audience} onChange={(e) => setEdit({ ...edit, audience: e.target.value })} />
              </Field>
              <Field label="Tone of voice">
                <input className={inputCls} value={edit.tone} onChange={(e) => setEdit({ ...edit, tone: e.target.value })} />
              </Field>
              <Field label="Approved facts" hint="One per line. The writer only states these.">
                <textarea className={cx(inputCls, "min-h-28")} value={edit.facts.join("\n")} onChange={(e) => setEdit({ ...edit, facts: e.target.value.split("\n") })} />
              </Field>
              <Field label="Banned words & claims" hint="One per line. Lines containing them are removed; QA flags them.">
                <textarea className={cx(inputCls, "min-h-28")} value={edit.banned.join("\n")} onChange={(e) => setEdit({ ...edit, banned: e.target.value.split("\n") })} />
              </Field>
              <Field label="Default CTA">
                <input className={inputCls} value={edit.cta_default} onChange={(e) => setEdit({ ...edit, cta_default: e.target.value })} />
              </Field>
              <Field label="Language">
                <input className={inputCls} value={edit.language} onChange={(e) => setEdit({ ...edit, language: e.target.value })} />
              </Field>
              <Field group label="Colours" hint="Background, text, accent (captions highlight + CTA).">
                <div className="flex gap-3">
                  {[0, 1, 2].map((i) => (
                    <input
                      key={i}
                      type="color"
                      aria-label={["Background", "Text", "Accent"][i]}
                      value={edit.colors[i] ?? "#000000"}
                      onChange={(e) => {
                        const colors = [...edit.colors];
                        colors[i] = e.target.value;
                        setEdit({ ...edit, colors });
                      }}
                      className="h-9 w-14 cursor-pointer rounded-md border border-line bg-bg"
                    />
                  ))}
                </div>
              </Field>
              <Field label="Caption style">
                <select className={inputCls} value={edit.caption_style ?? ""} onChange={(e) => setEdit({ ...edit, caption_style: e.target.value || null })}>
                  <option value="">Default</option>
                  <option value="bold_pop">Bold pop (TikTok)</option>
                  <option value="karaoke">Karaoke fill</option>
                  <option value="minimal">Minimal</option>
                  <option value="none">None</option>
                </select>
              </Field>
            </div>
            <div className="grid gap-4 md:grid-cols-3">
              {(
                [
                  ["logo", "logo_asset_id", "Logo (PNG)", "image/*"],
                  ["music", "music_asset_id", "Music bed", "audio/*"],
                  ["endcard", "endcard_asset_id", "End card image", "image/*"],
                ] as const
              ).map(([kind, key, label, accept]) => {
                const a = assetName(edit[key]);
                return (
                  <Field group key={kind} label={label}>
                    {a ? (
                      <div className="flex items-center justify-between rounded-lg border border-line bg-bg px-3 py-2 text-xs">
                        <span className="truncate">{a.name}</span>
                        <button className="text-muted hover:text-text" onClick={() => setEdit({ ...edit, [key]: null })}>
                          Remove
                        </button>
                      </div>
                    ) : (
                      <Uploader
                        kind={kind}
                        accept={accept}
                        label={`Upload ${label.toLowerCase()}`}
                        compact
                        onUploaded={(as) => {
                          setAssets((x) => [...x, as]);
                          setEdit({ ...edit, [key]: as.id });
                        }}
                      />
                    )}
                  </Field>
                );
              })}
            </div>
            <div className="flex justify-end">
              <Button
                variant="primary"
                loading={busy}
                icon={<Save className="size-4" />}
                onClick={() => {
                  setEdit({ ...edit, facts: edit.facts.filter(Boolean), banned: edit.banned.filter(Boolean) });
                  save();
                }}
              >
                Save brand kit
              </Button>
            </div>
          </Card>
        )}
      </div>
    </div>
  );
}
