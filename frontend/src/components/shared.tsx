import React, { useState, useCallback } from "react";

/* ── Risk Badge ─────────────────────────────────── */
export function RiskBadge({ label, size }: { label: string; size?: "sm" | "md" }) {
  return (
    <span className={`risk-badge ${label} ${size === "sm" ? "risk-badge-sm" : ""}`}>
      <span className={`risk-dot ${label}`} />
      {label}
    </span>
  );
}

/* ── Status Badge ───────────────────────────────── */
export function StatusBadge({ status }: { status: string }) {
  return <span className={`status-badge ${status}`}>{status}</span>;
}

/* ── Action Badge ───────────────────────────────── */
const ACTION_COLORS: Record<string, string> = {
  retry_payment: "var(--blue)",
  send_reminder: "var(--yellow)",
  escalate: "var(--red)",
  no_action: "var(--text-dim)",
};
const ACTION_LABELS: Record<string, string> = {
  retry_payment: "Retry Payment",
  send_reminder: "Send Reminder",
  escalate: "Escalate",
  no_action: "No Action",
};

export function ActionBadge({ action }: { action: string }) {
  return (
    <span
      className="action-badge"
      style={{ borderColor: ACTION_COLORS[action] || "var(--border)" }}
    >
      <span className="action-badge-dot" style={{ background: ACTION_COLORS[action] || "var(--text-dim)" }} />
      {ACTION_LABELS[action] || action}
    </span>
  );
}

/* ── Time Formatting ────────────────────────────── */
export function formatTime(iso: string): string {
  try {
    const d = new Date(iso);
    const now = new Date();
    const diffMs = now.getTime() - d.getTime();
    const diffMin = Math.floor(diffMs / 60000);
    const diffHr = Math.floor(diffMs / 3600000);
    if (diffMin < 1) return "just now";
    if (diffMin < 60) return `${diffMin}m ago`;
    if (diffHr < 24) return `${diffHr}h ago`;
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  } catch {
    return iso;
  }
}

export function formatTimeFull(iso: string): string {
  try {
    return new Date(iso).toLocaleString(undefined, {
      year: "numeric", month: "short", day: "numeric",
      hour: "2-digit", minute: "2-digit", second: "2-digit",
    });
  } catch {
    return iso;
  }
}

/* ── Loading Skeleton ───────────────────────────── */
export function LoadingSkeleton({ rows = 3, className }: { rows?: number; className?: string }) {
  return (
    <div className={`skeleton-wrap ${className || ""}`}>
      {Array.from({ length: rows }).map((_, i) => (
        <div className="skeleton-row" key={i}>
          <div className="skeleton-line skeleton-w60" />
          <div className="skeleton-line skeleton-w40" />
        </div>
      ))}
    </div>
  );
}

export function LoadingSpinner({ text }: { text?: string }) {
  return (
    <div className="loading">
      <div className="spinner" />
      {text && <span style={{ marginLeft: 12 }}>{text}</span>}
    </div>
  );
}

/* ── Empty State ────────────────────────────────── */
export function EmptyState({ icon, title, description }: { icon?: string; title: string; description?: string }) {
  return (
    <div className="empty-state">
      {icon && <div className="empty-state-icon">{icon}</div>}
      <div className="empty-state-title">{title}</div>
      {description && <div className="empty-state-desc">{description}</div>}
    </div>
  );
}

/* ── Error State ────────────────────────────────── */
export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="error-state">
      <div className="error-state-icon">!</div>
      <div className="error-state-msg">{message}</div>
      {onRetry && (
        <button className="btn btn-secondary btn-sm" onClick={onRetry} style={{ marginTop: 12 }}>
          Retry
        </button>
      )}
    </div>
  );
}

/* ── Toast Notification ─────────────────────────── */
interface ToastItem {
  id: number;
  type: "success" | "error" | "info";
  message: string;
}

let toastId = 0;

export function useToast() {
  const [toasts, setToasts] = useState<ToastItem[]>([]);

  const addToast = useCallback((type: ToastItem["type"], message: string, duration = 4000) => {
    const id = ++toastId;
    setToasts((prev) => [...prev, { id, type, message }]);
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, duration);
  }, []);

  const ToastContainer = () => (
    <div className="toast-container">
      {toasts.map((t) => (
        <div key={t.id} className={`toast toast-${t.type}`}>
          <span className="toast-icon">
            {t.type === "success" ? "\u2713" : t.type === "error" ? "\u2717" : "i"}
          </span>
          <span className="toast-msg">{t.message}</span>
        </div>
      ))}
    </div>
  );

  return { addToast, ToastContainer };
}

/* ── Collapsible Payload ────────────────────────── */
export function CollapsiblePayload({ data, label }: { data: Record<string, unknown>; label?: string }) {
  const [expanded, setExpanded] = useState(false);
  const keys = Object.keys(data);
  if (keys.length === 0) return null;

  const preview = keys.slice(0, 3).map((k) => `${k}: ${truncate(String(data[k]), 30)}`).join(", ");

  return (
    <div className="collapsible">
      <button className="collapsible-trigger" onClick={() => setExpanded(!expanded)}>
        <span className="collapsible-arrow">{expanded ? "\u25BC" : "\u25B6"}</span>
        {label || "Payload"}
        <span className="collapsible-preview">{!expanded && preview}</span>
      </button>
      {expanded && (
        <pre className="collapsible-content">
          {JSON.stringify(data, null, 2)}
        </pre>
      )}
    </div>
  );
}

/* ── Progress Bar ───────────────────────────────── */
export function ProgressBar({ value, max, color }: { value: number; max: number; color?: string }) {
  const pct = max > 0 ? Math.min((value / max) * 100, 100) : 0;
  const barColor = color || (pct >= 80 ? "var(--red)" : pct >= 50 ? "var(--yellow)" : "var(--green)");
  return (
    <div className="progress-bar">
      <div className="progress-fill" style={{ width: `${pct}%`, background: barColor }} />
    </div>
  );
}

/* ── Helpers ────────────────────────────────────── */
function truncate(str: string, len: number): string {
  return str.length > len ? str.slice(0, len) + "\u2026" : str;
}

export function eventTypeToAction(eventType: string): string {
  switch (eventType) {
    case "RETRY_PAYMENT": return "retry_payment";
    case "REMINDER_SENT": return "send_reminder";
    case "ESCALATED": return "escalate";
    default: return eventType.toLowerCase();
  }
}

export function isRecoveryEvent(type: string): boolean {
  return ["RETRY_PAYMENT", "REMINDER_SENT", "ESCALATED"].includes(type);
}
