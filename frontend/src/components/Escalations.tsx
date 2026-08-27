import React, { useEffect, useState, useCallback } from "react";
import { Link } from "react-router-dom";
import { api } from "../services/api";
import { RiskAssessmentResponse, EventResponse } from "../types";
import { RiskBadge, LoadingSpinner, ErrorState, EmptyState } from "./shared";

interface EscalationEntry {
  payment_id: string;
  risk: RiskAssessmentResponse | null;
  reason: string;
  escalation_type: "critical_risk" | "max_actions" | "max_retries";
  event_count: number;
  recovery_count: number;
}

const ESCALATION_LABELS: Record<string, string> = {
  critical_risk: "Critical Risk",
  max_actions: "Max Actions Reached",
  max_retries: "Max Retries Reached",
};

export default function Escalations() {
  const [entries, setEntries] = useState<EscalationEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [risks, events] = await Promise.all([
        api.listRiskAssessments({ limit: 500 }),
        api.getEvents({ limit: 500 }),
      ]);

      const paymentMap = new Map<string, { risk: RiskAssessmentResponse | null; events: EventResponse[] }>();
      risks.forEach((r: RiskAssessmentResponse) => {
        paymentMap.set(r.payment_id, { risk: r, events: [] });
      });
      events.forEach((e: EventResponse) => {
        if (!e.payment_id) return;
        if (!paymentMap.has(e.payment_id)) {
          paymentMap.set(e.payment_id, { risk: null, events: [] });
        }
        paymentMap.get(e.payment_id)!.events.push(e);
      });

      const escalations: EscalationEntry[] = [];
      paymentMap.forEach((data, pid) => {
        const recoveryEvents = data.events.filter((e) =>
          ["RETRY_PAYMENT", "REMINDER_SENT", "ESCALATED"].includes(e.event_type)
        );
        const retryCount = recoveryEvents.filter((e) => e.event_type === "RETRY_PAYMENT").length;
        const totalActions = recoveryEvents.length;
        const isCritical = data.risk?.risk_label === "critical";

        let escalationType: EscalationEntry["escalation_type"] | null = null;
        let reason = "";

        if (totalActions >= 5) {
          escalationType = "max_actions";
          reason = `Maximum recovery actions (${totalActions}/5) reached`;
        } else if (retryCount >= 3) {
          escalationType = "max_retries";
          reason = `Payment retry limit (${retryCount}/3) reached`;
        } else if (isCritical) {
          escalationType = "critical_risk";
          reason = data.risk?.reasons?.[0] || "Critical risk level detected";
        }

        if (escalationType) {
          escalations.push({
            payment_id: pid,
            risk: data.risk,
            reason,
            escalation_type: escalationType,
            event_count: data.events.length,
            recovery_count: recoveryEvents.length,
          });
        }
      });

      escalations.sort((a, b) => {
        const order = { critical_risk: 0, max_actions: 1, max_retries: 2 };
        return (order[a.escalation_type] || 3) - (order[b.escalation_type] || 3);
      });

      setEntries(escalations);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load escalations");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  if (loading) return <LoadingSpinner text="Loading escalations..." />;
  if (error) return <ErrorState message={error} onRetry={load} />;

  return (
    <div>
      <div className="page-header">
        <div className="page-title">Escalations</div>
        <div className="page-subtitle">
          Payments requiring manual attention \u00B7 {entries.length} total
        </div>
      </div>

      {entries.length === 0 ? (
        <div className="detail-panel">
          <EmptyState
            icon="\u2713"
            title="No escalations"
            description="All payments are within recovery bounds"
          />
        </div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th style={{ width: 4 }}></th>
                <th>Payment ID</th>
                <th>Escalation Reason</th>
                <th>Risk</th>
                <th>Events</th>
                <th>Recovery</th>
                <th style={{ width: 70 }}></th>
              </tr>
            </thead>
            <tbody>
              {entries.map((e) => (
                <tr key={e.payment_id}>
                  <td style={{ padding: 0 }}>
                    <div className={`escalation-indicator ${e.escalation_type}`} />
                  </td>
                  <td>
                    <Link to={`/payments/${e.payment_id}`} className="mono" style={{ color: "var(--accent-light)", textDecoration: "none" }}>
                      {e.payment_id}
                    </Link>
                  </td>
                  <td>
                    <div>
                      <div style={{ fontSize: 12, fontWeight: 600, color: getEscalationColor(e.escalation_type), marginBottom: 2 }}>
                        {ESCALATION_LABELS[e.escalation_type]}
                      </div>
                      <div className="text-sm text-muted">{e.reason}</div>
                    </div>
                  </td>
                  <td>
                    {e.risk ? <RiskBadge label={e.risk.risk_label} size="sm" /> : <span className="text-muted">{"\u2014"}</span>}
                  </td>
                  <td className="text-muted">{e.event_count}</td>
                  <td>
                    <span className="text-muted">{e.recovery_count}</span>
                    <span className="text-dim text-sm"> / 5</span>
                  </td>
                  <td>
                    <Link to={`/payments/${e.payment_id}`} className="btn btn-secondary btn-xs">View</Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function getEscalationColor(type: string): string {
  switch (type) {
    case "critical_risk": return "var(--red)";
    case "max_actions": return "var(--orange)";
    case "max_retries": return "var(--yellow)";
    default: return "var(--text-muted)";
  }
}
