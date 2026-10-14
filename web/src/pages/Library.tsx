import { FolderOpen, Music, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { api, type Asset } from "../api";
import { Uploader } from "../components/Uploader";
import { cx, Empty } from "../components/ui";

const KINDS = ["avatar", "product", "face", "logo", "music", "endcard"];

export default function Library() {
  const [kind, setKind] = useState("avatar");
  const [items, setItems] = useState<Asset[]>([]);
  useEffect(() => {
    api.assets(kind).then(setItems);
  }, [kind]);

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-xl font-semibold">Library</h1>
        <p className="mt-1 text-sm text-muted">Presenters, products, faces, logos and music reused across batches.</p>
      </header>
      <div className="flex flex-wrap gap-2">
        {KINDS.map((k) => (
          <button
            key={k}
            onClick={() => setKind(k)}
            className={cx("rounded-full border px-3 py-1 text-xs capitalize", kind === k ? "border-accent bg-accent/10" : "border-line text-muted hover:text-text")}
          >
            {k}
          </button>
        ))}
      </div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-6">
        <Uploader kind={kind} accept={kind === "music" ? "audio/*" : "image/*"} label={`Add ${kind}`} multiple onUploaded={(a) => setItems((x) => [...x, a])} />
        {items.map((a) => (
          <div key={a.id} className="group relative overflow-hidden rounded-lg border border-line bg-panel">
            {kind === "music" ? (
              <div className="flex h-28 flex-col items-center justify-center gap-2 p-2 text-xs text-muted">
                <Music className="size-5" />
                <audio src={a.url} controls className="w-full" />
              </div>
            ) : (
              <img src={a.url} alt={a.name} className="h-28 w-full object-cover" />
            )}
            <div className="truncate px-2 py-1.5 text-[11px] text-muted">{a.name}</div>
            <button
              aria-label={`Delete ${a.name}`}
              onClick={async () => {
                await api.deleteAsset(a.id);
                setItems((x) => x.filter((y) => y.id !== a.id));
              }}
              className="absolute right-1.5 top-1.5 rounded-md bg-black/60 p-1.5 text-muted opacity-0 hover:text-bad group-hover:opacity-100"
            >
              <Trash2 className="size-3.5" />
            </button>
          </div>
        ))}
      </div>
      {items.length === 0 && (
        <Empty icon={<FolderOpen className="size-6" />} title={`No ${kind} assets`}>
          Uploads made while creating batches appear here too.
        </Empty>
      )}
    </div>
  );
}
