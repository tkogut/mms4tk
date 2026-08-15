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
  const [tradesHistory, setTradesHistory] = useState([]);
  const [kanbanCards, setKanbanCards] = useState([]);
  const [openPositions, setOpenPositions] = useState([]);
  const [isConnected, setIsConnected] = useState(true);
  const [appVersion, setAppVersion] = useState('0.24.3');
  const [apiKey, setApiKey] = useState(localStorage.getItem('mms4_api_key') || '');

  const { isInstallable, isInstalled, handleInstall } = usePWA();

  const fetchDashboardData = async () => {
    try {
      // 0. Fetch health for live version (with cache busting query parameter)
      const healthRes = await fetch(`/api/v1/health?t=${Date.now()}`);
      if (healthRes.ok) {
        const hData = await healthRes.json();
        if (hData.version) setAppVersion(hData.version);
      }

      // 1. Fetch daemon state
      const stateRes = await fetch('/api/v1/state');
      if (stateRes.ok) {
        const data = await stateRes.json();
        setStateData(data);
        setIsConnected(true);
      }

      // 2. Fetch stats & balance
      const statsRes = await fetch('/api/v1/stats/pnl');
      if (statsRes.ok) {
        const data = await statsRes.json();
        setStatsData(data);
      }

      // 3. Fetch trade history
      const tradesRes = await fetch('/api/v1/history/trades');
      if (tradesRes.ok) {
        const data = await tradesRes.json();
        setTradesHistory(data);
      }

      // 4. Fetch live open positions
      const posRes = await fetch('/api/v1/positions');
      if (posRes.ok) {
        const data = await posRes.json();
        setOpenPositions(data.positions || []);
      }

      // 5. Fetch kanban
      const kanbanRes = await fetch('/api/v1/hermes/kanban');
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
      if (newSettings.api_key) {
        setApiKey(newSettings.api_key);
        localStorage.setItem('mms4_api_key', newSettings.api_key);
      }

      const currentKey = newSettings.api_key || apiKey || 'test-api-auth-key-unit-test';

      await fetch('/api/v1/settings/update', {
        method: 'POST',
        headers: { 
          'Content-Type': 'application/json',
          'X-MMS4-API-KEY': currentKey
        },
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
      <Header 
        version={appVersion} 
        isConnected={isConnected} 
        stateData={stateData} 
        onRefresh={fetchDashboardData} 
      />

      {/* Navigation Tabs */}
      <Navbar activeTab={activeTab} setActiveTab={setActiveTab} />

      {/* Main Content Area */}
      <main className="mt-4">
        {activeTab === 'monitoring' && (
          <MonitoringTab 
            stateData={stateData} 
            statsData={statsData} 
            kanbanCards={kanbanCards} 
            openPositions={openPositions}
            onRefresh={fetchDashboardData} 
          />
        )}

        {activeTab === 'analytics' && (
          <AnalyticsTab 
            statsData={statsData} 
            tradesHistory={tradesHistory} 
            openPositions={openPositions}
          />
        )}

        {activeTab === 'settings' && (
          <SettingsTab 
            stateData={stateData} 
            statsData={statsData} 
            apiKey={apiKey}
            onUpdateSettings={handleUpdateSettings} 
          />
        )}

        {activeTab === 'symbols' && (
          <SymbolsTab />
        )}
      </main>
    </div>
  );
}
