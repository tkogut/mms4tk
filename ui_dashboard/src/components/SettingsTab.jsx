import React, { useState } from 'react';
import { Settings, Shield, Key, Save, Lock, Sliders } from 'lucide-react';

export function SettingsTab({ stateData, onUpdateSettings }) {
  const [leverage, setLeverage] = useState(stateData?.leverage || 3.0);
  const [stopLoss, setStopLoss] = useState(stateData?.stop_loss_pct || 1.25);
  const [isScoutMode, setIsScoutMode] = useState(stateData?.scout_mode || false);
  const [apiKey, setApiKey] = useState('');
  const [savedSuccess, setSavedSuccess] = useState(false);

  const handleSubmit = (e) => {
    e.preventDefault();
    if (onUpdateSettings) {
      onUpdateSettings({
        leverage: parseFloat(leverage),
        stop_loss_pct: parseFloat(stopLoss),
        scout_mode: isScoutMode,
        api_key: apiKey
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
            <h3 className="text-lg font-bold text-gray-100">Parametry Strategii & Alokacji</h3>
            <p className="text-xs text-slate-400">Konfiguracja dźwigni, progu Stop Loss oraz trybów ryzyka bota</p>
          </div>
        </div>

        {/* Leverage & Stop Loss Controls */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-6">
          <div>
            <label className="block text-xs font-semibold text-slate-300 mb-2">
              Dźwignia Alokacji (Leverage): <span className="text-blue-400 font-mono">{leverage}x</span>
            </label>
            <input
              type="range"
              min="1.0"
              max="5.0"
              step="0.5"
              value={leverage}
              onChange={(e) => setLeverage(e.target.value)}
              className="w-full h-2 bg-slate-800 rounded-lg appearance-none cursor-pointer accent-blue-500"
            />
            <div className="flex justify-between text-[10px] text-slate-500 font-mono mt-1">
              <span>1.0x (Bezpieczny)</span>
              <span>3.0x (Standard)</span>
              <span>5.0x (Agresywny)</span>
            </div>
          </div>

          <div>
            <label className="block text-xs font-semibold text-slate-300 mb-2">
              Próg Stop Loss (%): <span className="text-red-400 font-mono">{stopLoss}%</span>
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
            <p className="text-[10px] text-slate-500 mt-1">Szybka redukcja pozycji przy wybitku świecy</p>
          </div>
        </div>

        {/* Scout Mode Toggle */}
        <div className="p-4 bg-slate-950/60 border border-slate-800 rounded-lg flex items-center justify-between">
          <div>
            <span className="font-semibold text-sm text-slate-200 block">Tryb Zwiadowcy (SCOUT_MODE)</span>
            <span className="text-xs text-slate-400">Ogranicza wielkość nowych pozycji po aktywacji Stop Loss</span>
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

        {/* Authentication Key */}
        <div className="pt-4 border-t border-slate-800">
          <label className="block text-xs font-semibold text-slate-300 mb-2 flex items-center gap-1.5">
            <Key className="w-4 h-4 text-blue-400" />
            <span>MMS4 REST API Key</span>
          </label>
          <input
            type="password"
            placeholder="Wprowadź MMS4_API_KEY do zapisu..."
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            className="w-full px-3 py-2.5 bg-slate-950 border border-slate-800 rounded-lg text-sm text-slate-200 focus:outline-none focus:border-blue-500 font-mono"
          />
        </div>

        {/* Save Button */}
        <div className="flex items-center justify-between pt-2">
          {savedSuccess && (
            <span className="text-xs font-semibold text-emerald-400">
              ✓ Ustawienia zapisane pomyślnie!
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
