import React, { useEffect, useState, useCallback } from "react";
import { Link } from "react-router-dom";
import { api } from "../services/api";
import { RiskAssessmentResponse, EventResponse, RecoveryMetrics } from "../types";
import { RiskBadge, formatTime, EmptyState, LoadingSpinner, ErrorState } from "./shared";

interface Stats {
  totalEvents: number;
  totalRisk: number;
  atRisk: number;
  highCount: number;
  criticalCount: number;
  recoveryActions: number;
  escalated: number;
}

function formatPaise(amount: number): string {
  if (amount >= 10000000) return `\u20B9${(amount / 10000000).toFixed(1)}Cr`;
  if (amount >= 100000) return `\u20B9${(amount / 100000).toFixed(1)}L`;
  if (amount >= 1000) return `\u20B9${(amount / 1000).toFixed(1)}K`;
  return `\u20B9${amount}`;
}

export default function Dashboard() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [metrics, setMetrics] = useState<RecoveryMetrics | null>(null);
  const [recentRisk, setRecentRisk] = useState<RiskAssessmentResponse[]>([]);
  const [recentEvents, setRecentEvents] = useState<EventResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [events, risks, recoveryMetrics] = await Promise.all([
        api.getEvents({ limit: 500 }),
        api.listRiskAssessments({ limit: 500 }),
        api.getRecoveryMetrics(),
      ]);

      const riskMap = new Map<string, RiskAssessmentResponse>();
      risks.forEach((r: RiskAssessmentResponse) => riskMap.set(r.payment_id, r));

      let recoveryActions = 0;
      let escalated = 0;
      events.forEach((e: EventResponse) => {
        if (["RETRY_PAYMENT", "REMINDER_SENT", "ESCALATED"].includes(e.event_type)) {
          recoveryActions++;
        }
        if (e.event_type === "ESCALATED") escalated++;
      });

      const highCount = risks.filter((r: RiskAssessmentResponse) => r.risk_label === "high").length;
      const criticalCount = risks.filter((r: RiskAssessmentResponse) => r.risk_label === "critical").length;
      const atRisk = risks.filter((r: RiskAssessmentResponse) =>
        r.risk_label === "high" || r.risk_label === "critical"
      ).length;

      setStats({
        totalEvents: events.length,
        totalRisk: risks.length,
        atRisk,
        highCount,
        criticalCount,
        recoveryActions,
        escalated,
      });

      setMetrics(recoveryMetrics);

      setRecentRisk(
        risks
          .filter((r: RiskAssessmentResponse) => r.risk_score > 0)
          .sort((a: RiskAssessmentResponse, b: RiskAssessmentResponse) => b.risk_score - a.risk_score)
          .slice(0, 5)
      );

      setRecentEvents(events.slice(0, 8));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load dashboard");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  if (loading) return <LoadingSpinner text="Loading dashboard..." />;
  if (error) return <ErrorState message={error} onRetry={load} />;
  if (!stats) return null;

  return (
    <div>
      <div className="page-header">
        <div className="page-title">Revenue Recovery Overview</div>
        <div className="page-subtitle">Real-time payment health and recovery status</div>
      </div>

      <div className="kpi-grid">
        <div className="kpi-card accent">
          <div className="kpi-label">Payments Monitored</div>
          <div className="kpi-value accent">{stats.totalRisk}</div>
          <div className="kpi-sub">{stats.totalEvents} total events</div>
        </div>
        <div className="kpi-card red">
          <div className="kpi-label">Revenue at Risk</div>
          <div className="kpi-value red">{stats.atRisk}</div>
          <div className="kpi-sub">
            {stats.criticalCount} critical \u00B7 {stats.highCount} high
          </div>
        </div>
        <div className="kpi-card green">
          <div className="kpi-label">Money Recovered</div>
          <div className="kpi-value green">
            {metrics ? formatPaise(metrics.money_recovered) : "\u2014"}
          </div>
          <div className="kpi-sub">
            {metrics ? `${metrics.recovered_payments} of ${metrics.eligible_payments} eligible` : "loading..."}
          </div>
        </div>
        <div className="kpi-card blue">
          <div className="kpi-label">Recovery Rate</div>
          <div className="kpi-value blue">
            {metrics ? `${(metrics.recovery_rate * 100).toFixed(0)}%` : "\u2014"}
          </div>
          <div className="kpi-sub">
            {metrics ? `${formatPaise(metrics.money_recovered)} of ${formatPaise(metrics.money_at_risk)}` : "loading..."}
          </div>
        </div>
        <div className="kpi-card orange">
          <div className="kpi-label">Escalated</div>
          <div className="kpi-value orange">{stats.escalated}</div>
          <div className="kpi-sub">require manual review</div>
        </div>
      </div>

      <div className="grid-2">
        <div className="section">
          <div className="section-header">
            <div className="section-title">Highest Risk Payments</div>
            <Link to="/risk" className="btn btn-secondary btn-xs">View All</Link>
          </div>
          <div className="table-wrap">
            {recentRisk.length > 0 ? (
              <table>
                <thead>
                  <tr>
                    <th>Payment ID</th>
                    <th>Score</th>
                    <th>Label</th>
                    <th>Top Reason</th>
                  </tr>
                </thead>
                <tbody>
                  {recentRisk.map((r) => (
                    <tr key={r.payment_id}>
                      <td>
                        <Link to={`/payments/${r.payment_id}`} className="mono" style={{ color: "var(--accent-light)", textDecoration: "none" }}>
                          {r.payment_id}
                        </Link>
                      </td>
                      <td style={{ fontWeight: 600 }}>{(r.risk_score * 100).toFixed(0)}%</td>
                      <td><RiskBadge label={r.risk_label} size="sm" /></td>
                      <td className="reason-cell" title={r.reasons?.[0] || ""}>
                        {r.reasons?.[0] || "\u2014"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <EmptyState icon="\u2713" title="No risk detected" description="All payments are healthy" />
            )}
          </div>
        </div>

        <div className="section">
          <div className="section-header">
            <div className="section-title">Recent Activity</div>
            <Link to="/events" className="btn btn-secondary btn-xs">View All</Link>
          </div>
          <div className="table-wrap">
            {recentEvents.length > 0 ? (
              <table>
                <thead>
                  <tr>
                    <th>Type</th>
                    <th>Payment</th>
                    <th>Time</th>
                  </tr>
                </thead>
                <tbody>
                  {recentEvents.map((e) => (
                    <tr key={e.id}>
                      <td>
                        <span className={`status-badge ${e.event_payload?.final_status || ""}`} style={{ textTransform: "none" }}>
                          {e.event_type.replace(/_/g, " ")}
                        </span>
                      </td>
                      <td className="mono" style={{ color: "var(--text-dim)" }}>
                        {e.payment_id || "\u2014"}
                      </td>
                      <td className="text-sm text-muted">{formatTime(e.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <EmptyState icon="\u2022" title="No events yet" description="Events appear as webhooks arrive" />
            )}
          </div>
        </div>
      </div>

      {stats.atRisk > 0 && (
        <div className="section">
          <div className="section-header">
            <div className="section-title">Risk Distribution</div>
          </div>
          <div className="detail-panel" style={{ display: "flex", gap: 24, flexWrap: "wrap" }}>
            {(["critical", "high", "medium", "low", "none"] as const).map((label) => {
              const count = recentRisk.filter((r) => r.risk_label === label).length;
              return (
                <div key={label} style={{ flex: "1 1 100px", textAlign: "center" }}>
                  <RiskBadge label={label} />
                  <div style={{ fontSize: 24, fontWeight: 700, marginTop: 6 }}>{count}</div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
