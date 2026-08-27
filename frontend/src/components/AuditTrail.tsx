import React, { useEffect, useState, useCallback } from "react";
import { Link } from "react-router-dom";
import { api } from "../services/api";
import { EventResponse } from "../types";
import { formatTime, LoadingSpinner, ErrorState, EmptyState, CollapsiblePayload } from "./shared";

interface Props {
  paymentId?: string;
  limit?: number;
}

const EVENT_ICONS: Record<string, { emoji: string; category: string }> = {
  PAYMENT_FAILED: { emoji: "\u274C", category: "webhook" },
  PAYMENT_SUCCESS: { emoji: "\u2705", category: "webhook" },
  PAYMENT_AUTHORIZED: { emoji: "\u{1F513}", category: "webhook" },
  PAYMENT_CAPTURED: { emoji: "\u2705", category: "webhook" },
  REFUND_PENDING: { emoji: "\u21A9\uFE0F", category: "webhook" },
  REFUND_PROCESSED: { emoji: "\u21A9\uFE0F", category: "webhook" },
  DISPUTE_OPENED: { emoji: "\u26A0\uFE0F", category: "webhook" },
  DISPUTE_CLOSED: { emoji: "\u{1F512}", category: "webhook" },
  SETTLEMENT_PROCESSED: { emoji: "\u{1F3E6}", category: "webhook" },
  SETTLEMENT_RECONCILED: { emoji: "\u{1F3E6}", category: "webhook" },
  RETRY_PAYMENT: { emoji: "\u26A1", category: "recovery" },
  REMINDER_SENT: { emoji: "\u{1F4E7}", category: "recovery" },
  ESCALATED: { emoji: "\u{1F53A}", category: "recovery" },
  RISK_ASSESSED: { emoji: "\u{1F4CA}", category: "risk" },
};

function formatEventType(type: string): string {
  return type
    .replace(/_/g, " ")
    .toLowerCase()
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

export default function AuditTrail({ paymentId, limit = 50 }: Props) {
  const [events, setEvents] = useState<EventResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params: { limit: number; payment_id?: string } = { limit };
      if (paymentId) params.payment_id = paymentId;
      const data = await api.getEvents(params);
      setEvents(data);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load events");
    } finally {
      setLoading(false);
    }
  }, [paymentId, limit]);

  useEffect(() => { load(); }, [load]);

  if (loading) return <LoadingSpinner text="Loading events..." />;
  if (error) return <ErrorState message={error} onRetry={load} />;
  if (events.length === 0) return <EmptyState icon="\u{1F4CB}" title="No events recorded" description="Events will appear as webhooks arrive" />;

  return (
    <div className="timeline">
      {events.map((e) => {
        const info = EVENT_ICONS[e.event_type] || { emoji: "\u{1F4CB}", category: "other" };
        const hasPayload = e.event_payload && Object.keys(e.event_payload).length > 0;
        const isRecovery = ["RETRY_PAYMENT", "REMINDER_SENT", "ESCALATED"].includes(e.event_type);
        const status = e.event_payload?.final_status;

        return (
          <div className="timeline-item" key={e.id}>
            <div className={`timeline-dot ${info.category}`}>{info.emoji}</div>
            <div className="timeline-body">
              <div className="timeline-header">
                <div className="timeline-type">{formatEventType(e.event_type)}</div>
                {isRecovery && status && (
                  <span className={`status-badge ${status}`}>{status}</span>
                )}
                {e.payment_id && (
                  <Link
                    to={`/payments/${e.payment_id}`}
                    className="mono"
                    style={{ color: "var(--accent-light)", textDecoration: "none", fontSize: 11 }}
                  >
                    {e.payment_id}
                  </Link>
                )}
              </div>
              <div className="timeline-meta">
                <span>{formatTime(e.created_at)}</span>
              </div>
              {hasPayload && (
                <CollapsiblePayload data={e.event_payload!} label="Event Data" />
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
