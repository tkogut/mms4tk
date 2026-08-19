import React, { useState } from 'react';
import { Sliders, Lock, Key, Save, Shield, PieChart, CheckCircle2 } from 'lucide-react';

export function SettingsTab({ stateData, statsData, apiKey, onUpdateSettings }) {
  const [riskProfile, setRiskProfile] = useState('MODERATE');
  const [leverage, setLeverage] = useState(stateData?.leverage || 3.0);
  const [stopLoss, setStopLoss] = useState(stateData?.stop_loss_pct || 1.25);
  const [isScoutMode, setIsScoutMode] = useState(stateData?.state === 'SCOUT_MODE' || stateData?.scout_mode || false);
  const [currentApiKey, setCurrentApiKey] = useState(apiKey || '');
  
  // Password change state
  const [newPassword, setNewPassword] = useState('');
  const [passwordMsg, setPasswordMsg] = useState('');

  const [savedSuccess, setSavedSuccess] = useState(false);

  // Leverage brackets per risk_profile
  const getLeverageRange = () => {
    switch (riskProfile) {
      case 'CONSERVATIVE': return { min: 1.0, max: 2.0, default: 1.0 };
      case 'AGGRESSIVE': return { min: 5.0, max: 10.0, default: 10.0 };
      case 'MODERATE':
      default: return { min: 2.0, max: 5.0, default: 3.0 };
    }
  };

  const range = getLeverageRange();

  const handleRiskChange = (profile) => {
    setRiskProfile(profile);
    if (profile === 'CONSERVATIVE') setLeverage(1.0);
    else if (profile === 'MODERATE') setLeverage(3.0);
    else if (profile === 'AGGRESSIVE') setLeverage(10.0);
  };

  const handleSubmit = (e) => {
    e.preventDefault();
    if (onUpdateSettings) {
      onUpdateSettings({
        risk_profile: riskProfile,
        leverage: parseFloat(leverage),
        stop_loss_pct: parseFloat(stopLoss),
        scout_mode: isScoutMode,
        api_key: currentApiKey
      });
    }
    setSavedSuccess(true);
    setTimeout(() => setSavedSuccess(false), 3000);
  };

  const handlePasswordChange = async (e) => {
    e.preventDefault();
    if (!newPassword || newPassword.length < 4) {
      setPasswordMsg('Hasło musi mieć co najmniej 4 znaki');
      return;
    }
    try {
      const currentKey = currentApiKey || apiKey || 'test-api-auth-key-unit-test';
      const res = await fetch('/api/v1/settings/change_password', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-MMS4-API-KEY': currentKey
        },
        body: JSON.stringify({ new_password: newPassword, username: 'admin' })
      });
      if (res.ok) {
        setPasswordMsg('✓ Hasło panelu zaktualizowane pomyślnie!');
        setNewPassword('');
      } else {
        setPasswordMsg('❌ Błąd zmiany hasła (sprawdź API Key)');
      }
    } catch (err) {
      setPasswordMsg('❌ Błąd połączenia z serwerem');
    }
  };

  return (
    <div className="max-w-3xl space-y-6">
      {/* Strategy Parameters Form */}
      <form onSubmit={handleSubmit} className="bg-slate-900 border border-slate-800 rounded-xl p-6 shadow-lg space-y-6">
        <div className="flex items-center gap-3 pb-4 border-b border-slate-800">
          <div className="p-2.5 bg-blue-500/10 text-blue-400 rounded-lg">
            <Sliders className="w-6 h-6" />
          </div>
          <div>
            <h3 className="text-lg font-bold text-gray-100">Profil Ryzyka & Dźwignia Strategii</h3>
            <p className="text-xs text-slate-400">Powiązanie poziomu ryzyka (CONSERVATIVE, MODERATE, AGGRESSIVE) z zakresem dźwigni (1x, 3x, 10x)</p>
          </div>
        </div>

        {/* Risk Profile Selection (1x, 3x, 10x) */}
        <div>
          <label className="block text-xs font-semibold text-slate-300 mb-3">
            Wybież Profil Ryzyka (Risk Profile):
          </label>
          <div className="grid grid-cols-3 gap-3">
            <button
              type="button"
              onClick={() => handleRiskChange('CONSERVATIVE')}
              className={`p-3.5 rounded-xl border text-left transition-all ${
                riskProfile === 'CONSERVATIVE'
                  ? 'bg-emerald-950/60 border-emerald-500/60 text-emerald-300 shadow-md shadow-emerald-500/10 font-bold'
                  : 'bg-slate-950/60 border-slate-800 text-slate-400 hover:border-slate-700'
              }`}
            >
              <div className="text-xs text-emerald-400 font-mono font-bold mb-1">CONSERVATIVE</div>
              <div className="text-base font-extrabold text-gray-100">1.0x – 2.0x</div>
              <div className="text-[10px] text-slate-400 mt-1">Bezpieczny SPOT</div>
            </button>

            <button
              type="button"
              onClick={() => handleRiskChange('MODERATE')}
              className={`p-3.5 rounded-xl border text-left transition-all ${
                riskProfile === 'MODERATE'
                  ? 'bg-blue-950/60 border-blue-500/60 text-blue-300 shadow-md shadow-blue-500/10 font-bold'
                  : 'bg-slate-950/60 border-slate-800 text-slate-400 hover:border-slate-700'
              }`}
            >
              <div className="text-xs text-blue-400 font-mono font-bold mb-1">MODERATE</div>
              <div className="text-base font-extrabold text-gray-100">2.0x – 5.0x</div>
              <div className="text-[10px] text-slate-400 mt-1">Standard (3.0x)</div>
            </button>

            <button
              type="button"
              onClick={() => handleRiskChange('AGGRESSIVE')}
              className={`p-3.5 rounded-xl border text-left transition-all ${
                riskProfile === 'AGGRESSIVE'
                  ? 'bg-amber-950/60 border-amber-500/60 text-amber-300 shadow-md shadow-amber-500/10 font-bold'
                  : 'bg-slate-950/60 border-slate-800 text-slate-400 hover:border-slate-700'
              }`}
            >
              <div className="text-xs text-amber-400 font-mono font-bold mb-1">AGGRESSIVE</div>
              <div className="text-base font-extrabold text-gray-100">5.0x – 10.0x</div>
              <div className="text-[10px] text-slate-400 mt-1">Maksymalny (10.0x)</div>
            </button>
          </div>
        </div>

        {/* Fine-Tuning Leverage Slider within Risk Bracket */}
        <div className="p-4 bg-slate-950/80 border border-slate-800 rounded-lg space-y-3">
          <div className="flex justify-between items-center">
            <label className="text-xs font-semibold text-slate-300">
              Dociągnięcie Dźwigni w ramach profilu <span className="text-blue-400 font-mono">{riskProfile}</span>:
            </label>
            <span className="text-base font-bold font-mono text-blue-400 bg-blue-950 px-2.5 py-0.5 rounded border border-blue-500/30">
              {Number(leverage).toFixed(1)}x
            </span>
          </div>
          <input
            type="range"
            min={range.min}
            max={range.max}
            step="0.5"
            value={leverage}
            onChange={(e) => setLeverage(e.target.value)}
            className="w-full h-2 bg-slate-800 rounded-lg appearance-none cursor-pointer accent-blue-500"
          />
          <div className="flex justify-between text-[10px] text-slate-500 font-mono">
            <span>Minimum ({range.min.toFixed(1)}x)</span>
            <span>Maksimum ({range.max.toFixed(1)}x)</span>
          </div>
        </div>

        {/* Stop Loss & Scout Mode Controls */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label className="block text-xs font-semibold text-slate-300 mb-1.5">
              Próg Stop Loss (%):
            </label>
            <input
              type="number"
              step="0.05"
              min="0.5"
              max="5.0"
              value={stopLoss}
              onChange={(e) => setStopLoss(e.target.value)}
              className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-lg text-sm text-slate-200 focus:outline-none focus:border-blue-500 font-mono"
            />
            <p className="text-[10px] text-slate-500 mt-1">Domyślnie 1.25% dla świecy wybijającej</p>
          </div>

          <div className="p-3 bg-slate-950/60 border border-slate-800 rounded-lg flex items-center justify-between">
            <div>
              <span className="font-semibold text-xs text-slate-200 block">Tryb SCOUT_MODE</span>
              <span className="text-[10px] text-slate-400">Redukcja ryzyka po aktywacji SL</span>
            </div>
            <button
              type="button"
              onClick={() => setIsScoutMode(!isScoutMode)}
              className={`w-11 h-5 flex items-center rounded-full p-0.5 transition-colors ${
                isScoutMode ? 'bg-amber-500 justify-end' : 'bg-slate-800 justify-start'
              }`}
            >
              <span className="w-3.5 h-3.5 rounded-full bg-white shadow-md" />
            </button>
          </div>
        </div>

        {/* API Authentication Key */}
        <div className="pt-2 border-t border-slate-800">
          <label className="block text-xs font-semibold text-slate-300 mb-1.5 flex items-center gap-1.5">
            <Key className="w-4 h-4 text-blue-400" />
            <span>MMS4 REST API Key</span>
          </label>
          <input
            type="password"
            placeholder="Wprowadź MMS4_API_KEY do zapisu..."
            value={currentApiKey}
            onChange={(e) => setCurrentApiKey(e.target.value)}
            className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-lg text-sm text-slate-200 focus:outline-none focus:border-blue-500 font-mono"
          />
        </div>

        {/* Save Strategy Settings Button */}
        <div className="flex items-center justify-between pt-2">
          {savedSuccess && (
            <span className="text-xs font-semibold text-emerald-400">
              ✓ Ustawienia strategii zapisane pomyślnie!
            </span>
          )}
          <button
            type="submit"
            className="ml-auto flex items-center gap-2 px-6 py-2.5 bg-blue-600 hover:bg-blue-500 text-white rounded-lg text-sm font-semibold transition-colors shadow-md shadow-blue-500/20"
          >
            <Save className="w-4 h-4" />
            <span>Zapisz Parametry</span>
          </button>
        </div>
      </form>

      {/* Password Change Form Card */}
      <form onSubmit={handlePasswordChange} className="bg-slate-900 border border-slate-800 rounded-xl p-6 shadow-lg space-y-4">
        <div className="flex items-center gap-3 pb-3 border-b border-slate-800">
          <div className="p-2.5 bg-amber-500/10 text-amber-400 rounded-lg">
            <Lock className="w-5 h-5" />
          </div>
          <div>
            <h3 className="text-base font-bold text-gray-100">Zmiana Hasła Logowania Panelu (BasicAuth)</h3>
            <p className="text-xs text-slate-400">Zaktualizuj hasło zabezpieczające dostęp HTTP BasicAuth w odwrotnym proxy Traefik</p>
          </div>
        </div>

        <div>
          <label className="block text-xs font-semibold text-slate-300 mb-1.5">
            Nowe Hasło Panelu:
          </label>
          <input
            type="password"
            placeholder="Wprowadź nowe hasło logowania..."
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            className="w-full px-3 py-2.5 bg-slate-950 border border-slate-800 rounded-lg text-sm text-slate-200 focus:outline-none focus:border-amber-500 font-mono"
            required
          />
        </div>

        <div className="flex items-center justify-between pt-1">
          {passwordMsg && (
            <span className={`text-xs font-semibold ${passwordMsg.includes('✓') ? 'text-emerald-400' : 'text-amber-400'}`}>
              {passwordMsg}
            </span>
          )}
          <button
            type="submit"
            className="ml-auto flex items-center gap-2 px-5 py-2 bg-amber-600 hover:bg-amber-500 text-white rounded-lg text-xs font-bold transition-colors shadow-md"
          >
            <Lock className="w-4 h-4" />
            <span>Zmień Hasło</span>
          </button>
        </div>
      </form>
    </div>
  );
}
