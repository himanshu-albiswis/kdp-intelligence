import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { DemoOne } from "@/components/demo";
import Dashboard from "@/dashboard/Dashboard";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<DemoOne />} />
        <Route path="/app" element={<Dashboard />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
