import React, { useState, useEffect } from 'react';
import { usePWA } from './hooks/usePWA';
import { PWABanner } from './components/PWABanner';
import { Header } from './components/Header';
import { Navbar } from './components/Navbar';
import { MonitoringTab } from './components/MonitoringTab';
import { AnalyticsTab } from './components/AnalyticsTab';
import { SettingsTab } from './components/SettingsTab';
import { SymbolsTab } from './components/SymbolsTab';

export default function App() {
  const [activeTab, setActiveTab] = useState('monitoring');
  const [stateData, setStateData] = useState(null);
  const [statsData, setStatsData] = useState(null);
  const [analyticsData, setAnalyticsData] = useState(null);
  const [kanbanCards, setKanbanCards] = useState([]);
  const [isConnected, setIsConnected] = useState(true);

  const { isInstallable, isInstalled, handleInstall } = usePWA();

  const fetchDashboardData = async () => {
    try {
      // 1. Fetch daemon state
      const stateRes = await fetch('/api/daemon/status');
      if (stateRes.ok) {
        const data = await stateRes.json();
        setStateData(data);
        setIsConnected(true);
      }

      // 2. Fetch stats
      const statsRes = await fetch('/api/stats/pnl');
      if (statsRes.ok) {
        const data = await statsRes.json();
        setStatsData(data);
      }

      // 3. Fetch analytics
      const analyticsRes = await fetch('/api/analytics/signals');
      if (analyticsRes.ok) {
        const data = await analyticsRes.json();
        setAnalyticsData(data);
      }

      // 4. Fetch kanban
      const kanbanRes = await fetch('/api/hermes/kanban');
      if (kanbanRes.ok) {
        const data = await kanbanRes.json();
        setKanbanCards(data.cards || []);
      }
    } catch (err) {
      console.warn('[App] Connection warning:', err);
      setIsConnected(false);
    }
  };

  useEffect(() => {
    fetchDashboardData();
    const interval = setInterval(fetchDashboardData, 5000);
    return () => clearInterval(interval);
  }, []);

  const handleUpdateSettings = async (newSettings) => {
    try {
      await fetch('/api/settings/update', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(newSettings)
      });
      fetchDashboardData();
    } catch (err) {
      console.error('[App] Failed to update settings:', err);
    }
  };

  return (
    <div className="min-h-screen bg-[#090d16] text-gray-100 p-4 sm:p-6 md:p-8 max-w-7xl mx-auto">
      {/* Native PWA Banner */}
      {!isInstalled && (
        <PWABanner isInstallable={isInstallable} handleInstall={handleInstall} />
      )}

      {/* Main Header */}
      <Header version="0.7.0" isConnected={isConnected} />

      {/* Navigation Tabs */}
      <Navbar activeTab={activeTab} setActiveTab={setActiveTab} />

      {/* Main Content Area */}
      <main className="mt-4">
        {activeTab === 'monitoring' && (
          <MonitoringTab 
            stateData={stateData} 
            statsData={statsData} 
            kanbanCards={kanbanCards} 
            onRefresh={fetchDashboardData} 
          />
        )}

        {activeTab === 'analytics' && (
          <AnalyticsTab analyticsData={analyticsData} />
        )}

        {activeTab === 'settings' && (
          <SettingsTab stateData={stateData} onUpdateSettings={handleUpdateSettings} />
        )}

        {activeTab === 'symbols' && (
          <SymbolsTab />
        )}
      </main>
    </div>
  );
}
