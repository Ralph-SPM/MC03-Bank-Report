import React, { useState, useMemo } from 'react';
import {
  Download,
  Search,
  Copy,
  Check,
  AlertTriangle,
  Sparkles,
  UserCheck,
  Users,
  UserX,
  FileSpreadsheet,
  Clock,
  Filter,
  Edit3,
} from 'lucide-react';
import type { RowItem, Taxonomies } from '../types';

export type FilterCategory = 'all' | 'needs_review' | 'representative' | 'informant' | 'borrower' | 'edited';

interface RemarkProcessorViewProps {
  rows: RowItem[];
  setRows: React.Dispatch<React.SetStateAction<RowItem[]>>;
  taxonomies: Taxonomies | null;
  totalRows?: number;
  detectionLatencyMs?: number;
  englishCount?: number;
  tagalogCount?: number;
  elapsedSeconds?: number;
  modelTagalog?: string;
  modelEnglish?: string;
  onExport: () => void;
  isExporting: boolean;
}

export const RemarkProcessorView: React.FC<RemarkProcessorViewProps> = ({
  rows,
  setRows,
  taxonomies,
  detectionLatencyMs,
  englishCount = 0,
  tagalogCount = 0,
  elapsedSeconds,
  onExport,
  isExporting,
}) => {
  const [activeFilter, setActiveFilter] = useState<FilterCategory>('all');
  const [searchQuery, setSearchQuery] = useState('');
  const [copiedRowIdx, setCopiedRowIdx] = useState<number | null>(null);

  // Update a specific row's field and flag as edited
  const handleUpdateRow = (rowIndex: number, field: keyof RowItem, value: any) => {
    setRows((prev) =>
      prev.map((r) => {
        if (r.row_index === rowIndex) {
          const updated = { ...r, [field]: value, is_edited: true };
          if (field === 'trimmed_statement') {
            updated.final_char_count = String(value).length;
          }
          return updated;
        }
        return r;
      })
    );
  };

  const handleCopy = (text: string, rowIdx: number) => {
    navigator.clipboard.writeText(text);
    setCopiedRowIdx(rowIdx);
    setTimeout(() => setCopiedRowIdx(null), 1800);
  };

  // Compute live counts for each filter category
  const counts = useMemo(() => {
    let needsReview = 0;
    let representative = 0;
    let informant = 0;
    let borrower = 0;
    let edited = 0;

    for (const r of rows) {
      const isReview =
        r.csu_confidence !== 'high' ||
        r.rfd_confidence !== 'high' ||
        (r.csu_alternatives && r.csu_alternatives.length > 0) ||
        (r.rfd_alternatives && r.rfd_alternatives.length > 0);
      if (isReview) needsReview++;

      const cat = r.category_label?.toLowerCase();
      if (cat === 'representative') representative++;
      else if (cat === 'informant') informant++;
      else if (cat === 'borrower') borrower++;

      if (r.is_edited) edited++;
    }

    return {
      all: rows.length,
      needs_review: needsReview,
      representative,
      informant,
      borrower,
      edited,
    };
  }, [rows]);

  // Filter rows by active pill and search query
  const filteredRows = useMemo(() => {
    return rows.filter((r) => {
      // Quick filter pill
      if (activeFilter === 'needs_review') {
        const isReview =
          r.csu_confidence !== 'high' ||
          r.rfd_confidence !== 'high' ||
          (r.csu_alternatives && r.csu_alternatives.length > 0) ||
          (r.rfd_alternatives && r.rfd_alternatives.length > 0);
        if (!isReview) return false;
      } else if (activeFilter === 'representative') {
        if (r.category_label?.toLowerCase() !== 'representative') return false;
      } else if (activeFilter === 'informant') {
        if (r.category_label?.toLowerCase() !== 'informant') return false;
      } else if (activeFilter === 'borrower') {
        if (r.category_label?.toLowerCase() !== 'borrower') return false;
      } else if (activeFilter === 'edited') {
        if (!r.is_edited) return false;
      }

      // Search query
      if (!searchQuery.trim()) return true;
      const q = searchQuery.toLowerCase();
      return (
        r.ch_code?.toLowerCase().includes(q) ||
        r.account_number?.toLowerCase().includes(q) ||
        r.contact_person?.toLowerCase().includes(q) ||
        r.contact_relation?.toLowerCase().includes(q) ||
        r.category_label?.toLowerCase().includes(q) ||
        r.concat_val?.toLowerCase().includes(q) ||
        r.trimmed_statement?.toLowerCase().includes(q) ||
        r.predicted_csu?.toLowerCase().includes(q) ||
        r.predicted_rfd?.toLowerCase().includes(q) ||
        r.csu_reasoning?.toLowerCase().includes(q) ||
        r.rfd_reasoning?.toLowerCase().includes(q)
      );
    });
  }, [rows, activeFilter, searchQuery]);

  return (
    <div className="space-y-3">
      {/* Top Action Bar & Summary Metrics */}
      <div className="bg-white rounded-xl border border-slate-200 shadow-xs p-4 flex flex-col md:flex-row md:items-center justify-between gap-4">
        {/* Left: Summary Metrics & Elapsed Timing */}
        <div className="flex flex-wrap items-center gap-3 text-xs">
          <div className="flex items-center space-x-1.5 px-3 py-1.5 bg-slate-100 rounded-lg font-mono text-slate-700">
            <span className="font-bold text-slate-900">{rows.length}</span>
            <span>records processed</span>
          </div>

          {elapsedSeconds !== undefined && elapsedSeconds > 0 && (
            <div className="flex items-center space-x-1.5 px-2.5 py-1 bg-blue-50 text-blue-700 border border-blue-200 rounded-lg text-[11px] font-mono">
              <Clock className="w-3.5 h-3.5 text-blue-600" />
              <span>
                Elapsed: <strong>{elapsedSeconds < 60 ? `${elapsedSeconds}s` : `${Math.floor(elapsedSeconds / 60)}m ${elapsedSeconds % 60}s`}</strong>
              </span>
            </div>
          )}

          {detectionLatencyMs !== undefined && (
            <div className="flex items-center space-x-1 px-2.5 py-1 bg-purple-50 text-purple-700 border border-purple-200 rounded-lg text-[11px] font-mono">
              <Sparkles className="w-3.5 h-3.5 text-purple-600" />
              <span>Lingua: <strong>{detectionLatencyMs} ms</strong></span>
            </div>
          )}

          <div className="hidden sm:flex items-center space-x-2 text-[11px] font-mono text-slate-500">
            <span>{englishCount} EN</span>
            <span>•</span>
            <span>{tagalogCount} TL</span>
          </div>
        </div>

        {/* Right: Search Input & Export Action */}
        <div className="flex items-center space-x-2">
          <div className="relative w-full md:w-64">
            <Search className="w-4 h-4 text-slate-400 absolute left-2.5 top-2.5 pointer-events-none" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search CH, relation, concat, CSU..."
              className="w-full pl-8 pr-3 py-1.5 text-xs bg-slate-50 border border-slate-200 rounded-lg focus:outline-hidden focus:ring-1 focus:ring-emerald-500 focus:bg-white font-mono"
            />
          </div>

          <button
            onClick={onExport}
            disabled={isExporting || rows.length === 0}
            className="inline-flex items-center space-x-1.5 px-4 py-2 bg-emerald-600 hover:bg-emerald-700 active:bg-emerald-800 disabled:bg-slate-300 text-white rounded-lg text-xs font-bold shadow-xs transition-colors whitespace-nowrap cursor-pointer"
            title="Export all rows with your edits preserved to Excel"
          >
            <Download className="w-4 h-4" />
            <span>{isExporting ? 'Exporting...' : 'Export Bank-Ready Excel'}</span>
          </button>
        </div>
      </div>

      {/* Quick Filter Badges Bar */}
      <div className="flex flex-wrap items-center justify-between gap-2 bg-white rounded-xl border border-slate-200 px-4 py-2.5 shadow-2xs">
        <div className="flex flex-wrap items-center gap-1.5 text-xs">
          <span className="text-[11px] font-bold text-slate-500 uppercase tracking-wider mr-1 flex items-center gap-1">
            <Filter className="w-3.5 h-3.5 text-slate-400" />
            Filters:
          </span>

          {/* All */}
          <button
            onClick={() => setActiveFilter('all')}
            className={`inline-flex items-center space-x-1.5 px-3 py-1.5 rounded-lg font-medium text-xs transition-colors cursor-pointer ${
              activeFilter === 'all'
                ? 'bg-slate-900 text-white shadow-xs'
                : 'bg-slate-100 text-slate-600 hover:bg-slate-200 hover:text-slate-900'
            }`}
          >
            <span>All</span>
            <span
              className={`px-1.5 py-0.5 rounded-full text-[10px] font-mono ${
                activeFilter === 'all' ? 'bg-slate-800 text-slate-200' : 'bg-slate-200 text-slate-700'
              }`}
            >
              {counts.all}
            </span>
          </button>

          {/* Needs Review / Low Confidence */}
          <button
            onClick={() => setActiveFilter('needs_review')}
            className={`inline-flex items-center space-x-1.5 px-3 py-1.5 rounded-lg font-medium text-xs transition-colors cursor-pointer ${
              activeFilter === 'needs_review'
                ? 'bg-amber-600 text-white shadow-xs'
                : 'bg-amber-50 text-amber-800 border border-amber-200/80 hover:bg-amber-100'
            }`}
          >
            <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
            <span>Needs Review</span>
            <span
              className={`px-1.5 py-0.5 rounded-full text-[10px] font-mono font-bold ${
                activeFilter === 'needs_review' ? 'bg-amber-700 text-white' : 'bg-amber-200 text-amber-900'
              }`}
            >
              {counts.needs_review}
            </span>
          </button>

          {/* Representative */}
          <button
            onClick={() => setActiveFilter('representative')}
            className={`inline-flex items-center space-x-1.5 px-3 py-1.5 rounded-lg font-medium text-xs transition-colors cursor-pointer ${
              activeFilter === 'representative'
                ? 'bg-blue-600 text-white shadow-xs'
                : 'bg-blue-50 text-blue-800 border border-blue-200/80 hover:bg-blue-100'
            }`}
          >
            <Users className="w-3.5 h-3.5 shrink-0" />
            <span>Representative</span>
            <span
              className={`px-1.5 py-0.5 rounded-full text-[10px] font-mono font-bold ${
                activeFilter === 'representative' ? 'bg-blue-700 text-white' : 'bg-blue-200 text-blue-900'
              }`}
            >
              {counts.representative}
            </span>
          </button>

          {/* Informant */}
          <button
            onClick={() => setActiveFilter('informant')}
            className={`inline-flex items-center space-x-1.5 px-3 py-1.5 rounded-lg font-medium text-xs transition-colors cursor-pointer ${
              activeFilter === 'informant'
                ? 'bg-purple-600 text-white shadow-xs'
                : 'bg-purple-50 text-purple-800 border border-purple-200/80 hover:bg-purple-100'
            }`}
          >
            <UserX className="w-3.5 h-3.5 shrink-0" />
            <span>Informant</span>
            <span
              className={`px-1.5 py-0.5 rounded-full text-[10px] font-mono font-bold ${
                activeFilter === 'informant' ? 'bg-purple-700 text-white' : 'bg-purple-200 text-purple-900'
              }`}
            >
              {counts.informant}
            </span>
          </button>

          {/* Borrower */}
          <button
            onClick={() => setActiveFilter('borrower')}
            className={`inline-flex items-center space-x-1.5 px-3 py-1.5 rounded-lg font-medium text-xs transition-colors cursor-pointer ${
              activeFilter === 'borrower'
                ? 'bg-emerald-600 text-white shadow-xs'
                : 'bg-emerald-50 text-emerald-800 border border-emerald-200/80 hover:bg-emerald-100'
            }`}
          >
            <UserCheck className="w-3.5 h-3.5 shrink-0" />
            <span>Borrower</span>
            <span
              className={`px-1.5 py-0.5 rounded-full text-[10px] font-mono font-bold ${
                activeFilter === 'borrower' ? 'bg-emerald-700 text-white' : 'bg-emerald-200 text-emerald-900'
              }`}
            >
              {counts.borrower}
            </span>
          </button>

          {/* Edited */}
          <button
            onClick={() => setActiveFilter('edited')}
            className={`inline-flex items-center space-x-1.5 px-3 py-1.5 rounded-lg font-medium text-xs transition-colors cursor-pointer ${
              activeFilter === 'edited'
                ? 'bg-indigo-600 text-white shadow-xs'
                : 'bg-indigo-50 text-indigo-800 border border-indigo-200/80 hover:bg-indigo-100'
            }`}
          >
            <Edit3 className="w-3.5 h-3.5 shrink-0" />
            <span>Edited</span>
            <span
              className={`px-1.5 py-0.5 rounded-full text-[10px] font-mono font-bold ${
                activeFilter === 'edited' ? 'bg-indigo-700 text-white' : 'bg-indigo-200 text-indigo-900'
              }`}
            >
              {counts.edited}
            </span>
          </button>
        </div>

        {/* Filter Count Status & Clear Indicator */}
        <div className="text-[11px] font-medium text-slate-500 flex items-center space-x-2">
          <span>
            Showing <strong className="text-slate-900 font-mono">{filteredRows.length}</strong> of{' '}
            <strong className="text-slate-900 font-mono">{rows.length}</strong> records
            {filteredRows.length !== rows.length && (
              <span className="text-emerald-700 font-normal ml-1">
                (All {rows.length} will be exported)
              </span>
            )}
          </span>
          {(activeFilter !== 'all' || searchQuery) && (
            <button
              onClick={() => {
                setActiveFilter('all');
                setSearchQuery('');
              }}
              className="text-indigo-600 hover:text-indigo-800 font-semibold underline underline-offset-2 ml-1 cursor-pointer"
            >
              Clear filters
            </button>
          )}
        </div>
      </div>

      {/* Focused 5-Column Table */}
      <div className="bg-white rounded-xl border border-slate-200 shadow-xs overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse text-xs">
            <thead>
              <tr className="bg-slate-50/90 border-b border-slate-200 text-[11px] font-semibold text-slate-600 uppercase tracking-wider">
                <th className="py-3 px-3.5 w-32">CH Code</th>
                <th className="py-3 px-3.5 w-56">Relationship</th>
                <th className="py-3 px-3.5 w-44">Concat Column</th>
                <th className="py-3 px-3.5 min-w-[340px]">Remark Summary (Bank-Ready)</th>
                <th className="py-3 px-3.5 min-w-[380px]">Tool's Answer (CSU &amp; RFD)</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-200 font-sans">
              {filteredRows.length === 0 ? (
                <tr>
                  <td colSpan={5} className="py-12 text-center text-slate-400">
                    <FileSpreadsheet className="w-8 h-8 mx-auto mb-2 text-slate-300" />
                    <p className="font-medium text-slate-600">No records found</p>
                    <p className="text-xs text-slate-400 mt-1">Try adjusting your search criteria or upload a new workbook</p>
                  </td>
                </tr>
              ) : (
                filteredRows.map((r) => {
                  const isOverflow = (r.final_char_count || r.trimmed_statement?.length || 0) > 200;
                  const charCount = r.final_char_count || r.trimmed_statement?.length || 0;

                  return (
                    <tr key={r.row_index} className="hover:bg-slate-50/80 transition-colors">
                      {/* 1. CH Code */}
                      <td className="py-3 px-3.5 align-top font-mono">
                        <div className="font-bold text-slate-900 text-xs">
                          {r.ch_code || <span className="text-slate-400 font-normal">N/A</span>}
                        </div>
                        <div className="text-[10px] text-slate-400 mt-0.5">
                          Row #{r.row_index}
                        </div>
                        {r.account_number && (
                          <div className="text-[10px] text-indigo-600 font-medium mt-0.5">
                            Acct: {r.account_number}
                          </div>
                        )}
                        {r.row_date && (
                          <div className="text-[9px] text-slate-400 mt-0.5">
                            {r.row_date}
                          </div>
                        )}
                      </td>

                      {/* 2. Relationship */}
                      <td className="py-3 px-3.5 align-top">
                        <div className="font-semibold text-slate-800">
                          {r.contact_person || '—'}
                        </div>
                        {r.contact_relation && r.contact_relation.toLowerCase() !== 'none' && (
                          <div className="text-[11px] text-slate-600 italic mt-0.5">
                            "{r.contact_relation}"
                          </div>
                        )}
                        <div className="mt-1.5">
                          {r.category_label === 'Representative' ? (
                            <span className="inline-flex items-center px-2 py-0.5 rounded-md text-[10px] font-semibold bg-indigo-50 text-indigo-700 border border-indigo-200">
                              <Users className="w-3 h-3 mr-1 text-indigo-500" />
                              Representative
                            </span>
                          ) : r.category_label === 'Informant' ? (
                            <span className="inline-flex items-center px-2 py-0.5 rounded-md text-[10px] font-semibold bg-amber-50 text-amber-700 border border-amber-200">
                              <UserX className="w-3 h-3 mr-1 text-amber-500" />
                              Informant
                            </span>
                          ) : r.category_label === 'Borrower' || r.category_label === 'Cardholder' ? (
                            <span className="inline-flex items-center px-2 py-0.5 rounded-md text-[10px] font-semibold bg-emerald-50 text-emerald-700 border border-emerald-200">
                              <UserCheck className="w-3 h-3 mr-1 text-emerald-500" />
                              Borrower
                            </span>
                          ) : (
                            <span className="text-[10px] font-mono text-slate-400">
                              {r.category_label || 'Unclassified'}
                            </span>
                          )}
                        </div>
                      </td>

                      {/* 3. Concat Column */}
                      <td className="py-3 px-3.5 align-top font-mono text-xs">
                        {r.concat_val ? (
                          <div className="bg-slate-100/80 px-2 py-1.5 rounded border border-slate-200 text-slate-700 break-all select-all font-medium text-[11px]">
                            {r.concat_val}
                          </div>
                        ) : (
                          <span className="text-slate-400 italic text-[11px]">Empty / Unassigned</span>
                        )}
                      </td>

                      {/* 4. Remark Summary (Editable) */}
                      <td className="py-3 px-3.5 align-top">
                        <div className="space-y-1.5">
                          <textarea
                            value={r.trimmed_statement || ''}
                            onChange={(e) => handleUpdateRow(r.row_index, 'trimmed_statement', e.target.value)}
                            rows={3}
                            className={`w-full text-xs p-2.5 rounded-lg border focus:outline-hidden focus:ring-1 font-mono transition-colors resize-y leading-relaxed ${
                              isOverflow
                                ? 'bg-rose-50/50 border-rose-300 focus:ring-rose-500 text-rose-900'
                                : 'bg-slate-50 border-slate-200 focus:ring-emerald-500 focus:bg-white text-slate-900'
                            }`}
                            placeholder="Enter bank-compliant remark summary..."
                          />

                          <div className="flex items-center justify-between text-[11px]">
                            <div className="flex items-center space-x-2">
                              <span className={`font-mono font-medium ${isOverflow ? 'text-rose-600 font-bold' : 'text-slate-500'}`}>
                                Len: {charCount}/200
                              </span>
                              {isOverflow && (
                                <span className="inline-flex items-center gap-1 text-[10px] font-bold text-rose-600 bg-rose-100 px-1.5 py-0.2 rounded border border-rose-200">
                                  <AlertTriangle className="w-3 h-3" /> PLS REVISE
                                </span>
                              )}
                              {r.classification_source === 'AI' && (
                                <span className="text-[9px] font-bold text-indigo-700 bg-indigo-50 border border-indigo-200 px-1.5 py-0.2 rounded font-mono">
                                  AI SUMMARIZED
                                </span>
                              )}
                            </div>

                            <button
                              type="button"
                              onClick={() => handleCopy(r.trimmed_statement, r.row_index)}
                              className="text-slate-500 hover:text-emerald-700 flex items-center space-x-1 text-[10px] font-medium transition-colors"
                            >
                              {copiedRowIdx === r.row_index ? (
                                <>
                                  <Check className="w-3 h-3 text-emerald-600" />
                                  <span className="text-emerald-600 font-bold">Copied!</span>
                                </>
                              ) : (
                                <>
                                  <Copy className="w-3 h-3" />
                                  <span>Copy</span>
                                </>
                              )}
                            </button>
                          </div>
                        </div>
                      </td>

                      {/* 5. Tool's Answer (CSU & RFD with Alternatives & Confidence) */}
                      <td className="py-3 px-3.5 align-top">
                        <div className="space-y-2.5">
                          {/* CSU Selector */}
                          <div>
                            <div className="flex items-center justify-between mb-1">
                              <span className="text-[10px] uppercase font-bold text-slate-500 font-mono">
                                Collection Status Update (CSU):
                              </span>
                              {r.csu_confidence && (
                                <span
                                  className={`text-[9px] font-bold uppercase px-1.5 py-0.2 rounded border ${
                                    r.csu_confidence === 'high'
                                      ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
                                      : 'bg-amber-50 text-amber-800 border-amber-200'
                                  }`}
                                >
                                  {r.csu_confidence} confidence
                                </span>
                              )}
                            </div>

                            <select
                              value={r.predicted_csu || ''}
                              onChange={(e) => handleUpdateRow(r.row_index, 'predicted_csu', e.target.value)}
                              className="w-full text-xs py-1.5 px-2 bg-slate-50 border border-slate-200 rounded-lg text-slate-800 font-medium focus:ring-1 focus:ring-emerald-500 focus:bg-white"
                            >
                              <option value="">-- Select CSU --</option>
                              {taxonomies?.csu_options.map((opt) => (
                                <option key={opt} value={opt}>
                                  {opt}
                                </option>
                              ))}
                            </select>
                          </div>

                          {/* RFD Selector */}
                          <div>
                            <div className="flex items-center justify-between mb-1">
                              <span className="text-[10px] uppercase font-bold text-slate-500 font-mono">
                                Reason For Default (RFD):
                              </span>
                              {r.rfd_confidence && (
                                <span
                                  className={`text-[9px] font-bold uppercase px-1.5 py-0.2 rounded border ${
                                    r.rfd_confidence === 'high'
                                      ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
                                      : 'bg-amber-50 text-amber-800 border-amber-200'
                                  }`}
                                >
                                  {r.rfd_confidence} confidence
                                </span>
                              )}
                            </div>

                            <select
                              value={r.predicted_rfd || ''}
                              onChange={(e) => handleUpdateRow(r.row_index, 'predicted_rfd', e.target.value)}
                              className="w-full text-xs py-1.5 px-2 bg-slate-50 border border-slate-200 rounded-lg text-slate-800 font-medium focus:ring-1 focus:ring-emerald-500 focus:bg-white"
                            >
                              <option value="">[Empty / No Information]</option>
                              {taxonomies?.rfd_options.map((opt) => (
                                <option key={opt} value={opt}>
                                  {opt}
                                </option>
                              ))}
                            </select>
                          </div>

                          {/* Why Section & Alternative Switch Pills */}
                          {(r.csu_reasoning || r.rfd_reasoning || (r.csu_alternatives && r.csu_alternatives.length > 0) || (r.rfd_alternatives && r.rfd_alternatives.length > 0)) && (
                            <div className="p-2 rounded-lg bg-slate-50 border border-slate-200 text-[11px] text-slate-600 space-y-1.5">
                              {/* Why Reasonings */}
                              {r.csu_reasoning && (
                                <div className="leading-snug">
                                  <span className="font-bold text-indigo-700 mr-1 uppercase text-[9px] tracking-wider">CSU Why:</span>
                                  <span>{r.csu_reasoning}</span>
                                </div>
                              )}

                              {r.rfd_reasoning && (
                                <div className="leading-snug">
                                  <span className="font-bold text-amber-700 mr-1 uppercase text-[9px] tracking-wider">RFD Why:</span>
                                  <span>{r.rfd_reasoning}</span>
                                </div>
                              )}

                              {/* Alternative Candidates Quick Click Pills */}
                              {((r.csu_alternatives && r.csu_alternatives.length > 0) || (r.rfd_alternatives && r.rfd_alternatives.length > 0)) && (
                                <div className="pt-1 border-t border-slate-200 mt-1">
                                  <div className="text-[10px] font-bold text-slate-500 uppercase tracking-wider mb-1">
                                    Alternative Candidates (Click to select):
                                  </div>
                                  <div className="flex flex-wrap gap-1">
                                    {r.csu_alternatives?.map((alt) => (
                                      <button
                                        key={alt}
                                        type="button"
                                        onClick={() => handleUpdateRow(r.row_index, 'predicted_csu', alt)}
                                        className="inline-flex items-center px-2 py-0.5 rounded text-[10px] font-medium bg-indigo-50 text-indigo-700 border border-indigo-200 hover:bg-indigo-100 transition-colors"
                                        title={`Switch CSU to: ${alt}`}
                                      >
                                        <span className="font-mono mr-1 text-[9px] text-indigo-500">CSU:</span> {alt}
                                      </button>
                                    ))}
                                    {r.rfd_alternatives?.map((alt) => (
                                      <button
                                        key={alt}
                                        type="button"
                                        onClick={() => handleUpdateRow(r.row_index, 'predicted_rfd', alt)}
                                        className="inline-flex items-center px-2 py-0.5 rounded text-[10px] font-medium bg-amber-50 text-amber-800 border border-amber-200 hover:bg-amber-100 transition-colors"
                                        title={`Switch RFD to: ${alt}`}
                                      >
                                        <span className="font-mono mr-1 text-[9px] text-amber-600">RFD:</span> {alt || '[Empty]'}
                                      </button>
                                    ))}
                                  </div>
                                </div>
                              )}
                            </div>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};
