import React, { useState, useMemo } from 'react';
import {
  Table,
  CheckCircle2,
  Scissors,
  FileCheck,
  Languages,
  Search,
  Bot,
  X,
  Clock,
  Download,
} from 'lucide-react';
import type { RowItem, TestResponse } from '../types';

interface RemarkLabViewProps {
  report: TestResponse;
  rows: RowItem[];
  setRows: React.Dispatch<React.SetStateAction<RowItem[]>>;
  elapsedSeconds?: number;
  onExport?: () => void;
  isExporting?: boolean;
  onShowToast: (msg: string) => void;
}

export const RemarkLabView: React.FC<RemarkLabViewProps> = ({
  report,
  rows,
  setRows,
  elapsedSeconds,
  onExport,
  isExporting = false,
  onShowToast,
}) => {
  const [filterCategory, setFilterCategory] = useState<string>('all');
  const [searchQuery, setSearchQuery] = useState<string>('');
  const [isFinetuneModalOpen, setIsFinetuneModalOpen] = useState(false);
  const [isSubmittingFeedback, setIsSubmittingFeedback] = useState(false);
  const [finetuneResult, setFinetuneResult] = useState<any>(null);

  const handleFeedbackChange = (rowIdx: number, text: string) => {
    setRows((prev) =>
      prev.map((r) => (r.row_index === rowIdx ? { ...r, prompt_feedback: text } : r))
    );
  };

  const rowsWithFeedback = useMemo(() => {
    return rows.filter((r) => r.prompt_feedback && r.prompt_feedback.trim().length > 0);
  }, [rows]);

  // Filtering
  const filteredRows = useMemo(() => {
    return rows.filter((r) => {
      // Category filter
      if (filterCategory === 'discrepancy' && !r.discrepancy_flag) return false;
      if (filterCategory === 'english' && r.language_route !== 'ENGLISH') return false;
      if (filterCategory === 'tagalog' && r.language_route !== 'TAGALOG') return false;
      if (filterCategory === 'overflow' && (r.final_char_count <= 200 && r.original_char_count <= 200)) return false;
      if (filterCategory === 'representative' && r.category_label?.toLowerCase() !== 'representative') return false;
      if (filterCategory === 'informant' && r.category_label?.toLowerCase() !== 'informant') return false;
      if (filterCategory === 'sanitized' && (!r.prohibited_tags_stripped || r.prohibited_tags_stripped.length === 0)) return false;

      // Text search
      if (!searchQuery.trim()) return true;
      const q = searchQuery.toLowerCase();
      return (
        r.account_number?.toLowerCase().includes(q) ||
        r.contact_person?.toLowerCase().includes(q) ||
        r.contact_relation?.toLowerCase().includes(q) ||
        r.raw_remarks?.toLowerCase().includes(q) ||
        r.trimmed_statement?.toLowerCase().includes(q) ||
        r.predicted_csu?.toLowerCase().includes(q) ||
        r.predicted_rfd?.toLowerCase().includes(q) ||
        r.manual_csu?.toLowerCase().includes(q) ||
        r.manual_rfd?.toLowerCase().includes(q)
      );
    });
  }, [rows, filterCategory, searchQuery]);

  const handleSendFeedback = async () => {
    if (rowsWithFeedback.length === 0) return;
    setIsSubmittingFeedback(true);
    try {
      const items = rowsWithFeedback.map((r) => ({
        row_index: r.row_index,
        account_number: r.account_number,
        raw_remarks: r.raw_remarks,
        cleaned_remarks: r.trimmed_statement,
        feedback: r.prompt_feedback,
      }));

      const res = await fetch('/remarks-lab/finetune-prompt', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ items }),
      });
      const data = await res.json();
      setFinetuneResult(data);
    } catch (err: any) {
      alert('Failed to analyze feedback: ' + err.message);
    } finally {
      setIsSubmittingFeedback(false);
    }
  };

  const handleApplyRules = async () => {
    if (!finetuneResult?.rules) return;
    try {
      const res = await fetch('/remarks-lab/apply-rules', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(finetuneResult.rules),
      });
      if (res.ok) {
        onShowToast('Fine-tuned prompt rules applied to active models!');
        setIsFinetuneModalOpen(false);
      }
    } catch (err: any) {
      alert('Failed to apply rules: ' + err.message);
    }
  };

  return (
    <div className="space-y-4">
      {/* 1. Benchmark KPI Summary Cards */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-7 gap-3">
        {/* Rows Analyzed */}
        <div className="bg-white p-4 rounded-xl border border-slate-200 shadow-xs flex flex-col justify-between">
          <div className="flex items-center justify-between text-slate-500 text-xs">
            <span>Rows Analyzed</span>
            <Table className="w-4 h-4 text-slate-400" />
          </div>
          <div className="mt-2 flex items-baseline space-x-1.5">
            <span className="text-2xl font-bold font-mono text-slate-900">{report.total_rows}</span>
            <span className="text-[11px] text-slate-500">records</span>
          </div>
          <div className="mt-2 text-[10px] text-slate-500">
            {report.rows_with_ground_truth} with manual ground truth
          </div>
        </div>

        {/* Prohibited Tags Stripped */}
        <div className="bg-white p-4 rounded-xl border border-slate-200 shadow-xs flex flex-col justify-between">
          <div className="flex items-center justify-between text-slate-500 text-xs">
            <span>Tags Sanitized</span>
            <Scissors className="w-4 h-4 text-amber-500" />
          </div>
          <div className="mt-2 flex items-baseline space-x-1.5">
            <span className="text-2xl font-bold font-mono text-amber-600">{report.sanitized_tag_count}</span>
            <span className="text-[11px] text-amber-600 font-medium">stripped</span>
          </div>
          <div className="mt-2 text-[10px] text-slate-500">
            BKAL, L3, INB, OBD, Phone, Email
          </div>
        </div>

        {/* CSU Agreement */}
        <div className="bg-white p-4 rounded-xl border border-slate-200 shadow-xs flex flex-col justify-between">
          <div className="flex items-center justify-between text-slate-500 text-xs">
            <span>CSU Agreement</span>
            <CheckCircle2 className="w-4 h-4 text-indigo-500" />
          </div>
          <div className="mt-2 flex items-baseline space-x-1.5">
            <span className={`text-2xl font-bold font-mono ${
              report.csu_accuracy_pct >= 85 ? 'text-emerald-600' : 'text-amber-600'
            }`}>
              {report.csu_accuracy_pct}%
            </span>
            <span className="text-[11px] text-slate-500">agreement</span>
          </div>
          <div className="mt-2 text-[10px] text-slate-500">
            Agreement with manual entry
          </div>
        </div>

        {/* RFD Agreement */}
        <div className="bg-white p-4 rounded-xl border border-slate-200 shadow-xs flex flex-col justify-between">
          <div className="flex items-center justify-between text-slate-500 text-xs">
            <span>RFD Agreement</span>
            <FileCheck className="w-4 h-4 text-emerald-500" />
          </div>
          <div className="mt-2 flex items-baseline space-x-1.5">
            <span className={`text-2xl font-bold font-mono ${
              report.rfd_accuracy_pct >= 85 ? 'text-emerald-600' : 'text-amber-600'
            }`}>
              {report.rfd_accuracy_pct}%
            </span>
            <span className="text-[11px] text-slate-500">agreement</span>
          </div>
          <div className="mt-2 text-[10px] text-slate-500">
            Agreement with manual entry
          </div>
        </div>

        {/* 200-Char Truncations */}
        <div className="bg-white p-4 rounded-xl border border-slate-200 shadow-xs flex flex-col justify-between">
          <div className="flex items-center justify-between text-slate-500 text-xs">
            <span>Overflow Prevented</span>
            <Scissors className="w-4 h-4 text-blue-500" />
          </div>
          <div className="mt-2 flex items-baseline space-x-1.5">
            <span className="text-2xl font-bold font-mono text-blue-600">{report.char_overflow_prevented_count}</span>
            <span className="text-[11px] text-slate-500">rows</span>
          </div>
          <div className="mt-2 text-[10px] text-slate-500">
            AI: <strong>{report.ai_summarized_count}</strong> • Fallback: <strong>{report.rule_based_fallback_count}</strong>
          </div>
        </div>

        {/* Language Routing */}
        <div className="bg-white p-4 rounded-xl border border-slate-200 shadow-xs flex flex-col justify-between">
          <div className="flex items-center justify-between text-slate-500 text-xs">
            <span>Language Split</span>
            <Languages className="w-4 h-4 text-purple-500" />
          </div>
          <div className="mt-2 flex items-baseline space-x-1.5 font-mono">
            <span className="text-lg font-bold text-purple-700">{report.english_count} <span className="text-xs font-normal text-slate-500">EN</span></span>
            <span className="text-slate-300">|</span>
            <span className="text-lg font-bold text-purple-700">{report.tagalog_count} <span className="text-xs font-normal text-slate-500">TL</span></span>
          </div>
          <div className="mt-2 text-[10px] text-slate-500">
            Lingua detection: <strong className="font-mono text-purple-900">{report.detection_latency_ms} ms</strong>
          </div>
        </div>

        {/* Total Time Elapsed */}
        <div className="bg-white p-4 rounded-xl border border-slate-200 shadow-xs flex flex-col justify-between">
          <div className="flex items-center justify-between text-slate-500 text-xs">
            <span>Time Elapsed</span>
            <Clock className="w-4 h-4 text-cyan-500" />
          </div>
          <div className="mt-2 flex items-baseline space-x-1.5 font-mono">
            <span className="text-2xl font-bold text-cyan-700">
              {elapsedSeconds !== undefined && elapsedSeconds > 0
                ? (elapsedSeconds < 60 ? `${elapsedSeconds}s` : `${Math.floor(elapsedSeconds / 60)}m ${elapsedSeconds % 60}s`)
                : '—'}
            </span>
          </div>
          <div className="mt-2 text-[10px] text-slate-500">
            Total end-to-end processing
          </div>
        </div>
      </div>

      {/* 2. Interactive Filter Bar */}
      <div className="bg-white p-4 rounded-xl border border-slate-200 shadow-xs flex flex-col md:flex-row md:items-center justify-between gap-3">
        {/* Filter Pills */}
        <div className="flex flex-wrap items-center gap-1.5 text-xs">
          {[
            { id: 'all', label: `All (${report.total_rows})` },
            { id: 'discrepancy', label: `Differences (${report.discrepancy_count})` },
            { id: 'english', label: `English (${report.english_count})` },
            { id: 'tagalog', label: `Tagalog/Taglish (${report.tagalog_count})` },
            { id: 'overflow', label: 'Exceeds 200 Chars' },
            { id: 'representative', label: `Reps (${report.representative_count})` },
            { id: 'informant', label: `Informants (${report.informant_count})` },
            { id: 'sanitized', label: `Tags Stripped (${report.sanitized_tag_count})` },
          ].map((pill) => (
            <button
              key={pill.id}
              onClick={() => setFilterCategory(pill.id)}
              className={`px-3 py-1.5 rounded-full font-medium transition-colors ${
                filterCategory === pill.id
                  ? 'bg-slate-900 text-white shadow-xs'
                  : 'bg-slate-100 text-slate-700 hover:bg-slate-200'
              }`}
            >
              {pill.label}
            </button>
          ))}
        </div>

        {/* Search & Fine-Tune Action */}
        <div className="flex items-center space-x-2">
          <div className="relative w-full md:w-56">
            <Search className="w-4 h-4 text-slate-400 absolute left-2.5 top-2.5 pointer-events-none" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search test rows..."
              className="w-full pl-8 pr-3 py-1.5 text-xs bg-slate-50 border border-slate-200 rounded-lg focus:ring-1 focus:ring-indigo-500 font-mono"
            />
          </div>

          <button
            onClick={() => {
              setIsFinetuneModalOpen(true);
              if (rowsWithFeedback.length > 0) handleSendFeedback();
            }}
            className="inline-flex items-center space-x-1.5 px-3 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg text-xs font-semibold shadow-xs transition-colors whitespace-nowrap cursor-pointer"
            title="Send human reviewer feedback to fine-tune AI rules"
          >
            <Bot className="w-4 h-4" />
            <span>Send Feedback ({rowsWithFeedback.length})</span>
          </button>

          {onExport && (
            <button
              onClick={onExport}
              disabled={isExporting || rows.length === 0}
              className="inline-flex items-center space-x-1.5 px-3.5 py-2 bg-emerald-600 hover:bg-emerald-700 active:bg-emerald-800 disabled:bg-slate-300 text-white rounded-lg text-xs font-semibold shadow-xs transition-colors whitespace-nowrap cursor-pointer"
              title="Export evaluated benchmark rows with reasoning and agreement metrics to Excel"
            >
              <Download className="w-4 h-4" />
              <span>{isExporting ? 'Exporting...' : 'Export Evaluated Excel'}</span>
            </button>
          )}
        </div>
      </div>

      {/* 3. Benchmark Table */}
      <div className="bg-white rounded-xl border border-slate-200 shadow-xs overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse text-xs">
            <thead>
              <tr className="bg-slate-50/90 border-b border-slate-200 text-[11px] font-semibold text-slate-600 uppercase tracking-wider">
                <th className="py-3 px-3 w-28">Row &amp; Date</th>
                <th className="py-3 px-3 w-44">Account / Contact</th>
                <th className="py-3 px-3 w-40">Relation Class</th>
                <th className="py-3 px-3 min-w-[300px]">Raw &amp; Cleaned Remarks</th>
                <th className="py-3 px-3 min-w-[320px]">Tool's Answer vs. Your Original Entry</th>
                <th className="py-3 px-3 min-w-[240px]">Feedback for Fine-Tuning LLM Prompt</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-200 font-sans">
              {filteredRows.map((r) => (
                <tr key={r.row_index} className="hover:bg-slate-50/80 transition-colors">
                  {/* 1. Row & Flags */}
                  <td className="py-3 px-3 align-top font-mono text-[11px]">
                    <div className="font-bold text-slate-800">#{r.row_index}</div>
                    {r.row_date && <div className="text-[10px] text-indigo-600 mt-0.5">{r.row_date}</div>}
                    {r.discrepancy_flag && (
                      <span className="inline-flex items-center px-1.5 py-0.2 rounded text-[9px] font-bold bg-amber-100 text-amber-800 border border-amber-200 mt-1">
                        DIFF
                      </span>
                    )}
                  </td>

                  {/* 2. Account & Contact */}
                  <td className="py-3 px-3 align-top">
                    <div className="font-mono font-semibold text-slate-900">{r.account_number || 'N/A'}</div>
                    {r.ch_code && <div className="text-[10px] font-mono text-slate-400">CH: {r.ch_code}</div>}
                    <div className="mt-1 font-medium text-slate-700">{r.contact_person || '—'}</div>
                    {r.contact_relation && <div className="text-[11px] text-slate-500 italic">"{r.contact_relation}"</div>}
                  </td>

                  {/* 3. Relation Class */}
                  <td className="py-3 px-3 align-top">
                    <span className={`inline-flex items-center px-2 py-0.5 rounded text-[10px] font-semibold ${
                      r.category_label === 'Representative'
                        ? 'bg-indigo-50 text-indigo-700 border border-indigo-200'
                        : r.category_label === 'Informant'
                        ? 'bg-amber-50 text-amber-700 border border-amber-200'
                        : 'bg-emerald-50 text-emerald-700 border border-emerald-200'
                    }`}>
                      {r.category_label}
                    </span>
                    {r.normalized_role && (
                      <div className="mt-1 text-[10px] font-mono text-slate-500">
                        Role: <span className="font-semibold text-slate-700">{r.normalized_role}</span>
                      </div>
                    )}
                  </td>

                  {/* 4. Raw & Cleaned */}
                  <td className="py-3 px-3 align-top">
                    <div className="space-y-1.5">
                      <div className="text-[10px] text-slate-600 bg-amber-50/60 p-1.5 rounded border border-amber-200">
                        <span className="font-bold font-mono text-[9px] text-amber-700 uppercase">Test Source (MESSAGE):</span>
                        <div className="line-clamp-2 mt-0.5">{r.raw_remarks}</div>
                      </div>

                      <div className="bg-indigo-50/50 p-2 rounded-lg border border-indigo-100 font-mono text-[11px] text-slate-900">
                        <div className="flex items-center justify-between text-[9px] font-bold text-indigo-700 uppercase mb-1">
                          <span>Bank Cleaned:</span>
                          <span className={r.final_char_count > 200 ? 'text-rose-600' : 'text-slate-600'}>
                            Len: {r.final_char_count}/200
                          </span>
                        </div>
                        <div className="leading-snug">{r.trimmed_statement}</div>
                      </div>
                    </div>
                  </td>

                  {/* 5. Tool vs Entry */}
                  <td className="py-3 px-3 align-top">
                    <div className="space-y-2 text-[11px]">
                      {/* CSU */}
                      <div className="border-b border-slate-100 pb-1.5">
                        <div className="flex items-center justify-between">
                          <span className="text-[10px] font-mono text-slate-400">CSU Tool:</span>
                          <span className="font-semibold text-slate-800">{r.predicted_csu}</span>
                        </div>
                        {r.manual_csu && (
                          <div className={`flex items-center justify-between text-[10px] font-mono mt-0.5 ${
                            r.csu_match ? 'text-emerald-600' : 'text-rose-600 font-bold'
                          }`}>
                            <span>Your entry:</span>
                            <span>{r.manual_csu} {r.csu_match ? '✓' : '✗'}</span>
                          </div>
                        )}
                        {r.csu_reasoning && (
                          <div className="text-[10px] text-slate-500 italic mt-1 bg-slate-50 p-1 rounded">
                            <strong className="text-[9px] text-indigo-700 not-italic uppercase mr-1">Why:</strong>{r.csu_reasoning}
                          </div>
                        )}
                      </div>

                      {/* RFD */}
                      <div>
                        <div className="flex items-center justify-between">
                          <span className="text-[10px] font-mono text-slate-400">RFD Tool:</span>
                          <span className="font-semibold text-slate-800">{r.predicted_rfd || '[Empty]'}</span>
                        </div>
                        {r.manual_rfd && (
                          <div className={`flex items-center justify-between text-[10px] font-mono mt-0.5 ${
                            r.rfd_match ? 'text-emerald-600' : 'text-rose-600 font-bold'
                          }`}>
                            <span>Your entry:</span>
                            <span>{r.manual_rfd} {r.rfd_match ? '✓' : '✗'}</span>
                          </div>
                        )}
                        {r.rfd_reasoning && (
                          <div className="text-[10px] text-slate-500 italic mt-1 bg-slate-50 p-1 rounded">
                            <strong className="text-[9px] text-amber-700 not-italic uppercase mr-1">Why:</strong>{r.rfd_reasoning}
                          </div>
                        )}
                      </div>
                    </div>
                  </td>

                  {/* 6. Feedback for Fine-Tuning */}
                  <td className="py-3 px-3 align-top">
                    <textarea
                      value={r.prompt_feedback || ''}
                      onChange={(e) => handleFeedbackChange(r.row_index, e.target.value)}
                      rows={2}
                      className="w-full text-xs p-2 bg-slate-50 border border-slate-200 rounded-lg focus:ring-1 focus:ring-indigo-500 focus:bg-white resize-y text-slate-800 placeholder:text-slate-400"
                      placeholder="Leave blank (default) or type prompt correction feedback..."
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* 4. Fine-Tune Modal */}
      {isFinetuneModalOpen && (
        <div className="fixed inset-0 z-50 bg-slate-900/60 backdrop-blur-xs flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl border border-slate-200 max-w-2xl w-full p-6 space-y-4 shadow-xl max-h-[90vh] overflow-y-auto">
            <div className="flex items-center justify-between border-b border-slate-100 pb-3">
              <div className="flex items-center space-x-2">
                <Bot className="w-5 h-5 text-indigo-600" />
                <h3 className="font-bold text-slate-900 text-sm">AI Prompt Fine-Tuning &amp; Rule Optimizer</h3>
              </div>
              <button
                onClick={() => setIsFinetuneModalOpen(false)}
                className="text-slate-400 hover:text-slate-600 p-1 rounded-lg"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            {isSubmittingFeedback ? (
              <div className="py-12 text-center text-slate-500">
                <Bot className="w-8 h-8 animate-bounce mx-auto mb-2 text-indigo-600" />
                <p className="font-medium text-xs">Analyzing reviewer feedback patterns with AI...</p>
              </div>
            ) : finetuneResult ? (
              <div className="space-y-4 text-xs">
                <div className="p-3 bg-indigo-50 rounded-xl border border-indigo-200 font-mono text-[11px] text-indigo-950 whitespace-pre-wrap">
                  {finetuneResult.finetuned_prompt_guidance}
                </div>

                <div className="flex justify-end space-x-2 pt-2">
                  <button
                    onClick={handleApplyRules}
                    className="px-4 py-2 bg-emerald-600 hover:bg-emerald-700 text-white rounded-lg font-bold text-xs shadow-xs"
                  >
                    Apply to Active Models
                  </button>
                  <button
                    onClick={() => setIsFinetuneModalOpen(false)}
                    className="px-4 py-2 bg-slate-100 text-slate-700 rounded-lg text-xs font-medium"
                  >
                    Close
                  </button>
                </div>
              </div>
            ) : (
              <div className="py-6 text-center text-slate-500">
                <p className="text-xs">No feedback provided yet. Enter corrections in the "Feedback for Fine-Tuning" column for any rows you want to teach the model.</p>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
};
