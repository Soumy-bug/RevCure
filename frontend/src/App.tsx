import React from "react";
import { BrowserRouter, Routes, Route, NavLink } from "react-router-dom";
import Dashboard from "./components/Dashboard";
import PaymentList from "./components/PaymentList";
import PaymentDetail from "./components/PaymentDetail";
import Escalations from "./components/Escalations";
import AuditTrailPage from "./components/AuditTrailPage";

export default function App() {
  return (
    <BrowserRouter>
      <div className="app">
        <header className="topbar">
          <div className="topbar-brand">
            <div className="topbar-brand-icon">R</div>
            RevCure <span>Revenue Recovery</span>
          </div>
          <nav className="topbar-nav">
            <NavLink to="/" end className={({ isActive }) => isActive ? "active" : ""}>
              Overview
            </NavLink>
            <NavLink to="/risk" className={({ isActive }) => isActive ? "active" : ""}>
              Risk Table
            </NavLink>
            <NavLink to="/escalations" className={({ isActive }) => isActive ? "active" : ""}>
              Escalations
            </NavLink>
            <NavLink to="/events" className={({ isActive }) => isActive ? "active" : ""}>
              Events
            </NavLink>
          </nav>
        </header>
        <main className="main">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/risk" element={<PaymentList />} />
            <Route path="/payments/:paymentId" element={<PaymentDetail />} />
            <Route path="/escalations" element={<Escalations />} />
            <Route path="/events" element={<AuditTrailPage />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
}
