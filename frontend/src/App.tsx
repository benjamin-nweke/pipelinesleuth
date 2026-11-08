import React from 'react';
import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom';
import { Navbar } from './components/common/Navbar';
import { DashboardPage } from './pages/DashboardPage';
import { NewInvestigationPage } from './pages/NewInvestigationPage';
import { WorkspacePage } from './pages/WorkspacePage';
import { EvidencePage } from './pages/EvidencePage';
import { PatchPage } from './pages/PatchPage';
import { ValidationPage } from './pages/ValidationPage';
import { ReportPage } from './pages/ReportPage';
import { SettingsPage } from './pages/SettingsPage';

export const App: React.FC = () => {
  return (
    <Router>
      <div className="min-h-screen bg-charcoal-950 text-charcoal-100 flex flex-col font-sans">
        <Navbar />
        <main className="flex-1">
          <Routes>
            <Route path="/" element={<DashboardPage />} />
            <Route path="/new" element={<NewInvestigationPage />} />
            <Route path="/investigations/:id" element={<WorkspacePage />} />
            <Route path="/investigations/:id/evidence" element={<EvidencePage />} />
            <Route path="/investigations/:id/patches" element={<PatchPage />} />
            <Route path="/investigations/:id/validation" element={<ValidationPage />} />
            <Route path="/investigations/:id/report" element={<ReportPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </main>
      </div>
    </Router>
  );
};

export default App;
