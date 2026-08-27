const API_BASE = process.env.REACT_APP_API_URL || "/api/v1";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const url = `${API_BASE}${path}`;
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json", ...options?.headers },
    ...options,
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch {}
    throw new Error(detail);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

export const api = {
  getHealth: () => request<{ status: string }>("/health"),

  getEvents: (params?: { limit?: number; offset?: number; payment_id?: string }) => {
    const q = new URLSearchParams();
    if (params?.limit) q.set("limit", String(params.limit));
    if (params?.offset) q.set("offset", String(params.offset));
    if (params?.payment_id) q.set("payment_id", params.payment_id);
    const qs = q.toString();
    return request<any[]>(`/events${qs ? `?${qs}` : ""}`);
  },

  getRiskAssessment: (paymentId: string) =>
    request<any>(`/payments/${encodeURIComponent(paymentId)}/risk`),

  assessRisk: (paymentId: string) =>
    request<any>(`/payments/${encodeURIComponent(paymentId)}/risk/assess`, { method: "POST" }),

  listRiskAssessments: (params?: { min_score?: number; limit?: number; offset?: number }) => {
    const q = new URLSearchParams();
    if (params?.min_score != null) q.set("min_score", String(params.min_score));
    if (params?.limit) q.set("limit", String(params.limit));
    if (params?.offset) q.set("offset", String(params.offset));
    const qs = q.toString();
    return request<any[]>(`/risk/payments${qs ? `?${qs}` : ""}`);
  },

  recoverPayment: (paymentId: string) =>
    request<any>(`/payments/${encodeURIComponent(paymentId)}/recover`, { method: "POST" }),

  getRecoveryMetrics: () => request<any>("/recovery/metrics"),
};
