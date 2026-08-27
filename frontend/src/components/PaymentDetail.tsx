import React, { useEffect, useState, useCallback } from "react";
import { useParams, Link } from "react-router-dom";
import { api } from "../services/api";
import { RiskAssessmentResponse, EventResponse } from "../types";
import RecoveryActions from "./RecoveryActions";
import AuditTrail from "./AuditTrail";
import { RiskBadge, formatTimeFull, LoadingSpinner, ErrorState, ProgressBar } from "./shared";

export default function PaymentDetail() {
  const { paymentId } = useParams<{ paymentId: string }>();
  const [risk, setRisk] = useState<RiskAssessmentResponse | null>(null);
  const [events, setEvents] = useState<EventResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [assessing, setAssessing] = useState(false);
  const [assessToast, setAssessToast] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!paymentId) return;
    setLoading(true);
    setError(null);
    try {
      const [riskData, eventsData] = await Promise.all([
        api.getRiskAssessment(paymentId).catch(() => null),
        api.getEvents({ payment_id: paymentId, limit: 200 }),
      ]);
      setRisk(riskData);
      setEvents(eventsData);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load payment data");
    } finally {
      setLoading(false);
    }
  }, [paymentId]);

  useEffect(() => { load(); }, [load]);

  const handleAssess = async () => {
    if (!paymentId) return;
    setAssessing(true);
    setAssessToast(null);
    try {
      const data = await api.assessRisk(paymentId);
      setRisk(data);
      setAssessToast("Risk assessment updated");
      setTimeout(() => setAssessToast(null), 3000);
    } catch (e: unknown) {
      setAssessToast(e instanceof Error ? e.message : "Assessment failed");
    } finally {
      setAssessing(false);
    }
  };

  if (loading) return <LoadingSpinner text="Loading payment details..." />;
  if (error) return <ErrorState message={error} onRetry={load} />;
  if (!paymentId) return <ErrorState message="No payment ID provided" />;

  const recoveryEvents = events.filter((e) => isRecoveryEvent(e.event_type));
  const retryCount = recoveryEvents.filter((e) => e.event_type === "RETRY_PAYMENT").length;
  const totalActions = recoveryEvents.length;
  const isEscalated = totalActions >= 5 || retryCount >= 3;

  return (
    <div>
      <Link to="/risk" className="back-link">{"\u2190"} Back to Risk Table</Link>

      {assessToast && (
        <div className={`toast ${assessToast.includes("failed") || assessToast.includes("Error") ? "toast-error" : "toast-success"}`}
          style={{ position: "relative", marginBottom: 12, animation: "none" }}>
          <span className="toast-msg">{assessToast}</span>
        </div>
      )}

      <div className="section-header mb-16">
        <div>
          <div className="page-title">
            Payment <span className="mono" style={{ color: "var(--accent-light)" }}>{paymentId}</span>
          </div>
          <div className="page-subtitle" style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 4 }}>
            {risk ? (
              <>
                <span>Risk:</span>
                <RiskBadge label={risk.risk_label} />
                <span style={{ color: "var(--text-dim)" }}>\u00B7</span>
                <span>Score: {(risk.risk_score * 100).toFixed(0)}%</span>
              </>
            ) : (
              "No risk assessment found"
            )}
          </div>
        </div>
        <button className="btn btn-secondary" onClick={handleAssess} disabled={assessing}>
          {assessing ? (
            <><div className="spinner" style={{ width: 14, height: 14, borderWidth: 2 }} /> Assessing...</>
          ) : (
            "Re-assess Risk"
          )}
        </button>
      </div>

      <div className="grid-2 mb-16">
        <div className="detail-panel">
          <div className="section-title text-sm mb-12">Risk Assessment</div>
          {risk ? (
            <>
              <div style={{ display: "flex", alignItems: "center", gap: 20, marginBottom: 16 }}>
                <div>
                  <div className="detail-label">Score</div>
                  <div style={{ fontSize: 32, fontWeight: 700, letterSpacing: "-0.5px", marginTop: 2 }}>
                    {(risk.risk_score * 100).toFixed(0)}%
                  </div>
                </div>
                <div style={{ flex: 1 }}>
                  <div className="detail-label">Level</div>
                  <div style={{ marginTop: 4 }}><RiskBadge label={risk.risk_label} /></div>
                </div>
                <div style={{ flex: 1 }}>
                  <div className="detail-label">Assessed</div>
                  <div className="text-sm text-muted" style={{ marginTop: 4 }}>{formatTimeFull(risk.assessed_at)}</div>
                </div>
              </div>
              <ProgressBar value={risk.risk_score} max={1} />
              {risk.reasons && risk.reasons.length > 0 && (
                <div className="mt-16">
                  <div className="detail-label mb-8">Risk Reasons ({risk.reasons.length})</div>
                  <div className="reasons-list">
                    {risk.reasons.map((r, i) => (
                      <div className="reason-item" key={i}>
                        <span className="reason-bullet">{"\u25CF"}</span>
                        <span>{r}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </>
          ) : (
            <div className="text-muted text-sm" style={{ padding: "20px 0" }}>
              No risk assessment. Click "Re-assess Risk" to run detection.
            </div>
          )}
        </div>

        <div className="detail-panel">
          <div className="section-title text-sm mb-12">Recovery State</div>
          <div className="recovery-info-grid" style={{ marginBottom: 0 }}>
            <div className="recovery-info-item">
              <div className="recovery-info-label">Retries</div>
              <div className="recovery-info-value" style={{ color: retryCount >= 3 ? "var(--red)" : "var(--text)" }}>
                {retryCount} <span className="recovery-info-max">/ 3</span>
              </div>
            </div>
            <div className="recovery-info-item">
              <div className="recovery-info-label">Total Actions</div>
              <div className="recovery-info-value" style={{ color: totalActions >= 5 ? "var(--red)" : "var(--text)" }}>
                {totalActions} <span className="recovery-info-max">/ 5</span>
              </div>
            </div>
            <div className="recovery-info-item">
              <div className="recovery-info-label">Status</div>
              <div className="recovery-info-value">
                {isEscalated ? (
                  <span style={{ color: "var(--red)", fontWeight: 600 }}>Escalated</span>
                ) : totalActions > 0 ? (
                  <span style={{ color: "var(--green)" }}>Active</span>
                ) : (
                  <span className="text-muted">None</span>
                )}
              </div>
            </div>
          </div>
          <div style={{ marginTop: 16 }}>
            <div className="detail-label mb-4">Retry Budget</div>
            <ProgressBar value={retryCount} max={3} color={retryCount >= 3 ? "var(--red)" : "var(--blue)"} />
            <div style={{ marginTop: 12 }}>
              <div className="detail-label mb-4">Action Budget</div>
              <ProgressBar value={totalActions} max={5} color={totalActions >= 5 ? "var(--red)" : totalActions >= 3 ? "var(--yellow)" : "var(--green)"} />
            </div>
          </div>
        </div>
      </div>

      <div className="section mb-16">
        <RecoveryActions paymentId={paymentId} onRecoveryDone={load} />
      </div>

      <div className="section">
        <div className="section-title mb-8">Event Timeline ({events.length})</div>
        <div className="detail-panel">
          <AuditTrail paymentId={paymentId} limit={200} />
        </div>
      </div>
    </div>
  );
}

function isRecoveryEvent(type: string): boolean {
  return ["RETRY_PAYMENT", "REMINDER_SENT", "ESCALATED"].includes(type);
}
