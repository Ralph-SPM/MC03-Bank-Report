import React from 'react';
import { Zap, Beaker, Settings } from 'lucide-react';

interface NavbarProps {
  activeTab: 'processor' | 'lab';
  setActiveTab: (tab: 'processor' | 'lab') => void;
  modelTagalog?: string;
  modelEnglish?: string;
  activeProfileName?: string;
  onOpenSettings?: () => void;
}

export const Navbar: React.FC<NavbarProps> = ({
  activeTab,
  setActiveTab,
  modelTagalog = 'minimax-m2.5',
  modelEnglish = 'minimax-m2.5',
  activeProfileName = 'Default RCBC',
  onOpenSettings,
}) => {
  return (
    <header className="bg-slate-900 border-b border-slate-800 text-white sticky top-0 z-40 shadow-sm">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-14 flex items-center justify-between gap-4">
        {/* Left: Brand & Campaign */}
        <div className="flex items-center space-x-3 shrink-0">
          <div className="flex items-center space-x-2">
            <div className="w-7 h-7 rounded bg-indigo-600 flex items-center justify-center font-mono font-bold text-white text-xs shadow-inner">
              M3
            </div>
            <span className="font-bold tracking-tight text-slate-100 text-sm whitespace-nowrap">MC03 DA Portal</span>
          </div>
          <span className="hidden sm:inline-flex items-center px-2 py-0.5 rounded text-[11px] font-medium bg-slate-800 text-indigo-300 border border-slate-700 font-mono whitespace-nowrap">
            RCBC Auto Loan
          </span>
        </div>

        {/* Center: Dedicated Mode Switcher Tabs */}
        <div className="flex items-center space-x-1.5 bg-slate-800/80 p-1 rounded-lg border border-slate-700/80 shrink-0">
          <button
            onClick={() => setActiveTab('processor')}
            className={`flex items-center space-x-1.5 px-3 py-1.5 rounded-md text-xs font-semibold transition-all cursor-pointer whitespace-nowrap ${
              activeTab === 'processor'
                ? 'bg-emerald-600 text-white shadow-sm'
                : 'text-slate-300 hover:text-white hover:bg-slate-700/50'
            }`}
          >
            <Zap className="w-3.5 h-3.5" />
            <span>Remark Processor</span>
            <span className="text-[10px] px-1 py-0.2 rounded bg-black/20 text-white/90 uppercase font-mono tracking-wider ml-1">Real</span>
          </button>

          <button
            onClick={() => setActiveTab('lab')}
            className={`flex items-center space-x-1.5 px-3 py-1.5 rounded-md text-xs font-semibold transition-all cursor-pointer whitespace-nowrap ${
              activeTab === 'lab'
                ? 'bg-indigo-600 text-white shadow-sm'
                : 'text-slate-300 hover:text-white hover:bg-slate-700/50'
            }`}
          >
            <Beaker className="w-3.5 h-3.5" />
            <span>Remark Lab</span>
            <span className="text-[10px] px-1 py-0.2 rounded bg-black/20 text-white/90 uppercase font-mono tracking-wider ml-1">Test</span>
          </button>
        </div>

        {/* Right: Static Current Setting Display (NOT Clickable) & Settings Action Button */}
        <div className="flex items-center space-x-3 shrink-0">
          {/* Static Current Setting Display */}
          <div
            className="hidden lg:flex items-center space-x-2 px-3 py-1.5 rounded-lg bg-slate-800/90 border border-slate-700/80 text-xs text-slate-300 whitespace-nowrap select-none shrink-0"
            title="Current Active Settings and Models"
          >
            <span className="w-2 h-2 rounded-full bg-emerald-400 shrink-0"></span>
            <span className="text-slate-400 font-sans text-[11px] uppercase tracking-wider">Setting:</span>
            <span className="font-semibold text-slate-200 font-sans">{activeProfileName}</span>
            <span className="text-slate-600">|</span>
            {modelTagalog === modelEnglish ? (
              <span className="font-mono text-indigo-300 font-medium">{modelTagalog}</span>
            ) : (
              <span className="font-mono text-indigo-300 font-medium text-[11px]">
                TL: {modelTagalog} / EN: {modelEnglish}
              </span>
            )}
          </div>

          {/* Dedicated Settings Button */}
          <button
            onClick={onOpenSettings}
            className="flex items-center space-x-1.5 px-3 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white font-sans font-medium text-xs shadow-xs transition-colors shrink-0 cursor-pointer"
            title="Configure AI Models & Prompt Profiles"
          >
            <Settings className="w-3.5 h-3.5" />
            <span>Settings</span>
          </button>

          <a
            href="/"
            className="hidden xl:inline-flex items-center text-slate-400 hover:text-slate-200 text-xs font-sans transition-colors whitespace-nowrap shrink-0 pl-1"
            title="Return to MC03 Multi-Step Pipeline"
          >
            Pipeline &rarr;
          </a>
        </div>
      </div>
    </header>
  );
};

