import { Clapperboard, ImageIcon } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, fileUrl, isVideo, money, type BatchSummary } from "../api";
import { Empty, Pill } from "../components/ui";

export default function Batches() {
  const [items, setItems] = useState<BatchSummary[] | null>(null);
  useEffect(() => {
    const load = () => api.batches().then(setItems).catch(() => setItems([]));
    load();
    const t = setInterval(load, 4000);
    return () => clearInterval(t);
  }, []);

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-xl font-semibold">Batches</h1>
        <p className="mt-1 text-sm text-muted">Every batch turns one brief into a tested matrix of ad variants.</p>
      </header>
      {items === null ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <div key={i} className="h-64 animate-pulse rounded-xl border border-line bg-panel" />
          ))}
        </div>
      ) : items.length === 0 ? (
        <Empty icon={<Clapperboard className="size-6" />} title="No batches yet">
          Create one from a brief or a product URL. Without API keys everything runs in demo mode, so you can try the
          whole flow for free.
        </Empty>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {items.map((b) => (
            <Link
              key={b.id}
              to={`/batches/${b.id}`}
              className="group overflow-hidden rounded-xl border border-line bg-panel transition-colors hover:border-muted/50"
            >
              <div className="relative aspect-video bg-panel-2">
                {b.thumb ? (
                  // videos have a poster .jpg saved next to them
                  <img src={fileUrl(isVideo(b.thumb) ? b.thumb.replace(/\.\w+$/, ".jpg") : b.thumb)} alt="" className="size-full object-cover" />
                ) : (
                  <div className="grid size-full place-items-center text-muted">
                    <ImageIcon className="size-6" />
                  </div>
                )}
                <div className="absolute left-3 top-3">
                  <Pill status={b.status} />
                </div>
              </div>
              <div className="space-y-1 px-4 py-3">
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate text-sm font-medium group-hover:text-accent-2">{b.name}</span>
                  <span className="shrink-0 text-xs tabular-nums text-muted">{money(b.cost_usd)}</span>
                </div>
                <div className="flex gap-3 text-xs text-muted">
                  <span className="capitalize">{b.recipe}</span>
                  <span>
                    {b.done}/{b.variants} rendered
                  </span>
                  {b.approved > 0 && <span className="text-ok">{b.approved} approved</span>}
                  {b.failed > 0 && <span className="text-bad">{b.failed} failed</span>}
                </div>
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
