import { Loader2, X } from "lucide-react";
import { createContext, useCallback, useContext, useState, type ButtonHTMLAttributes, type ReactNode } from "react";

export function cx(...c: (string | false | null | undefined)[]) {
  return c.filter(Boolean).join(" ");
}

type BtnProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "ghost" | "outline" | "danger";
  size?: "sm" | "md";
  loading?: boolean;
  icon?: ReactNode;
};

export function Button({ variant = "outline", size = "md", loading, icon, className, children, disabled, ...rest }: BtnProps) {
  return (
    <button
      {...rest}
      disabled={disabled || loading}
      className={cx(
        "inline-flex items-center justify-center gap-2 rounded-lg font-medium transition-colors focus-visible:outline-2 focus-visible:outline-accent disabled:cursor-not-allowed disabled:opacity-50",
        size === "sm" ? "h-8 px-3 text-xs" : "h-9 px-4 text-sm",
        variant === "primary" && "bg-accent text-white hover:bg-accent-2",
        variant === "outline" && "border border-line bg-panel-2 text-text hover:border-muted/60",
        variant === "ghost" && "text-muted hover:bg-panel-2 hover:text-text",
        variant === "danger" && "border border-bad/40 bg-bad/10 text-bad hover:bg-bad/20",
        className,
      )}
    >
      {loading ? <Loader2 className="size-4 animate-spin" /> : icon}
      {children}
    </button>
  );
}

export function Card({ className, children }: { className?: string; children: ReactNode }) {
  return <div className={cx("rounded-xl border border-line bg-panel", className)}>{children}</div>;
}

export function Section({ title, desc, action, children }: { title: string; desc?: string; action?: ReactNode; children: ReactNode }) {
  return (
    <section className="space-y-3">
      <div className="flex items-end justify-between gap-4">
        <div>
          <h2 className="text-sm font-semibold">{title}</h2>
          {desc && <p className="mt-0.5 text-xs text-muted">{desc}</p>}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}

const STATUS: Record<string, string> = {
  draft: "bg-muted/15 text-muted",
  pending: "bg-muted/15 text-muted",
  queued: "bg-muted/15 text-muted",
  running: "bg-accent/15 text-accent-2",
  done: "bg-ok/15 text-ok",
  approved: "bg-ok/15 text-ok",
  failed: "bg-bad/15 text-bad",
  rejected: "bg-bad/15 text-bad",
  cancelled: "bg-warn/15 text-warn",
};

export function Pill({ status, children }: { status: string; children?: ReactNode }) {
  return (
    <span className={cx("inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-medium capitalize", STATUS[status] ?? STATUS.draft)}>
      {status === "running" && <span className="size-1.5 rounded-full bg-current animate-pulse-dot" />}
      {children ?? status}
    </span>
  );
}

/** A labelled form field. Use `group` when the children are buttons/uploads rather than one input,
 * so the label doesn't become the accessible name of the first button inside it. */
export function Field({ label, hint, group, children }: { label: string; hint?: string; group?: boolean; children: ReactNode }) {
  const body = (
    <>
      <span className="text-xs font-medium text-muted">{label}</span>
      {children}
      {hint && <span className="block text-[11px] text-muted/80">{hint}</span>}
    </>
  );
  return group ? (
    <div role="group" aria-label={label} className="block space-y-1.5">
      {body}
    </div>
  ) : (
    <label className="block space-y-1.5">{body}</label>
  );
}

export const inputCls =
  "w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm text-text placeholder:text-muted/60 focus:border-accent focus:outline-none";

export function Empty({ icon, title, children }: { icon: ReactNode; title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-line px-6 py-16 text-center">
      <div className="mb-3 text-muted">{icon}</div>
      <p className="text-sm font-medium">{title}</p>
      {children && <div className="mt-2 max-w-sm text-xs text-muted">{children}</div>}
    </div>
  );
}

export function Modal({ open, onClose, title, wide, children }: { open: boolean; onClose: () => void; title: string; wide?: boolean; children: ReactNode }) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm" onClick={onClose}>
      <div
        role="dialog"
        aria-label={title}
        className={cx("max-h-[92vh] w-full overflow-auto rounded-2xl border border-line bg-panel shadow-2xl scroll-thin", wide ? "max-w-6xl" : "max-w-lg")}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-line px-5 py-3">
          <h3 className="text-sm font-semibold">{title}</h3>
          <button onClick={onClose} className="text-muted hover:text-text" aria-label="Close">
            <X className="size-4" />
          </button>
        </div>
        <div className="p-5">{children}</div>
      </div>
    </div>
  );
}

type Toast = { id: number; text: string; kind: "ok" | "error" };
const ToastCtx = createContext<(text: string, kind?: "ok" | "error") => void>(() => {});
export const useToast = () => useContext(ToastCtx);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((text: string, kind: "ok" | "error" = "ok") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, text, kind }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 5000);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="fixed bottom-4 right-4 z-[60] space-y-2">
        {toasts.map((t) => (
          <div
            key={t.id}
            role="status"
            className={cx(
              "max-w-sm rounded-lg border px-4 py-2.5 text-sm shadow-xl",
              t.kind === "ok" ? "border-ok/30 bg-panel-2 text-text" : "border-bad/40 bg-panel-2 text-bad",
            )}
          >
            {t.text}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

export function Stat({ label, value, sub }: { label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <div className="rounded-xl border border-line bg-panel px-4 py-3">
      <div className="text-[11px] uppercase tracking-wide text-muted">{label}</div>
      <div className="mt-1 text-xl font-semibold tabular-nums">{value}</div>
      {sub && <div className="mt-0.5 text-xs text-muted">{sub}</div>}
    </div>
  );
}
