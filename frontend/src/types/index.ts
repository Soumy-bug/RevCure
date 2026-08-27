export interface EventResponse {
  id: number;
  payment_id: string | null;
  event_type: string;
  event_payload: Record<string, any> | null;
  created_at: string;
}

export interface RiskAssessmentResponse {
  payment_id: string;
  risk_score: number;
  risk_label: "none" | "low" | "medium" | "high" | "critical";
  reasons: string[];
  assessed_at: string;
}

export interface RecoveryAttemptResponse {
  id: number;
  payment_id: string;
  action: string;
  status: string;
  reason: string | null;
  attempt_number: number;
  created_at: string;
  amount: number | null;
  razorpay_order_id: string | null;
  outcome: string;
  recovered_at: string | null;
}

export interface RecoveryMetrics {
  money_at_risk: number;
  money_recovered: number;
  recovery_rate: number;
  eligible_payments: number;
  recovered_payments: number;
}

export interface RecoveryResult {
  payment_id: string;
  action: string;
  status: string;
  reason: string | null;
  attempt_number: number;
  created_at: string;
}

export type RiskLabel = "none" | "low" | "medium" | "high" | "critical";

export interface PaymentSummary {
  payment_id: string;
  risk_assessment: RiskAssessmentResponse | null;
  events: EventResponse[];
  recovery_attempts: RecoveryAttemptResponse[];
  latest_event: EventResponse | null;
  retry_count: number;
  total_actions: number;
}

export interface DashboardStats {
  total_events: number;
  total_risk_assessments: number;
  high_risk_count: number;
  critical_risk_count: number;
  payments_at_risk: number;
  recovery_actions_taken: number;
  recovery_succeeded: number;
  recovery_failed: number;
  recovery_skipped: number;
}

export interface ApiError {
  detail: string;
}
