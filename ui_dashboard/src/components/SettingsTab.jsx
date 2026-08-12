import React, { useState } from 'react';
import { Settings, Shield, Key, Save, Lock, Sliders, PieChart, DollarSign, Cpu } from 'lucide-react';

export function SettingsTab({ stateData, statsData, apiKey, onUpdateSettings }) {
  const [leverage, setLeverage] = useState(stateData?.leverage || 3.0);
  const [stopLoss, setStopLoss] = useState(stateData?.stop_loss_pct || 1.25);
  const [isScoutMode, setIsScoutMode] = useState(stateData?.state === 'SCOUT_MODE' || stateData?.scout_mode || false);
  const [currentApiKey, setCurrentApiKey] = useState(apiKey || '');
  const [maxMarginUsd, setMaxMarginUsd] = useState(10.0);
  const [capitalSplit, setCapitalSplit] = useState(70);
  const [savedSuccess, setSavedSuccess] = useState(false);

  const handleSubmit = (e) => {
    e.preventDefault();
    if (onUpdateSettings) {
      onUpdateSettings({
        leverage: parseFloat(leverage),
        stop_loss_pct: parseFloat(stopLoss),
        scout_mode: isScoutMode,
        api_key: currentApiKey,
        max_margin_usd: parseFloat(maxMarginUsd),
        capital_split_ratio: parseFloat(capitalSplit) / 100.0
      });
    }
    setSavedSuccess(true);
    setTimeout(() => setSavedSuccess(false), 3000);
  };

  return (
    <div className="max-w-3xl space-y-6">
      <form onSubmit={handleSubmit} className="bg-slate-900 border border-slate-800 rounded-xl p-6 shadow-lg space-y-6">
        <div className="flex items-center gap-3 pb-4 border-b border-slate-800">
          <div className="p-2.5 bg-blue-500/10 text-blue-400 rounded-lg">
            <Sliders className="w-6 h-6" />
          </div>
          <div>
            <h3 className="text-lg font-bold text-gray-100">Parametry Strategii & Alokacji Kapitału</h3>
            <p className="text-xs text-slate-400">Konfiguracja dźwigni (1-10x), Stop Loss, alokacji oraz zabezpieczeń .env</p>
          </div>
        </div>

        {/* Leverage Control (1.0x to 10.0x) */}
        <div className="space-y-4">
          <div>
            <div className="flex justify-between items-center mb-2">
              <label className="text-xs font-semibold text-slate-300">
                Dźwignia Alokacji Strategii (Leverage):
              </label>
              <span className="text-base font-bold font-mono text-blue-400 bg-blue-950 px-2.5 py-0.5 rounded border border-blue-500/30">
                {Number(leverage).toFixed(1)}x
              </span>
            </div>
            <input
              type="range"
              min="1.0"
              max="10.0"
              step="0.5"
              value={leverage}
              onChange={(e) => setLeverage(e.target.value)}
              className="w-full h-2 bg-slate-800 rounded-lg appearance-none cursor-pointer accent-blue-500"
            />
            <div className="flex justify-between text-[10px] text-slate-500 font-mono mt-1">
              <span>1.0x (Bezpieczny SPOT)</span>
              <span>3.0x (Standard)</span>
              <span>5.0x (Średni)</span>
              <span>10.0x (Maksymalny)</span>
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 pt-2">
            <div>
              <label className="block text-xs font-semibold text-slate-300 mb-1.5">
                Próg Stop Loss (%):
              </label>
              <input
                type="number"
                step="0.05"
                min="0.5"
                max="10.0"
                value={stopLoss}
                onChange={(e) => setStopLoss(e.target.value)}
                className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-lg text-sm text-slate-200 focus:outline-none focus:border-blue-500 font-mono"
              />
              <p className="text-[10px] text-slate-500 mt-1">Ochrona pozycji przy nagłym wybiu świecy</p>
            </div>

            <div>
              <label className="block text-xs font-semibold text-slate-300 mb-1.5">
                Maksymalny Margines Pozycji (USD):
              </label>
              <input
                type="number"
                step="1.0"
                min="5.0"
                max="500.0"
                value={maxMarginUsd}
                onChange={(e) => setMaxMarginUsd(e.target.value)}
                className="w-full px-3 py-2 bg-slate-950 border border-slate-800 rounded-lg text-sm text-slate-200 focus:outline-none focus:border-blue-500 font-mono"
              />
              <p className="text-[10px] text-slate-500 mt-1">Maksymalny kapitał przydzielony na pozycję</p>
            </div>
          </div>
        </div>

        {/* Capital Split Allocation (70/30) */}
        <div className="p-4 bg-slate-950/60 border border-slate-800 rounded-lg space-y-3">
          <div className="flex items-center justify-between">
            <span className="font-semibold text-xs text-slate-200 flex items-center gap-1.5">
              <PieChart className="w-4 h-4 text-emerald-400" />
              <span>Podział Kapitału (Capital Split): {capitalSplit}% / {100 - capitalSplit}%</span>
            </span>
            <span className="text-xs font-mono text-emerald-400 font-bold">
              {capitalSplit}% Baza / {100 - capitalSplit}% Dodatek
            </span>
          </div>
          <input
            type="range"
            min="50"
            max="90"
            step="5"
            value={capitalSplit}
            onChange={(e) => setCapitalSplit(e.target.value)}
            className="w-full h-2 bg-slate-800 rounded-lg appearance-none cursor-pointer accent-emerald-500"
          />
          <p className="text-[10px] text-slate-400">
            Domyślna zasada 70% kapitału na pierwszą pozycję + 30% na dociągnięcie przy wyższym prawdopodobieństwie.
          </p>
        </div>

        {/* Scout Mode Toggle */}
        <div className="p-4 bg-slate-950/60 border border-slate-800 rounded-lg flex items-center justify-between">
          <div>
            <span className="font-semibold text-sm text-slate-200 block">Tryb Zwiadowcy (SCOUT_MODE)</span>
            <span className="text-xs text-slate-400">Automatyczna redukcja ryzyka po aktywacji Stop Loss</span>
          </div>
          <button
            type="button"
            onClick={() => setIsScoutMode(!isScoutMode)}
            className={`w-12 h-6 flex items-center rounded-full p-1 transition-colors ${
              isScoutMode ? 'bg-amber-500 justify-end' : 'bg-slate-800 justify-start'
            }`}
          >
            <span className="w-4 h-4 rounded-full bg-white shadow-md transform transition-transform" />
          </button>
        </div>

        {/* Security & In-App Key Manager */}
        <div className="pt-4 border-t border-slate-800 space-y-3">
          <label className="block text-xs font-semibold text-slate-300 flex items-center gap-1.5">
            <Lock className="w-4 h-4 text-amber-400" />
            <span>MMS4 REST API Key / Hasło Dostępu:</span>
          </label>
          <input
            type="password"
            placeholder="Wprowadź MMS4_API_KEY do zapisu..."
            value={currentApiKey}
            onChange={(e) => setCurrentApiKey(e.target.value)}
            className="w-full px-3 py-2.5 bg-slate-950 border border-slate-800 rounded-lg text-sm text-slate-200 focus:outline-none focus:border-blue-500 font-mono"
          />
          <p className="text-[10px] text-slate-500">
            🔒 Panel zabezpieczony jest dodatkowo hasłem HTTP BasicAuth w odwrotnym proxy Traefik.
          </p>
        </div>

        {/* Save Button */}
        <div className="flex items-center justify-between pt-2">
          {savedSuccess && (
            <span className="text-xs font-semibold text-emerald-400">
              ✓ Parametry zaktualizowane i zapisane pomyślnie!
            </span>
          )}
          <button
            type="submit"
            className="ml-auto flex items-center gap-2 px-6 py-2.5 bg-blue-600 hover:bg-blue-500 text-white rounded-lg text-sm font-semibold transition-colors shadow-md shadow-blue-500/20"
          >
            <Save className="w-4 h-4" />
            <span>Zapisz Zmiany</span>
          </button>
        </div>
      </form>
    </div>
  );
}
