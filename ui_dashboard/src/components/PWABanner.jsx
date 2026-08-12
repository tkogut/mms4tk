import React, { useState } from 'react';
import { Download, X, HelpCircle, Smartphone, Monitor } from 'lucide-react';

export function PWABanner({ isInstallable, handleInstall }) {
  const [showInstructions, setShowInstructions] = useState(false);
  const [dismissed, setDismissed] = useState(false);

  if (dismissed) return null;

  const onInstallClick = async () => {
    const installed = await handleInstall();
    if (!installed) {
      setShowInstructions(!showInstructions);
    }
  };

  return (
    <div className="mb-6 bg-gradient-to-r from-slate-900 via-slate-800 to-slate-900 border border-blue-500/40 rounded-xl p-4 shadow-lg shadow-blue-500/10">
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <div className="p-2.5 bg-blue-500/20 text-blue-400 rounded-lg">
            <Smartphone className="w-5 h-5" />
          </div>
          <div>
            <h3 className="font-semibold text-blue-300 text-sm sm:text-base">
              📲 Zainstaluj Mastermind App na Pulpicie
            </h3>
            <p className="text-xs text-slate-400">
              Szybki dostęp z ekranu głównego telefonu lub komputera (PWA)
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2 w-full sm:w-auto">
          <button
            onClick={onInstallClick}
            className="flex-1 sm:flex-initial flex items-center justify-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white rounded-lg font-medium text-xs sm:text-sm transition-colors shadow-md"
          >
            <Download className="w-4 h-4" />
            <span>{isInstallable ? 'Zainstaluj / Dodaj' : 'Jak Zainstalować?'}</span>
          </button>

          <button
            onClick={() => setShowInstructions(!showInstructions)}
            className="p-2 bg-slate-800 hover:bg-slate-700 text-slate-400 hover:text-slate-200 rounded-lg transition-colors"
            title="Wskazówki instalacji"
          >
            <HelpCircle className="w-4 h-4" />
          </button>

          <button
            onClick={() => setDismissed(true)}
            className="p-2 bg-slate-800 hover:bg-slate-700 text-slate-400 hover:text-slate-200 rounded-lg transition-colors"
            title="Zamknij baner"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      </div>

      {showInstructions && (
        <div className="mt-4 pt-4 border-t border-slate-700/60 text-xs text-slate-300 grid grid-cols-1 md:grid-cols-3 gap-3">
          <div className="bg-slate-950/60 p-3 rounded-lg border border-slate-800">
            <span className="font-bold text-blue-400 block mb-1">🤖 Android (Chrome)</span>
            Kliknij menu <code className="bg-slate-800 px-1 rounded">⋮</code> (prawy górny róg) &rarr; wybierz <strong>Dodaj do ekranu głównego / Zainstaluj aplikację</strong>.
          </div>
          <div className="bg-slate-950/60 p-3 rounded-lg border border-slate-800">
            <span className="font-bold text-blue-400 block mb-1">🍎 iPhone / iPad (Safari)</span>
            Dotknij ikony <code className="bg-slate-800 px-1 rounded">📤 Udostępnij</code> &rarr; wybierz <strong>Dodaj do ekranu początkowego</strong>.
          </div>
          <div className="bg-slate-950/60 p-3 rounded-lg border border-slate-800">
            <span className="font-bold text-blue-400 block mb-1">💻 Komputer (Chrome / Edge)</span>
            Kliknij ikonę <code className="bg-slate-800 px-1 rounded">⊕</code> po prawej stronie paska adresu URL.
          </div>
        </div>
      )}
    </div>
  );
}
