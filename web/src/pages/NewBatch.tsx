import { Clapperboard, Film, Image as ImageIcon, Sparkles, X } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { api, type Asset, type Brand, type Recipe, type Status } from "../api";
import { Uploader } from "../components/Uploader";
import { Button, Card, cx, Field, inputCls, useToast } from "../components/ui";

const ICONS: Record<string, ReactNode> = {
  ugc: <Clapperboard className="size-5" />,
  product: <ImageIcon className="size-5" />,
  clip: <Film className="size-5" />,
};
const ASPECTS = ["9:16", "4:5", "1:1", "16:9"];
const BACKENDS = [
  { id: "demo", label: "Demo", hint: "placeholder media, free" },
  { id: "serverless", label: "Serverless", hint: "RunPod endpoint, per-second" },
  { id: "pod", label: "Pods", hint: "rent GPUs for the batch" },
  { id: "url", label: "My ComfyUI", hint: "existing server" },
];

export default function NewBatch() {
  const nav = useNavigate();
  const toast = useToast();
  const [recipes, setRecipes] = useState<Recipe[]>([]);
  const [brands, setBrands] = useState<Brand[]>([]);
  const [status, setStatus] = useState<Status | null>(null);
  const [recipe, setRecipe] = useState("ugc");
  const [name, setName] = useState("");
  const [brief, setBrief] = useState("");
  const [url, setUrl] = useState("");
  const [brandId, setBrandId] = useState<number | "new">("new");
  const [quickBrand, setQuickBrand] = useState({ name: "", product: "", audience: "" });
  const [product, setProduct] = useState<Asset | null>(null);
  const [avatars, setAvatars] = useState<Asset[]>([]);
  const [face, setFace] = useState<Asset | null>(null);
  const [backend, setBackend] = useState("demo");
  const [aspects, setAspects] = useState<string[]>(["9:16", "1:1"]);
  const [budget, setBudget] = useState(25);
  const [voices, setVoices] = useState("");
  const [qa, setQa] = useState(true);
  const [lipsync, setLipsync] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.recipes().then(setRecipes);
    api.brands().then((b) => {
      setBrands(b);
      if (b.length) setBrandId(b[b.length - 1].id!);
    });
    api.status().then((s) => {
      setStatus(s);
      setBackend(s.backend);
      setBudget(s.budget_default);
    });
  }, []);

  const r = recipes.find((x) => x.name === recipe);
  const needsProduct = r?.requires.includes("product");

  async function submit() {
    if (!name.trim()) return toast("Give the batch a name", "error");
    if (needsProduct && !product) return toast("This recipe needs a product photo", "error");
    setBusy(true);
    try {
      let kitId = brandId;
      if (kitId === "new") {
        const b = await api.saveBrand({
          name: quickBrand.name || name,
          product: quickBrand.product,
          audience: quickBrand.audience,
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
          aspects,
        });
        kitId = b.id!;
      }
      const settings: Record<string, unknown> = { backend, aspects, max_spend_usd: budget, qa, lipsync };
      if (voices.trim()) settings.voices = voices.split(",").map((v) => v.trim()).filter(Boolean);
      const b = await api.createBatch({
        name,
        recipe,
        brief,
        url: url || null,
        brand_kit_id: kitId,
        product_asset_id: product?.id ?? null,
        avatar_asset_ids: avatars.map((a) => a.id),
        face_asset_id: face?.id ?? null,
        settings,
      });
      try {
        await api.writePlan(b.id);
      } catch (e) {
        toast(`Plan not written: ${(e as Error).message}`, "error");
      }
      nav(`/batches/${b.id}?tab=plan`);
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-xl font-semibold">New batch</h1>
        <p className="mt-1 text-sm text-muted">
          Describe the product once. {status?.keys.anthropic ? "Claude" : "The demo writer"} drafts hooks, scripts and
          visuals; you pick which combinations to render.
        </p>
      </header>

      <div className="grid gap-3 sm:grid-cols-3">
        {recipes.map((x) => (
          <button
            key={x.name}
            type="button"
            onClick={() => setRecipe(x.name)}
            className={cx(
              "rounded-xl border p-4 text-left transition-colors",
              recipe === x.name ? "border-accent bg-accent/10" : "border-line bg-panel hover:border-muted/50",
            )}
          >
            <div className={cx("mb-2", recipe === x.name ? "text-accent-2" : "text-muted")}>{ICONS[x.name]}</div>
            <div className="text-sm font-medium">{x.title}</div>
            <div className="mt-1 text-xs text-muted">{x.description}</div>
          </button>
        ))}
      </div>

      <div className="grid gap-6 lg:grid-cols-[1fr_340px]">
        <Card className="space-y-4 p-5">
          <Field label="Batch name">
            <input className={inputCls} value={name} onChange={(e) => setName(e.target.value)} placeholder="Spring launch - serum" />
          </Field>
          <Field label="Brief" hint="Angle, offer, audience, anything the ads must say or avoid.">
            <textarea
              className={cx(inputCls, "min-h-28")}
              value={brief}
              onChange={(e) => setBrief(e.target.value)}
              placeholder="Launch our vitamin C serum to busy 25-40s. Lead with how fast it absorbs. 20% off this week."
            />
          </Field>
          <Field label="Product page URL (optional)" hint="Claude reads the page for facts, claims and tone.">
            <input className={inputCls} value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://…" />
          </Field>
          <Field label="Brand kit">
            <select className={inputCls} value={brandId} onChange={(e) => setBrandId(e.target.value === "new" ? "new" : Number(e.target.value))}>
              {brands.map((b) => (
                <option key={b.id} value={b.id}>
                  {b.name}
                </option>
              ))}
              <option value="new">+ Quick new brand</option>
            </select>
          </Field>
          {brandId === "new" && (
            <div className="grid gap-3 sm:grid-cols-3">
              <input className={inputCls} placeholder="Brand name" value={quickBrand.name} onChange={(e) => setQuickBrand({ ...quickBrand, name: e.target.value })} />
              <input className={inputCls} placeholder="Product (one line)" value={quickBrand.product} onChange={(e) => setQuickBrand({ ...quickBrand, product: e.target.value })} />
              <input className={inputCls} placeholder="Audience" value={quickBrand.audience} onChange={(e) => setQuickBrand({ ...quickBrand, audience: e.target.value })} />
            </div>
          )}
          <div className="grid gap-4 sm:grid-cols-2">
            <Field group label={needsProduct ? "Product photo (required)" : "Product photo (for b-roll)"}>
              {product ? (
                <AssetChip asset={product} onRemove={() => setProduct(null)} />
              ) : (
                <Uploader kind="product" label="Drop product photo" onUploaded={setProduct} />
              )}
            </Field>
            {recipe === "ugc" && (
              <Field group label="Presenter photos" hint="Your actors (with consent). Leave empty for AI presenters.">
                <div className="space-y-2">
                  {avatars.map((a) => (
                    <AssetChip key={a.id} asset={a} onRemove={() => setAvatars(avatars.filter((x) => x.id !== a.id))} />
                  ))}
                  <Uploader kind="avatar" label="Add presenter" multiple compact onUploaded={(a) => setAvatars((v) => [...v, a])} />
                </div>
              </Field>
            )}
          </div>
          {recipe === "ugc" && (
            <Field group label="Face reference (optional)" hint="Keeps one consistent AI presenter across every variant (PuLID).">
              {face ? <AssetChip asset={face} onRemove={() => setFace(null)} /> : <Uploader kind="face" label="Drop a face photo" compact onUploaded={setFace} />}
            </Field>
          )}
        </Card>

        <div className="space-y-4">
          <Card className="space-y-4 p-5">
            <Field group label="Render on">
              <div className="grid grid-cols-2 gap-2">
                {BACKENDS.map((b) => (
                  <button
                    key={b.id}
                    type="button"
                    onClick={() => setBackend(b.id)}
                    className={cx(
                      "rounded-lg border px-3 py-2 text-left",
                      backend === b.id ? "border-accent bg-accent/10" : "border-line hover:border-muted/50",
                    )}
                  >
                    <div className="text-xs font-medium">{b.label}</div>
                    <div className="text-[10px] text-muted">{b.hint}</div>
                  </button>
                ))}
              </div>
            </Field>
            <Field group label="Aspect ratios" hint="Each variant is exported in every ratio (no extra GPU cost).">
              <div className="flex flex-wrap gap-2">
                {ASPECTS.map((a) => (
                  <button
                    key={a}
                    type="button"
                    onClick={() => setAspects(aspects.includes(a) ? aspects.filter((x) => x !== a) : [...aspects, a])}
                    className={cx(
                      "rounded-md border px-2.5 py-1 text-xs tabular-nums",
                      aspects.includes(a) ? "border-accent bg-accent/15 text-text" : "border-line text-muted",
                    )}
                  >
                    {a}
                  </button>
                ))}
              </div>
            </Field>
            <Field label="Budget cap (USD)" hint="The batch stops and releases GPUs when it's reached.">
              <input type="number" min={1} className={inputCls} value={budget} onChange={(e) => setBudget(Number(e.target.value))} />
            </Field>
            {status?.voice_provider === "elevenlabs" && recipe !== "product" && (
              <Field label="ElevenLabs voice IDs" hint="Comma separated; each becomes a test axis.">
                <input className={inputCls} value={voices} onChange={(e) => setVoices(e.target.value)} placeholder="21m00Tcm4TlvDq8ikWAM, …" />
              </Field>
            )}
            <Toggle checked={qa} onChange={setQa} label="Auto QA" hint="Claude reviews each ad and re-renders the weak stage" />
            {recipe === "ugc" && <Toggle checked={lipsync} onChange={setLipsync} label="Lip-sync polish" hint="Extra LatentSync pass" />}
          </Card>
          <Button variant="primary" className="w-full" loading={busy} icon={<Sparkles className="size-4" />} onClick={submit}>
            Create and write plan
          </Button>
        </div>
      </div>
    </div>
  );
}

function AssetChip({ asset, onRemove }: { asset: Asset; onRemove: () => void }) {
  return (
    <div className="flex items-center gap-3 rounded-lg border border-line bg-bg p-2">
      <img src={asset.url} alt="" className="size-12 rounded-md object-cover" />
      <span className="flex-1 truncate text-xs">{asset.name}</span>
      <button type="button" onClick={onRemove} className="p-1 text-muted hover:text-text" aria-label={`Remove ${asset.name}`}>
        <X className="size-4" />
      </button>
    </div>
  );
}

export function Toggle({ checked, onChange, label, hint }: { checked: boolean; onChange: (v: boolean) => void; label: string; hint?: string }) {
  return (
    <label className="flex cursor-pointer items-start gap-3">
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={label}
        onClick={() => onChange(!checked)}
        className={cx("mt-0.5 h-5 w-9 shrink-0 rounded-full p-0.5 transition-colors", checked ? "bg-accent" : "bg-line")}
      >
        <span className={cx("block size-4 rounded-full bg-white transition-transform", checked && "translate-x-4")} />
      </button>
      <span>
        <span className="block text-sm">{label}</span>
        {hint && <span className="block text-[11px] text-muted">{hint}</span>}
      </span>
    </label>
  );
}
