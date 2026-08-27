import React from "react";
import AuditTrail from "./AuditTrail";

export default function AuditTrailPage() {
  return (
    <div>
      <div className="page-header">
        <div className="page-title">Event Log</div>
        <div className="page-subtitle">All webhook and recovery events across payments</div>
      </div>
      <div className="detail-panel">
        <AuditTrail limit={200} />
      </div>
    </div>
  );
}
