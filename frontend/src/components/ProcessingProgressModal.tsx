import React from 'react';
import {
  Loader2,
  CheckCircle2,
  Clock,
  Hourglass,
  Sparkles,
  FileSpreadsheet,
  Languages,
  ShieldCheck,
  Cpu,
  X,
} from 'lucide-react';

export type ProcessingStage =
  | 'parsing'
  | 'preprocessing'
  | 'routing'
  | 'ai_inference'
  | 'finalizing'
  | 'complete';

export interface ProcessingProgressProps {
  isOpen: boolean;
  mode: 'processor' | 'lab';
  percent: number;
  stage: ProcessingStage;
  statusMessage: string;
  completedRows: number;
  totalRows: number;
  elapsedSeconds: number;
  estimatedSecondsRemaining: number | null;
  modelEnglish?: string;
  modelTagalog?: string;
  onCancel?: () => void;
}

export const ProcessingProgressModal: React.FC<ProcessingProgressProps> = ({
  isOpen,
  mode,
  percent,
  stage,
  statusMessage,
  completedRows,
  totalRows,
  elapsedSeconds,
  estimatedSecondsRemaining,
  modelEnglish = 'glm-5',
  modelTagalog = 'minimax-m2.5',
  onCancel,
}) => {
  if (!isOpen) return null;

  const formatTime = (secs: number) => {
    const mins = Math.floor(secs / 60);
    const rem = Math.floor(secs % 60);
    return `${mins.toString().padStart(2, '0')}:${rem.toString().padStart(2, '0')}s`;
  };

  const steps = [
    {
      id: 'parsing',
      label: 'Read & Parse',
      desc: 'Workbook & Concat extraction',
      icon: FileSpreadsheet,
      active: stage === 'parsing' || stage === 'preprocessing',
      done: ['routing', 'ai_inference', 'finalizing', 'complete'].includes(stage),
    },
    {
      id: 'routing',
      label: 'Language Routing',
      desc: 'Lingua Native Rust Engine',
      icon: Languages,
      active: stage === 'routing',
      done: ['ai_inference', 'finalizing', 'complete'].includes(stage),
    },
    {
      id: 'ai_inference',
      label: '2-Tier LLM Inference',
      desc: `${modelEnglish} & ${modelTagalog}`,
      icon: Cpu,
      active: stage === 'ai_inference',
      done: ['finalizing', 'complete'].includes(stage),
    },
    {
      id: 'finalizing',
      label: 'Taxonomy & Finalizing',
      desc: 'Reconciliation & 180-char enforcement',
      icon: ShieldCheck,
      active: stage === 'finalizing',
      done: stage === 'complete',
    },
  ];

  return (
    <div className="fixed inset-0 z-50 overflow-y-auto bg-slate-950/75 backdrop-blur-md flex items-center justify-center p-4">
      <div className="bg-white rounded-2xl border border-slate-200/80 shadow-2xl max-w-xl w-full overflow-hidden animate-in fade-in zoom-in-95 duration-200">
        {/* Top Accent Gradient Line */}
        <div className="h-1.5 w-full bg-gradient-to-r from-indigo-500 via-purple-500 to-emerald-500 animate-pulse" />

        {/* Modal Header */}
        <div className="p-6 pb-4 flex items-center justify-between border-b border-slate-100">
          <div className="flex items-center space-x-3">
            <div className="w-10 h-10 rounded-xl bg-indigo-50 border border-indigo-100 flex items-center justify-center text-indigo-600 shadow-xs">
              <Sparkles className="w-5 h-5 animate-spin-slow" />
            </div>
            <div>
              <div className="flex items-center space-x-2">
                <h3 className="font-bold text-slate-900 text-base tracking-tight">
                  {mode === 'processor' ? 'Processing Field Remarks' : 'Running Remark Lab Benchmark'}
                </h3>
                <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-bold bg-indigo-100 text-indigo-700 animate-pulse font-mono">
                  ACTIVE
                </span>
              </div>
              <p className="text-xs text-slate-500 mt-0.5">
                Automated 2-Tier AI inference with strict RCBC operational classification.
              </p>
            </div>
          </div>

          {onCancel && (
            <button
              onClick={onCancel}
              className="text-slate-400 hover:text-slate-600 p-1.5 rounded-lg hover:bg-slate-100 transition-colors"
              title="Cancel processing"
            >
              <X className="w-5 h-5" />
            </button>
          )}
        </div>

        <div className="p-6 space-y-6">
          {/* 4-Stage Stepper Bar */}
          <div className="grid grid-cols-4 gap-2">
            {steps.map((st) => {
              const Icon = st.icon;
              return (
                <div
                  key={st.id}
                  className={`flex flex-col items-center text-center p-2 rounded-xl border transition-all ${
                    st.done
                      ? 'bg-emerald-50/70 border-emerald-200 text-emerald-800'
                      : st.active
                      ? 'bg-indigo-50 border-indigo-300 text-indigo-900 ring-2 ring-indigo-200'
                      : 'bg-slate-50 border-slate-200 text-slate-400'
                  }`}
                >
                  <div
                    className={`w-7 h-7 rounded-full flex items-center justify-center mb-1 text-xs font-bold transition-all ${
                      st.done
                        ? 'bg-emerald-600 text-white'
                        : st.active
                        ? 'bg-indigo-600 text-white animate-pulse'
                        : 'bg-slate-200 text-slate-600'
                    }`}
                  >
                    {st.done ? (
                      <CheckCircle2 className="w-4 h-4 text-white" />
                    ) : st.active ? (
                      <Loader2 className="w-3.5 h-3.5 animate-spin text-white" />
                    ) : (
                      <Icon className="w-3.5 h-3.5 text-slate-500" />
                    )}
                  </div>
                  <span className="text-[11px] font-bold leading-tight truncate w-full">
                    {st.label}
                  </span>
                </div>
              );
            })}
          </div>

          {/* Main Progress Bar & Counter Card */}
          <div className="bg-slate-50 p-4 rounded-xl border border-slate-200 space-y-3">
            <div className="flex items-center justify-between">
              <div>
                <span className="text-2xl font-black text-slate-900 font-mono tracking-tight">
                  {Math.round(percent)}%
                </span>
                <span className="text-xs text-slate-500 font-medium ml-2">
                  {totalRows > 0 ? `(${completedRows} of ${totalRows} remarks completed)` : 'Initializing...'}
                </span>
              </div>
              <span className="text-xs font-semibold px-2.5 py-1 rounded-md bg-white border border-slate-200 text-slate-700 shadow-2xs font-mono">
                {stage === 'ai_inference'
                  ? 'Batch LLM Inference'
                  : stage === 'routing'
                  ? 'Language Routing'
                  : stage === 'finalizing'
                  ? 'Assembling Matrix'
                  : 'Preparing'}
              </span>
            </div>

            {/* Visual Animated Progress Bar */}
            <div className="h-3 w-full bg-slate-200 rounded-full overflow-hidden p-0.5">
              <div
                className="h-full bg-gradient-to-r from-indigo-600 via-purple-600 to-emerald-500 rounded-full transition-all duration-300 shadow-inner"
                style={{ width: `${Math.max(4, Math.min(100, percent))}%` }}
              />
            </div>

            {/* Current Activity Message */}
            <div className="flex items-start space-x-2 pt-1 text-xs text-slate-700 font-medium">
              <Loader2 className="w-4 h-4 text-indigo-600 animate-spin shrink-0 mt-0.5" />
              <p className="line-clamp-2 leading-relaxed font-mono text-[11px]">
                {statusMessage || 'Analyzing narrative remarks and determining contact status...'}
              </p>
            </div>
          </div>

          {/* Timing & Engine Indicators Grid */}
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 text-xs">
            {/* Elapsed Time */}
            <div className="bg-slate-50 p-3 rounded-xl border border-slate-200 flex items-center space-x-2.5">
              <div className="p-2 rounded-lg bg-blue-50 text-blue-600 border border-blue-100">
                <Clock className="w-4 h-4" />
              </div>
              <div>
                <span className="block text-[10px] uppercase font-bold text-slate-500 tracking-wider">
                  Elapsed Time
                </span>
                <span className="font-mono font-bold text-slate-900 text-sm">
                  {formatTime(elapsedSeconds)}
                </span>
              </div>
            </div>

            {/* Estimated Remaining */}
            <div className="bg-slate-50 p-3 rounded-xl border border-slate-200 flex items-center space-x-2.5">
              <div className="p-2 rounded-lg bg-amber-50 text-amber-600 border border-amber-100">
                <Hourglass className="w-4 h-4" />
              </div>
              <div>
                <span className="block text-[10px] uppercase font-bold text-slate-500 tracking-wider">
                  Estimated Left
                </span>
                <span className="font-mono font-bold text-slate-900 text-sm">
                  {estimatedSecondsRemaining !== null && estimatedSecondsRemaining > 0
                    ? `~${Math.ceil(estimatedSecondsRemaining)}s`
                    : percent >= 95
                    ? 'Almost done...'
                    : 'Calculating...'}
                </span>
              </div>
            </div>

            {/* Active Models */}
            <div className="col-span-2 sm:col-span-1 bg-slate-50 p-3 rounded-xl border border-slate-200 flex items-center space-x-2.5">
              <div className="p-2 rounded-lg bg-emerald-50 text-emerald-600 border border-emerald-100 shrink-0">
                <Cpu className="w-4 h-4" />
              </div>
              <div className="min-w-0 flex-1">
                <span className="block text-[10px] uppercase font-bold text-slate-500 tracking-wider">
                  Active Models
                </span>
                <div className="font-mono text-[11px] leading-tight text-slate-800 space-y-0.5 mt-0.5">
                  <div className="truncate" title={`English Model: ${modelEnglish}`}>
                    <span className="text-indigo-600 font-bold">EN:</span> {modelEnglish}
                  </div>
                  <div className="truncate" title={`Tagalog Model: ${modelTagalog}`}>
                    <span className="text-purple-600 font-bold">TL:</span> {modelTagalog}
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Modal Footer Note */}
        <div className="px-6 py-3.5 bg-slate-50 border-t border-slate-200 flex items-center justify-between text-[11px] text-slate-500">
          <div className="flex items-center space-x-2 truncate">
            <span>Concurrency: <strong>20 workers</strong></span>
            <span className="text-slate-300">•</span>
            <span className="truncate">
              Routes: <strong className="font-mono text-indigo-700">{modelEnglish}</strong> (EN) / <strong className="font-mono text-purple-700">{modelTagalog}</strong> (TL)
            </span>
          </div>
          {onCancel && (
            <button
              onClick={onCancel}
              className="text-xs font-semibold text-slate-600 hover:text-red-600 transition-colors ml-3 shrink-0"
            >
              Abort
            </button>
          )}
        </div>
      </div>
    </div>
  );
};
