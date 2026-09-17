import React, { useState, useEffect, useRef } from 'react';
import { Sliders, Shield, Zap, Scale, Key, Lock, Save, CheckCircle2, AlertTriangle, ChevronDown, ChevronUp } from 'lucide-react';

export function SettingsTab({ stateData, statsData, apiKey, onUpdateSettings, daemonData }) {
  // Accordion open states
  const [openSections, setOpenSections] = useState({
    risk: true,
    engine: false,
    capital: false,
    security: false
  });

  const toggleSection = (section) => {
    setOpenSections((prev) => ({ ...prev, [section]: !prev[section] }));
  };

  // Form states
  const [riskProfile, setRiskProfile] = useState('MODERATE');
  const [leverage, setLeverage] = useState(stateData?.leverage || 3.0);
  const [stopLoss, setStopLoss] = useState(daemonData?.stop_loss_pct ?? stateData?.stop_loss_pct ?? 2.0);
  const [isScoutMode, setIsScoutMode] = useState(stateData?.state === 'SCOUT_MODE' || stateData?.scout_mode || false);
  const [currentApiKey, setCurrentApiKey] = useState(apiKey || '');

  // Adaptive Hybrid Engine
  const [engineMode, setEngineMode] = useState(daemonData?.engine_mode || 'CLASSIC');
  const [vrHigh, setVrHigh] = useState(daemonData?.adaptive_vr_high ?? 1.6);
  const [vrLow, setVrLow] = useState(daemonData?.adaptive_vr_low ?? 0.8);
  const [timeLockStart, setTimeLockStart] = useState(daemonData?.time_lock_start || '13:30');
  const [timeLockEnd, setTimeLockEnd] = useState(daemonData?.time_lock_end || '14:30');

  // Capital & Midline TP
  const [portfolioMarginCap, setPortfolioMarginCap] = useState(daemonData?.total_max_portfolio_margin_usd ?? 0);
  const [midlinePartialTpEnabled, setMidlinePartialTpEnabled] = useState(daemonData?.midline_partial_tp_enabled ?? false);
  const [midlineTpFraction, setMidlineTpFraction] = useState(daemonData?.midline_tp_fraction ?? 0.50);

  // Password change state
  const [newPassword, setNewPassword] = useState('');
  const [passwordMsg, setPasswordMsg] = useState('');

  // UI status feedback
  const [savedSuccess, setSavedSuccess] = useState(false);
  const [errorMsg, setErrorMsg] = useState('');
  const [isSaving, setIsSaving] = useState(false);

  const isInitializedRef = useRef(false);

  useEffect(() => {
    if (daemonData && !isInitializedRef.current) {
      if (daemonData.engine_mode) setEngineMode(daemonData.engine_mode);
      if (daemonData.adaptive_vr_high !== undefined) setVrHigh(daemonData.adaptive_vr_high);
      if (daemonData.adaptive_vr_low !== undefined) setVrLow(daemonData.adaptive_vr_low);
      if (daemonData.time_lock_start) setTimeLockStart(daemonData.time_lock_start);
      if (daemonData.time_lock_end) setTimeLockEnd(daemonData.time_lock_end);
      if (daemonData.total_max_portfolio_margin_usd !== undefined) setPortfolioMarginCap(daemonData.total_max_portfolio_margin_usd);
      if (daemonData.stop_loss_pct !== undefined) setStopLoss(daemonData.stop_loss_pct);
      if (daemonData.midline_partial_tp_enabled !== undefined) setMidlinePartialTpEnabled(daemonData.midline_partial_tp_enabled);
      if (daemonData.midline_tp_fraction !== undefined) setMidlineTpFraction(daemonData.midline_tp_fraction);
      isInitializedRef.current = true;
    }
  }, [daemonData]);

  useEffect(() => {
    if (apiKey) {
      setCurrentApiKey(apiKey);
    }
  }, [apiKey]);

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

  const handleSubmit = async (e) => {
    if (e) e.preventDefault();
    setErrorMsg('');
    setSavedSuccess(false);
    setIsSaving(true);
    try {
      if (onUpdateSettings) {
        await onUpdateSettings({
          risk_profile: riskProfile,
          leverage: parseFloat(leverage),
          stop_loss_pct: parseFloat(stopLoss),
          scout_mode: isScoutMode,
          api_key: currentApiKey,
          engine_mode: engineMode,
          adaptive_vr_high: parseFloat(vrHigh),
          adaptive_vr_low: parseFloat(vrLow),
          time_lock_start: timeLockStart,
          time_lock_end: timeLockEnd,
          total_max_portfolio_margin_usd: parseFloat(portfolioMarginCap),
          midline_partial_tp_enabled: midlinePartialTpEnabled,
          midline_tp_fraction: parseFloat(midlineTpFraction)
        });
      }
      setSavedSuccess(true);
      setTimeout(() => setSavedSuccess(false), 4000);
    } catch (err) {
      setErrorMsg(err.message || 'Błąd zapisu ustawień. Sprawdź MMS4 REST API Key.');
    } finally {
      setIsSaving(false);
    }
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
        setPasswordMsg('✓ Hasło zaktualizowane pomyślnie!');
        setNewPassword('');
      } else {
        setPasswordMsg('❌ Błąd zmiany hasła');
      }
    } catch (err) {
      setPasswordMsg('❌ Błąd połączenia z serwerem');
    }
  };

  return (
    <div className="max-w-4xl mx-auto space-y-3">
      {/* Toast Feedback */}
      {savedSuccess && (
        <div className="p-2.5 bg-emerald-950/90 border border-emerald-500/50 rounded-lg text-emerald-300 text-xs font-mono font-bold flex items-center gap-2 shadow-md">
          <CheckCircle2 className="w-4 h-4 text-emerald-400" />
          <span>✓ Parametry strategii zostały pomyślnie zapisane w SQLite & Daemonie.</span>
        </div>
      )}

      {errorMsg && (
        <div className="p-2.5 bg-rose-950/90 border border-rose-500/50 rounded-lg text-rose-300 text-xs font-mono font-bold flex items-center gap-2 shadow-md">
          <AlertTriangle className="w-4 h-4 text-rose-400" />
          <span>{errorMsg}</span>
        </div>
      )}

      <form onSubmit={handleSubmit} className="space-y-2">
        {/* Accordion Item 1: Profil Ryzyka & Dźwignia Strategii */}
        <div className="bg-slate-900/95 border border-slate-800 rounded-xl overflow-hidden shadow-sm">
          <button
            type="button"
            onClick={() => toggleSection('risk')}
            className="w-full px-4 py-3 flex items-center justify-between text-left hover:bg-slate-850/50 transition-colors"
          >
            <div className="flex items-center gap-2.5">
              <div className="p-1.5 bg-blue-500/10 text-blue-400 border border-blue-500/20 rounded">
                <Sliders className="w-4 h-4" />
              </div>
              <div>
                <h3 className="font-mono font-bold text-xs sm:text-sm text-gray-100 uppercase tracking-wider">
                  1. Profil Ryzyka & Dźwignia Strategii
                </h3>
                <p className="text-[10px] text-slate-400 font-mono">
                  Dźwignia bazowa: {Number(leverage).toFixed(1)}x · Profil: {riskProfile}
                </p>
              </div>
            </div>
            {openSections.risk ? <ChevronUp className="w-4 h-4 text-slate-400" /> : <ChevronDown className="w-4 h-4 text-slate-400" />}
          </button>

          {openSections.risk && (
            <div className="p-4 pt-2 border-t border-slate-800/80 space-y-3">
              {/* Risk Profile Selector Buttons */}
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                {[
                  { id: 'CONSERVATIVE', title: 'CONSERVATIVE', range: '1.0x – 2.0x', desc: 'Bezpieczny SPOT', color: 'text-emerald-400', border: 'border-emerald-500/50' },
                  { id: 'MODERATE', title: 'MODERATE', range: '2.0x – 5.0x', desc: 'Standard (3.0x)', color: 'text-blue-400', border: 'border-blue-500/50' },
                  { id: 'AGGRESSIVE', title: 'AGGRESSIVE', range: '5.0x – 10.0x', desc: 'Maksymalny (10.0x)', color: 'text-amber-400', border: 'border-amber-500/50' }
                ].map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    onClick={() => handleRiskChange(item.id)}
                    className={`p-2.5 rounded-lg border text-left transition-all ${
                      riskProfile === item.id
                        ? `bg-slate-950 ${item.border} ${item.color} shadow-sm font-bold`
                        : 'bg-slate-950/50 border-slate-800 text-slate-400 hover:border-slate-700'
                    }`}
                  >
                    <div className="text-[10px] font-mono uppercase tracking-wider">{item.title}</div>
                    <div className="text-sm font-extrabold font-mono text-gray-100">{item.range}</div>
                    <div className="text-[9px] text-slate-500">{item.desc}</div>
                  </button>
                ))}
              </div>

              {/* Leverage Slider */}
              <div className="bg-slate-950/70 border border-slate-800 rounded-lg p-2.5 space-y-1.5">
                <div className="flex justify-between items-center text-xs font-mono">
                  <span className="text-slate-300">Dociągnięcie Dźwigni ({riskProfile}):</span>
                  <span className="font-bold text-blue-400 tabular-nums bg-blue-950 px-2 py-0.5 rounded border border-blue-500/30">
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
                  className="w-full h-1.5 bg-slate-800 rounded appearance-none cursor-pointer accent-blue-500"
                />
              </div>

              {/* Stop Loss & SCOUT Mode Toggle Switch */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 pt-1">
                <div>
                  <label className="block text-[11px] font-mono text-slate-300 mb-1">
                    Bazowy Stop Loss (%):
                  </label>
                  <input
                    type="number"
                    step="0.1"
                    min="0.5"
                    max="10.0"
                    value={stopLoss}
                    onChange={(e) => setStopLoss(e.target.value)}
                    className="w-full px-2.5 py-1.5 bg-slate-950 border border-slate-800 rounded text-xs font-mono font-bold text-gray-100 focus:outline-none focus:border-blue-500"
                  />
                </div>

                {/* Toggle Switch for SCOUT Mode */}
                <div className="flex items-center justify-between p-2.5 bg-slate-950/70 border border-slate-800 rounded-lg">
                  <div>
                    <span className="text-xs font-mono font-bold text-slate-200 block">Tryb Obronny SCOUT</span>
                    <span className="text-[10px] text-slate-500">Redukcja wielkości pozycji</span>
                  </div>
                  <label className="relative inline-flex items-center cursor-pointer">
                    <input
                      type="checkbox"
                      checked={isScoutMode}
                      onChange={(e) => setIsScoutMode(e.target.checked)}
                      className="sr-only peer"
                    />
                    <div className="w-9 h-5 bg-slate-800 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-amber-500" />
                  </label>
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Accordion Item 2: Silnik Adaptacyjny & Filtry Zmienności */}
        <div className="bg-slate-900/95 border border-slate-800 rounded-xl overflow-hidden shadow-sm">
          <button
            type="button"
            onClick={() => toggleSection('engine')}
            className="w-full px-4 py-3 flex items-center justify-between text-left hover:bg-slate-850/50 transition-colors"
          >
            <div className="flex items-center gap-2.5">
              <div className="p-1.5 bg-amber-500/10 text-amber-400 border border-amber-500/20 rounded">
                <Zap className="w-4 h-4" />
              </div>
              <div>
                <h3 className="font-mono font-bold text-xs sm:text-sm text-gray-100 uppercase tracking-wider">
                  2. Silnik Adaptacyjny & Filtry Zmienności
                </h3>
                <p className="text-[10px] text-slate-400 font-mono">
                  Tryb: {engineMode} · VR High: {vrHigh} · VR Low: {vrLow}
                </p>
              </div>
            </div>
            {openSections.engine ? <ChevronUp className="w-4 h-4 text-slate-400" /> : <ChevronDown className="w-4 h-4 text-slate-400" />}
          </button>

          {openSections.engine && (
            <div className="p-4 pt-2 border-t border-slate-800/80 space-y-3">
              {/* Engine Mode Toggle Switch */}
              <div className="flex items-center justify-between p-2.5 bg-slate-950/70 border border-slate-800 rounded-lg">
                <div>
                  <span className="text-xs font-mono font-bold text-slate-200 block">
                    Silnik: {engineMode}
                  </span>
                  <span className="text-[10px] text-slate-500">
                    {engineMode === 'ADAPTIVE_HYBRID' ? 'Filtry VR, trendu i okna time-lock aktywne' : 'Klasyczna strategia TMA'}
                  </span>
                </div>
                <button
                  type="button"
                  onClick={() => setEngineMode((m) => (m === 'ADAPTIVE_HYBRID' ? 'CLASSIC' : 'ADAPTIVE_HYBRID'))}
                  className={`px-3 py-1 rounded text-xs font-mono font-bold transition-colors border ${
                    engineMode === 'ADAPTIVE_HYBRID'
                      ? 'bg-blue-600 text-white border-blue-500'
                      : 'bg-slate-850 text-slate-400 border-slate-700'
                  }`}
                >
                  {engineMode === 'ADAPTIVE_HYBRID' ? 'ADAPTIVE ON' : 'CLASSIC'}
                </button>
              </div>

              {/* VR Thresholds & Time-Lock Windows */}
              <div className="grid grid-cols-1 sm:grid-cols-4 gap-2 text-xs font-mono">
                <div>
                  <label className="block text-[10px] text-slate-400 mb-1">VR High Próg:</label>
                  <input
                    type="number"
                    step="0.1"
                    value={vrHigh}
                    onChange={(e) => setVrHigh(e.target.value)}
                    className="w-full px-2 py-1 bg-slate-950 border border-slate-800 rounded font-bold text-gray-100"
                  />
                </div>
                <div>
                  <label className="block text-[10px] text-slate-400 mb-1">VR Low Próg:</label>
                  <input
                    type="number"
                    step="0.1"
                    value={vrLow}
                    onChange={(e) => setVrLow(e.target.value)}
                    className="w-full px-2 py-1 bg-slate-950 border border-slate-800 rounded font-bold text-gray-100"
                  />
                </div>
                <div>
                  <label className="block text-[10px] text-slate-400 mb-1">Time Lock Start:</label>
                  <input
                    type="time"
                    value={timeLockStart}
                    onChange={(e) => setTimeLockStart(e.target.value)}
                    className="w-full px-2 py-1 bg-slate-950 border border-slate-800 rounded font-bold text-gray-100"
                  />
                </div>
                <div>
                  <label className="block text-[10px] text-slate-400 mb-1">Time Lock End:</label>
                  <input
                    type="time"
                    value={timeLockEnd}
                    onChange={(e) => setTimeLockEnd(e.target.value)}
                    className="w-full px-2 py-1 bg-slate-950 border border-slate-800 rounded font-bold text-gray-100"
                  />
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Accordion Item 3: Alokacja Kapitału & Midline Take-Profit */}
        <div className="bg-slate-900/95 border border-slate-800 rounded-xl overflow-hidden shadow-sm">
          <button
            type="button"
            onClick={() => toggleSection('capital')}
            className="w-full px-4 py-3 flex items-center justify-between text-left hover:bg-slate-850/50 transition-colors"
          >
            <div className="flex items-center gap-2.5">
              <div className="p-1.5 bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 rounded">
                <Scale className="w-4 h-4" />
              </div>
              <div>
                <h3 className="font-mono font-bold text-xs sm:text-sm text-gray-100 uppercase tracking-wider">
                  3. Alokacja Kapitału & Midline Take-Profit
                </h3>
                <p className="text-[10px] text-slate-400 font-mono">
                  Margin Cap: ${portfolioMarginCap || 'Brak'} · Midline TP: {midlinePartialTpEnabled ? `${(midlineTpFraction * 100).toFixed(0)}%` : 'Wyłączone'}
                </p>
              </div>
            </div>
            {openSections.capital ? <ChevronUp className="w-4 h-4 text-slate-400" /> : <ChevronDown className="w-4 h-4 text-slate-400" />}
          </button>

          {openSections.capital && (
            <div className="p-4 pt-2 border-t border-slate-800/80 space-y-3">
              <div>
                <label className="block text-[11px] font-mono text-slate-300 mb-1">
                  Maksymalny Łączny Depozyt Portfela (USD, 0 = bez limitu):
                </label>
                <input
                  type="number"
                  step="10"
                  value={portfolioMarginCap}
                  onChange={(e) => setPortfolioMarginCap(e.target.value)}
                  className="w-full px-2.5 py-1.5 bg-slate-950 border border-slate-800 rounded text-xs font-mono font-bold text-gray-100 focus:outline-none"
                />
              </div>

              {/* Midline Partial TP Toggle Switch */}
              <div className="flex items-center justify-between p-2.5 bg-slate-950/70 border border-slate-800 rounded-lg">
                <div>
                  <span className="text-xs font-mono font-bold text-slate-200 block">
                    Częściowa Realizacja na Linii Środkowej (Midline TP)
                  </span>
                  <span className="text-[10px] text-slate-500">
                    Zamyka część pozycji przy dotknięciu TMA Midline
                  </span>
                </div>
                <label className="relative inline-flex items-center cursor-pointer">
                  <input
                    type="checkbox"
                    checked={midlinePartialTpEnabled}
                    onChange={(e) => setMidlinePartialTpEnabled(e.target.checked)}
                    className="sr-only peer"
                  />
                  <div className="w-9 h-5 bg-slate-800 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-emerald-500" />
                </label>
              </div>

              {midlinePartialTpEnabled && (
                <div>
                  <label className="block text-[11px] font-mono text-slate-300 mb-1">
                    Frakcja Midline TP (np. 0.50 = 50% pozycji):
                  </label>
                  <input
                    type="number"
                    step="0.05"
                    min="0.10"
                    max="0.90"
                    value={midlineTpFraction}
                    onChange={(e) => setMidlineTpFraction(e.target.value)}
                    className="w-full px-2.5 py-1.5 bg-slate-950 border border-slate-800 rounded text-xs font-mono font-bold text-gray-100"
                  />
                </div>
              )}
            </div>
          )}
        </div>

        {/* Accordion Item 4: Dostęp API & Bezpieczeństwo Panelu */}
        <div className="bg-slate-900/95 border border-slate-800 rounded-xl overflow-hidden shadow-sm">
          <button
            type="button"
            onClick={() => toggleSection('security')}
            className="w-full px-4 py-3 flex items-center justify-between text-left hover:bg-slate-850/50 transition-colors"
          >
            <div className="flex items-center gap-2.5">
              <div className="p-1.5 bg-violet-500/10 text-violet-400 border border-violet-500/20 rounded">
                <Key className="w-4 h-4" />
              </div>
              <div>
                <h3 className="font-mono font-bold text-xs sm:text-sm text-gray-100 uppercase tracking-wider">
                  4. Dostęp API & Bezpieczeństwo Panelu
                </h3>
                <p className="text-[10px] text-slate-400 font-mono">
                  Klucz API oraz hasło dostępowe panelu
                </p>
              </div>
            </div>
            {openSections.security ? <ChevronUp className="w-4 h-4 text-slate-400" /> : <ChevronDown className="w-4 h-4 text-slate-400" />}
          </button>

          {openSections.security && (
            <div className="p-4 pt-2 border-t border-slate-800/80 space-y-3">
              <div>
                <label className="block text-[11px] font-mono text-slate-300 mb-1">
                  MMS4 REST API Key (Nagłówek X-MMS4-API-KEY):
                </label>
                <input
                  type="password"
                  value={currentApiKey}
                  onChange={(e) => setCurrentApiKey(e.target.value)}
                  placeholder="Wpisz klucz API..."
                  className="w-full px-2.5 py-1.5 bg-slate-950 border border-slate-800 rounded text-xs font-mono font-bold text-gray-100"
                />
              </div>

              {/* Password Change Sub-Box */}
              <div className="bg-slate-950/70 border border-slate-800 rounded-lg p-3 space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-mono font-bold text-slate-200">Zmiana Hasła Panelu (admin)</span>
                  {passwordMsg && (
                    <span className="text-[10px] font-mono font-bold text-amber-400">{passwordMsg}</span>
                  )}
                </div>
                <div className="flex gap-2">
                  <input
                    type="password"
                    placeholder="Nowe hasło..."
                    value={newPassword}
                    onChange={(e) => setNewPassword(e.target.value)}
                    className="w-full px-2.5 py-1 bg-slate-900 border border-slate-800 rounded text-xs font-mono text-gray-100"
                  />
                  <button
                    type="button"
                    onClick={handlePasswordChange}
                    className="px-3 py-1 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded font-mono text-xs font-bold shrink-0 transition-colors"
                  >
                    Aktualizuj
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Save Action Sticky / Bottom Bar */}
        <div className="pt-2">
          <button
            type="submit"
            disabled={isSaving}
            className="w-full py-2.5 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white rounded-xl font-mono font-bold text-xs uppercase tracking-wider transition-all flex items-center justify-center gap-2 shadow-lg shadow-emerald-600/20"
          >
            <Save className="w-4 h-4" />
            <span>{isSaving ? 'Zapisywanie w toku...' : 'Zapisz Wszystkie Ustawienia'}</span>
          </button>
        </div>
      </form>
    </div>
  );
}
