import { Boxes, Clapperboard, FolderOpen, Palette, Plus, Settings2, Workflow } from "lucide-react";
import { useEffect, useState } from "react";
import { NavLink, Route, Routes, useNavigate } from "react-router-dom";
import { api, type Status } from "./api";
import { Button, cx } from "./components/ui";
import BatchDetail from "./pages/BatchDetail";
import Batches from "./pages/Batches";
import Brands from "./pages/Brands";
import Library from "./pages/Library";
import NewBatch from "./pages/NewBatch";
import SettingsPage from "./pages/Settings";
import Workflows from "./pages/Workflows";

const NAV = [
  { to: "/", label: "Batches", icon: Clapperboard },
  { to: "/brands", label: "Brand kits", icon: Palette },
  { to: "/library", label: "Library", icon: FolderOpen },
  { to: "/workflows", label: "Workflows", icon: Workflow },
  { to: "/settings", label: "Settings", icon: Settings2 },
];

export default function App() {
  const [status, setStatus] = useState<Status | null>(null);
  const nav = useNavigate();
  useEffect(() => {
    api.status().then(setStatus).catch(() => setStatus(null));
  }, []);

  return (
    <div className="flex h-full flex-col md:flex-row">
      <aside className="flex shrink-0 flex-col border-b border-line bg-panel md:w-56 md:border-b-0 md:border-r">
        <div className="flex items-center gap-2.5 px-4 py-4">
          <div className="grid size-8 place-items-center rounded-lg bg-accent">
            <Boxes className="size-4 text-white" />
          </div>
          <div>
            <div className="text-sm font-semibold leading-tight">run-comfy</div>
            <div className="text-[11px] text-muted">ad studio</div>
          </div>
        </div>
        <div className="px-3 pb-3">
          <Button variant="primary" className="w-full" icon={<Plus className="size-4" />} onClick={() => nav("/new")}>
            New batch
          </Button>
        </div>
        <nav className="flex gap-1 overflow-x-auto px-3 pb-3 md:flex-col md:overflow-visible">
          {NAV.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              end={to === "/"}
              className={({ isActive }) =>
                cx(
                  "flex shrink-0 items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition-colors",
                  isActive ? "bg-panel-2 text-text" : "text-muted hover:bg-panel-2/60 hover:text-text",
                )
              }
            >
              <Icon className="size-4" />
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="mt-auto hidden border-t border-line px-4 py-3 text-[11px] text-muted md:block">
          {status ? (
            <>
              <div>
                Backend <span className="text-text">{status.backend}</span>
              </div>
              <div>
                Writer <span className="text-text">{status.keys.anthropic ? "Claude" : "demo"}</span> · Voice{" "}
                <span className="text-text">{status.voice_provider}</span>
              </div>
            </>
          ) : (
            "API offline"
          )}
        </div>
      </aside>
      <main className="min-w-0 flex-1 overflow-auto scroll-thin">
        <div className="mx-auto max-w-7xl px-4 py-6 md:px-8">
          <Routes>
            <Route path="/" element={<Batches />} />
            <Route path="/new" element={<NewBatch />} />
            <Route path="/batches/:id" element={<BatchDetail />} />
            <Route path="/brands" element={<Brands />} />
            <Route path="/library" element={<Library />} />
            <Route path="/workflows" element={<Workflows />} />
            <Route path="/settings" element={<SettingsPage status={status} />} />
          </Routes>
        </div>
      </main>
    </div>
  );
}
