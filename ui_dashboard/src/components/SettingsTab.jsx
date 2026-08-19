import React, { useState, useEffect } from 'react';
import { Sliders, Lock, Key, Save, Shield, PieChart, CheckCircle2, Zap, Clock, Activity, AlertTriangle } from 'lucide-react';

export function SettingsTab({ stateData, statsData, apiKey, onUpdateSettings, daemonData }) {
  const [riskProfile, setRiskProfile] = useState('MODERATE');
  const [leverage, setLeverage] = useState(stateData?.leverage || 3.0);
  const [stopLoss, setStopLoss] = useState(stateData?.stop_loss_pct || 1.25);
  const [isScoutMode, setIsScoutMode] = useState(stateData?.state === 'SCOUT_MODE' || stateData?.scout_mode || false);
  const [currentApiKey, setCurrentApiKey] = useState(apiKey || '');

  // ── Adaptive Hybrid Engine State ──
  const [engineMode, setEngineMode] = useState(daemonData?.engine_mode || 'CLASSIC');
  const [vrHigh, setVrHigh] = useState(daemonData?.adaptive_vr_high ?? 1.6);
  const [vrLow, setVrLow] = useState(daemonData?.adaptive_vr_low ?? 0.8);
  const [timeLockStart, setTimeLockStart] = useState(daemonData?.time_lock_start || '13:30');
  const [timeLockEnd, setTimeLockEnd] = useState(daemonData?.time_lock_end || '14:30');
  const [portfolioMarginCap, setPortfolioMarginCap] = useState(daemonData?.total_max_portfolio_margin_usd ?? 0);

  // Sync state if daemonData changes
  useEffect(() => {
    if (daemonData) {
      if (daemonData.engine_mode) setEngineMode(daemonData.engine_mode);
      if (daemonData.adaptive_vr_high !== undefined) setVrHigh(daemonData.adaptive_vr_high);
      if (daemonData.adaptive_vr_low !== undefined) setVrLow(daemonData.adaptive_vr_low);
      if (daemonData.time_lock_start) setTimeLockStart(daemonData.time_lock_start);
      if (daemonData.time_lock_end) setTimeLockEnd(daemonData.time_lock_end);
      if (daemonData.total_max_portfolio_margin_usd !== undefined) setPortfolioMarginCap(daemonData.total_max_portfolio_margin_usd);
    }
  }, [daemonData]);

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
        api_key: currentApiKey,
        engine_mode: engineMode,
        adaptive_vr_high: parseFloat(vrHigh),
        adaptive_vr_low: parseFloat(vrLow),
        time_lock_start: timeLockStart,
        time_lock_end: timeLockEnd,
        total_max_portfolio_margin_usd: parseFloat(portfolioMarginCap)
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
            Wybierz Profil Ryzyka (Risk Profile):
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

        {/* ── Portfolio Margin Guard (Circuit Breaker) ── */}
        <div className="p-4 bg-slate-950/60 border border-slate-800 rounded-lg space-y-2">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Shield className="w-4 h-4 text-emerald-400" />
              <span className="text-xs font-semibold text-slate-200">Portfolio Margin Guard ($ USD):</span>
            </div>
            <span className="text-[10px] text-slate-400">Globalny limit portfela (0 = wyłączony)</span>
          </div>
          <input
            type="number"
            step="10"
            min="0"
            value={portfolioMarginCap}
            onChange={(e) => setPortfolioMarginCap(e.target.value)}
            placeholder="0 = wyłączony"
            className="w-full px-3 py-2 bg-slate-900 border border-slate-800 rounded-lg text-sm text-slate-200 focus:outline-none focus:border-emerald-500 font-mono"
          />
        </div>

        {/* ── Hybrydowy Silnik Adaptacyjny (Adaptive Engine Card) ── */}
        <div className="p-5 bg-gradient-to-br from-slate-950 via-slate-900 to-indigo-950/40 border border-indigo-500/30 rounded-xl space-y-4 shadow-inner">
          <div className="flex items-center justify-between pb-3 border-b border-indigo-500/20">
            <div className="flex items-center gap-2.5">
              <div className="p-2 bg-indigo-500/10 text-indigo-400 rounded-lg border border-indigo-500/20">
                <Zap className="w-5 h-5" />
              </div>
              <div>
                <h4 className="text-sm font-bold text-gray-100 flex items-center gap-2">
                  <span>Hybrydowy Silnik Adaptacyjny</span>
                  <span className={`text-[10px] px-2 py-0.5 rounded-full font-mono font-bold ${
                    engineMode === 'ADAPTIVE'
                      ? 'bg-indigo-500/20 text-indigo-300 border border-indigo-500/40'
                      : 'bg-slate-800 text-slate-400 border border-slate-700'
                  }`}>
                    {engineMode}
                  </span>
                </h4>
                <p className="text-[11px] text-slate-400">Nadzór Fast-Path: Filtr Zmienności (VR) & Sesji US Open</p>
              </div>
            </div>

            {/* Engine Mode Switch */}
            <select
              value={engineMode}
              onChange={(e) => setEngineMode(e.target.value)}
              className="px-3 py-1.5 bg-slate-900 border border-indigo-500/40 rounded-lg text-xs font-bold text-indigo-300 focus:outline-none focus:border-indigo-400 font-mono cursor-pointer"
            >
              <option value="CLASSIC">CLASSIC (Standardowy)</option>
              <option value="ADAPTIVE">ADAPTIVE (Hybrydowy)</option>
            </select>
          </div>

          {engineMode === 'ADAPTIVE' ? (
            <div className="space-y-4 pt-1">
              {/* Volatility Ratio Thresholds */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div className="p-3 bg-slate-900/80 border border-slate-800 rounded-lg">
                  <label className="block text-[11px] font-semibold text-slate-300 mb-1 flex items-center justify-between">
                    <span>Górny Próg VR (High Trend):</span>
                    <span className="font-mono text-amber-400 font-bold">{vrHigh}x</span>
                  </label>
                  <input
                    type="number"
                    step="0.1"
                    min="1.0"
                    max="5.0"
                    value={vrHigh}
                    onChange={(e) => setVrHigh(e.target.value)}
                    className="w-full px-2.5 py-1.5 bg-slate-950 border border-slate-800 rounded text-xs text-slate-200 font-mono focus:outline-none focus:border-indigo-500"
                  />
                  <p className="text-[10px] text-slate-500 mt-1">Blokuje HIGH_RISK_ADD w silnym trendzie</p>
                </div>

                <div className="p-3 bg-slate-900/80 border border-slate-800 rounded-lg">
                  <label className="block text-[11px] font-semibold text-slate-300 mb-1 flex items-center justify-between">
                    <span>Dolny Próg VR (Compression):</span>
                    <span className="font-mono text-blue-400 font-bold">{vrLow}x</span>
                  </label>
                  <input
                    type="number"
                    step="0.1"
                    min="0.1"
                    max="1.5"
                    value={vrLow}
                    onChange={(e) => setVrLow(e.target.value)}
                    className="w-full px-2.5 py-1.5 bg-slate-950 border border-slate-800 rounded text-xs text-slate-200 font-mono focus:outline-none focus:border-indigo-500"
                  />
                  <p className="text-[10px] text-slate-500 mt-1">Wymaga knotu Sweep Rejection w marazmie</p>
                </div>
              </div>

              {/* Time Lock Hours */}
              <div className="p-3 bg-slate-900/80 border border-slate-800 rounded-lg space-y-2">
                <div className="flex items-center gap-1.5 text-[11px] font-semibold text-slate-300">
                  <Clock className="w-3.5 h-3.5 text-indigo-400" />
                  <span>Okno Blokady Sesyjnej US Open (Godziny UTC):</span>
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-[10px] text-slate-400 mb-1">Start (HH:MM UTC):</label>
                    <input
                      type="text"
                      pattern="[0-9]{2}:[0-9]{2}"
                      value={timeLockStart}
                      onChange={(e) => setTimeLockStart(e.target.value)}
                      placeholder="13:30"
                      className="w-full px-2.5 py-1.5 bg-slate-950 border border-slate-800 rounded text-xs text-slate-200 font-mono focus:outline-none focus:border-indigo-500"
                    />
                  </div>
                  <div>
                    <label className="block text-[10px] text-slate-400 mb-1">Koniec (HH:MM UTC):</label>
                    <input
                      type="text"
                      pattern="[0-9]{2}:[0-9]{2}"
                      value={timeLockEnd}
                      onChange={(e) => setTimeLockEnd(e.target.value)}
                      placeholder="14:30"
                      className="w-full px-2.5 py-1.5 bg-slate-950 border border-slate-800 rounded text-xs text-slate-200 font-mono focus:outline-none focus:border-indigo-500"
                    />
                  </div>
                </div>
                <p className="text-[10px] text-slate-500">
                  Blokuje sygnały BASE_SHORT na otwarciu Wall Street, chroniąc przed polowaniem na płynność.
                </p>
              </div>
            </div>
          ) : (
            <div className="p-3 bg-slate-900/50 border border-slate-800/80 rounded-lg text-xs text-slate-400">
              Tryb CLASSIC aktywny: kalkulacje VR i blokady czasowe US Open są pomijane (Standardowa strategia MMS v4).
            </div>
          )}
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
              ✓ Ustawienia strategii i silnika adaptacyjnego zapisane!
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
