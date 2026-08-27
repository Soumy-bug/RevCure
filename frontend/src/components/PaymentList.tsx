import React, { useEffect, useState, useCallback, useMemo } from "react";
import { Link } from "react-router-dom";
import { api } from "../services/api";
import { RiskAssessmentResponse } from "../types";
import { RiskBadge, formatTime, LoadingSpinner, ErrorState, EmptyState, ProgressBar } from "./shared";

type SortKey = "risk_score" | "payment_id" | "assessed_at" | "risk_label";
type SortDir = "asc" | "desc";

const LABEL_ORDER: Record<string, number> = { critical: 4, high: 3, medium: 2, low: 1, none: 0 };

export default function PaymentList() {
  const [risks, setRisks] = useState<RiskAssessmentResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [filterLabel, setFilterLabel] = useState<string>("all");
  const [sortKey, setSortKey] = useState<SortKey>("risk_score");
  const [sortDir, setSortDir] = useState<SortDir>("desc");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.listRiskAssessments({ limit: 500 });
      setRisks(data);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load risk data");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const handleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortDir((d) => (d === "desc" ? "asc" : "desc"));
    } else {
      setSortKey(key);
      setSortDir(key === "risk_score" ? "desc" : "asc");
    }
  };

  const filtered = useMemo(() => {
    let result = [...risks];

    if (filterLabel !== "all") {
      result = result.filter((r) => r.risk_label === filterLabel);
    }

    if (search.trim()) {
      const q = search.toLowerCase();
      result = result.filter(
        (r) =>
          r.payment_id.toLowerCase().includes(q) ||
          r.reasons?.some((reason) => reason.toLowerCase().includes(q))
      );
    }

    result.sort((a, b) => {
      let cmp = 0;
      if (sortKey === "risk_score") cmp = a.risk_score - b.risk_score;
      else if (sortKey === "payment_id") cmp = a.payment_id.localeCompare(b.payment_id);
      else if (sortKey === "assessed_at") cmp = new Date(a.assessed_at).getTime() - new Date(b.assessed_at).getTime();
      else if (sortKey === "risk_label") cmp = (LABEL_ORDER[a.risk_label] || 0) - (LABEL_ORDER[b.risk_label] || 0);
      return sortDir === "desc" ? -cmp : cmp;
    });

    return result;
  }, [risks, search, filterLabel, sortKey, sortDir]);

  const labelCounts = useMemo(() => {
    const counts: Record<string, number> = { all: risks.length, critical: 0, high: 0, medium: 0, low: 0, none: 0 };
    risks.forEach((r) => { counts[r.risk_label] = (counts[r.risk_label] || 0) + 1; });
    return counts;
  }, [risks]);

  if (loading) return <LoadingSpinner text="Loading risk assessments..." />;
  if (error) return <ErrorState message={error} onRetry={load} />;

  return (
    <div>
      <div className="page-header">
        <div className="page-title">Revenue at Risk</div>
        <div className="page-subtitle">Payments ranked by risk score \u00B7 {risks.length} total</div>
      </div>

      <div className="kpi-grid mb-16">
        {(["critical", "high", "medium", "low", "none"] as const).map((label) => (
          <button
            key={label}
            className={`kpi-card ${label} ${filterLabel === label ? "active" : ""}`}
            onClick={() => setFilterLabel(filterLabel === label ? "all" : label)}
            style={{ cursor: "pointer", textAlign: "left" }}
          >
            <div className="kpi-label">{label}</div>
            <div className={`kpi-value ${label === "critical" ? "red" : label === "high" ? "orange" : label === "medium" ? "yellow" : label === "low" ? "green" : "blue"}`}>
              {labelCounts[label]}
            </div>
          </button>
        ))}
      </div>

      <div className="table-wrap">
        <div className="table-toolbar">
          <input
            type="text"
            placeholder="Search payment ID or reason..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <select value={filterLabel} onChange={(e) => setFilterLabel(e.target.value)}>
            <option value="all">All Labels</option>
            <option value="critical">Critical</option>
            <option value="high">High</option>
            <option value="medium">Medium</option>
            <option value="low">Low</option>
            <option value="none">None</option>
          </select>
        </div>
        <table>
          <thead>
            <tr>
              <th onClick={() => handleSort("payment_id")}>
                Payment ID {sortKey === "payment_id" && <span className="sort-arrow">{sortDir === "desc" ? "\u2193" : "\u2191"}</span>}
              </th>
              <th onClick={() => handleSort("risk_score")} style={{ width: 100 }}>
                Score {sortKey === "risk_score" && <span className="sort-arrow">{sortDir === "desc" ? "\u2193" : "\u2191"}</span>}
              </th>
              <th onClick={() => handleSort("risk_label")} style={{ width: 110 }}>
                Label {sortKey === "risk_label" && <span className="sort-arrow">{sortDir === "desc" ? "\u2193" : "\u2191"}</span>}
              </th>
              <th>Top Reason</th>
              <th onClick={() => handleSort("assessed_at")} style={{ width: 120 }}>
                Assessed {sortKey === "assessed_at" && <span className="sort-arrow">{sortDir === "desc" ? "\u2193" : "\u2191"}</span>}
              </th>
              <th style={{ width: 70 }}></th>
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 && (
              <tr><td colSpan={6}>
                <EmptyState icon="\u{1F50D}" title="No payments match your filters" description="Try adjusting your search or filter criteria" />
              </td></tr>
            )}
            {filtered.map((r) => (
              <tr key={r.payment_id}>
                <td>
                  <Link to={`/payments/${r.payment_id}`} className="mono" style={{ color: "var(--accent-light)", textDecoration: "none" }}>
                    {r.payment_id}
                  </Link>
                </td>
                <td>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <span style={{ fontWeight: 600, minWidth: 36 }}>{(r.risk_score * 100).toFixed(0)}%</span>
                    <ProgressBar value={r.risk_score} max={1} />
                  </div>
                </td>
                <td><RiskBadge label={r.risk_label} size="sm" /></td>
                <td className="reason-cell" title={r.reasons?.[0] || ""}>
                  {r.reasons?.[0] || "\u2014"}
                </td>
                <td className="text-muted text-sm">{formatTime(r.assessed_at)}</td>
                <td>
                  <Link to={`/payments/${r.payment_id}`} className="btn btn-secondary btn-xs">Detail</Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
