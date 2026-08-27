import React, { useEffect, useState, useCallback } from "react";
import { api } from "../services/api";
import { RecoveryAttemptResponse } from "../types";
import { StatusBadge, ActionBadge, formatTime, formatTimeFull, LoadingSpinner, EmptyState, ProgressBar, useToast } from "./shared";

interface Props {
  paymentId: string;
  onRecoveryDone?: () => void;
}

const ACTION_LABELS: Record<string, string> = {
  retry_payment: "Retry Payment",
  send_reminder: "Send Reminder",
  escalate: "Escalate",
  no_action: "No Action",
};

export default function RecoveryActions({ paymentId, onRecoveryDone }: Props) {
  const [attempts, setAttempts] = useState<RecoveryAttemptResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [executing, setExecuting] = useState(false);
  const [result, setResult] = useState<{ success: boolean; data: RecoveryAttemptResponse | null } | null>(null);
  const { addToast, ToastContainer } = useToast();

  const loadAttempts = useCallback(async () => {
    try {
      const events = await api.getEvents({ payment_id: paymentId, limit: 100 });
      const recoveryEvents = events.filter((e) =>
        ["RETRY_PAYMENT", "REMINDER_SENT", "ESCALATED"].includes(e.event_type)
      );
      const mapped: RecoveryAttemptResponse[] = recoveryEvents.map((e, i) => ({
        id: e.id,
        payment_id: paymentId,
        action: eventTypeToAction(e.event_type),
        status: e.event_payload?.final_status || "executed",
        reason: e.event_payload?.reason || null,
        attempt_number: e.event_payload?.attempt_number || i + 1,
        created_at: e.created_at,
        amount: e.event_payload?.razorpay_order?.amount || null,
        razorpay_order_id: e.event_payload?.razorpay_order?.order_id || null,
        outcome: e.event_payload?.final_status === "failed" ? "failed" : "executed",
        recovered_at: null,
      }));
      setAttempts(mapped);
    } catch {
      // Non-critical
    } finally {
      setLoading(false);
    }
  }, [paymentId]);

  useEffect(() => { loadAttempts(); }, [loadAttempts]);

  const handleRecover = async () => {
    setExecuting(true);
    setResult(null);
    try {
      const data = await api.recoverPayment(paymentId);
      setResult({ success: true, data });
      addToast("success", `Recovery action executed: ${ACTION_LABELS[data.action] || data.action}`);
      await loadAttempts();
      onRecoveryDone?.();
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Recovery failed";
      setResult({ success: false, data: null });
      addToast("error", msg);
    } finally {
      setExecuting(false);
    }
  };

  const retryCount = attempts.filter((a) => a.action === "retry_payment").length;
  const totalActions = attempts.length;
  const maxRetries = 3;
  const maxActions = 5;
  const canRetry = retryCount < maxRetries && totalActions < maxActions;

  return (
    <div className="recovery-panel">
      <ToastContainer />
      <div className="flex items-center justify-between mb-12">
        <div>
          <div className="section-title">Recovery Action</div>
          <div className="section-subtitle">
            {totalActions}/{maxActions} actions used \u00B7 {retryCount}/{maxRetries} retries used
          </div>
        </div>
        <button
          className={`btn ${canRetry ? "btn-primary" : "btn-secondary"}`}
          onClick={handleRecover}
          disabled={executing || !canRetry}
        >
          {executing ? (
            <><div className="spinner" style={{ width: 14, height: 14, borderWidth: 2 }} /> Executing...</>
          ) : canRetry ? (
            "Run Recovery"
          ) : (
            "Limit Reached"
          )}
        </button>
      </div>

      <div className="recovery-info-grid">
        <div className="recovery-info-item">
          <div className="recovery-info-label">Retry Count</div>
          <div className="recovery-info-value" style={{ color: retryCount >= maxRetries ? "var(--red)" : "var(--text)" }}>
            {retryCount} <span className="recovery-info-max">/ {maxRetries}</span>
          </div>
        </div>
        <div className="recovery-info-item">
          <div className="recovery-info-label">Total Actions</div>
          <div className="recovery-info-value" style={{ color: totalActions >= maxActions ? "var(--red)" : "var(--text)" }}>
            {totalActions} <span className="recovery-info-max">/ {maxActions}</span>
          </div>
        </div>
        <div className="recovery-info-item">
          <div className="recovery-info-label">Next Action</div>
          <div className="recovery-info-value" style={{ fontSize: 14 }}>
            {!canRetry && totalActions >= maxActions ? (
              <span style={{ color: "var(--red)" }}>Escalate</span>
            ) : !canRetry && retryCount >= maxRetries ? (
              <span style={{ color: "var(--yellow)" }}>Escalate</span>
            ) : (
              <span style={{ color: "var(--green)" }}>{totalActions < maxActions ? "Available" : "None"}</span>
            )}
          </div>
        </div>
      </div>

      <div style={{ marginTop: 12 }}>
        <div className="flex justify-between mb-4">
          <span className="detail-label">Retry Budget</span>
          <span className="text-sm text-muted">{retryCount}/{maxRetries}</span>
        </div>
        <ProgressBar value={retryCount} max={maxRetries} color={retryCount >= maxRetries ? "var(--red)" : "var(--blue)"} />
      </div>
      <div style={{ marginTop: 10 }}>
        <div className="flex justify-between mb-4">
          <span className="detail-label">Action Budget</span>
          <span className="text-sm text-muted">{totalActions}/{maxActions}</span>
        </div>
        <ProgressBar value={totalActions} max={maxActions} color={totalActions >= maxActions ? "var(--red)" : totalActions >= 3 ? "var(--yellow)" : "var(--green)"} />
      </div>

      {!canRetry && totalActions >= maxActions && (
        <div className="recovery-result error" style={{ marginTop: 14 }}>
          <div className="recovery-result-label">Maximum Actions Reached</div>
          <div className="text-sm">All {maxActions} recovery actions have been used. This payment requires manual escalation.</div>
        </div>
      )}

      {!canRetry && retryCount >= maxRetries && totalActions < maxActions && (
        <div className="recovery-result" style={{ marginTop: 14, background: "var(--yellow-bg)", borderColor: "rgba(253, 203, 110, 0.25)", color: "var(--yellow)" }}>
          <div className="recovery-result-label" style={{ color: "var(--yellow)" }}>Retry Limit Reached</div>
          <div className="text-sm" style={{ color: "var(--text-muted)" }}>
            {maxRetries} payment retries exhausted. The next action will be automatic escalation.
          </div>
        </div>
      )}

      {result?.success && result.data && (
        <div className="recovery-result success" style={{ marginTop: 14 }}>
          <div className="recovery-result-label">Recovery Executed Successfully</div>
          <div className="recovery-result-row"><span className="recovery-result-key">Action:</span> <ActionBadge action={result.data.action} /></div>
          <div className="recovery-result-row"><span className="recovery-result-key">Status:</span> <StatusBadge status={result.data.status} /></div>
          <div className="recovery-result-row"><span className="recovery-result-key">Reason:</span> {result.data.reason || "\u2014"}</div>
          <div className="recovery-result-row"><span className="recovery-result-key">Attempt:</span> #{result.data.attempt_number}</div>
          <div className="recovery-result-row"><span className="recovery-result-key">Time:</span> {formatTimeFull(result.data.created_at)}</div>
        </div>
      )}

      <div className="mt-16">
        <div className="section-title text-sm mb-8" style={{ color: "var(--text-muted)" }}>
          Recovery History ({attempts.length})
        </div>
        {loading ? (
          <LoadingSpinner />
        ) : attempts.length === 0 ? (
          <EmptyState icon="\u26A1" title="No recovery actions taken" description="Actions will appear here after running recovery" />
        ) : (
          <div className="timeline">
            {attempts.map((a) => (
              <div className="timeline-item" key={a.id}>
                <div className="timeline-dot recovery">{"\u26A1"}</div>
                <div className="timeline-body">
                  <div className="timeline-header">
                    <div className="timeline-type">{ACTION_LABELS[a.action] || a.action}</div>
                    <StatusBadge status={a.status} />
                  </div>
                  <div className="timeline-meta">
                    <span>Attempt #{a.attempt_number}</span>
                    <span style={{ color: "var(--text-dim)" }}>{"\u00B7"}</span>
                    <span>{formatTime(a.created_at)}</span>
                  </div>
                  {a.reason && (
                    <div className="collapsible" style={{ marginTop: 6 }}>
                      <div style={{
                        fontSize: 12, color: "var(--text-dim)", fontFamily: "var(--font-mono)",
                        padding: "6px 10px", background: "var(--bg-inset)", borderRadius: 4,
                        border: "1px solid var(--border)", lineHeight: 1.5,
                      }}>
                        {a.reason}
                      </div>
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function eventTypeToAction(eventType: string): string {
  switch (eventType) {
    case "RETRY_PAYMENT": return "retry_payment";
    case "REMINDER_SENT": return "send_reminder";
    case "ESCALATED": return "escalate";
    default: return eventType.toLowerCase();
  }
}
