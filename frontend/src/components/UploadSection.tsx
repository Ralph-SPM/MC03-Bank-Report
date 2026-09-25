import React, { useState, useRef } from 'react';
import { UploadCloud, Calendar, Filter, FileSpreadsheet, Loader2 } from 'lucide-react';

interface UploadSectionProps {
  mode: 'processor' | 'lab';
  onProcess: (file: File, dateFrom: string, dateTo: string) => Promise<void>;
  isLoading: boolean;
}

export const UploadSection: React.FC<UploadSectionProps> = ({ mode, onProcess, isLoading }) => {
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      setSelectedFile(e.target.files[0]);
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      setSelectedFile(e.dataTransfer.files[0]);
    }
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedFile) return;
    onProcess(selectedFile, dateFrom, dateTo);
  };

  return (
    <div className="bg-white rounded-xl border border-slate-200 shadow-xs p-5">
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 mb-4 pb-4 border-b border-slate-100">
        <div>
          <div className="flex items-center space-x-2">
            <span className={`p-1.5 rounded-lg flex items-center justify-center ${
              mode === 'processor'
                ? 'bg-emerald-50 text-emerald-600 border border-emerald-200'
                : 'bg-indigo-50 text-indigo-600 border border-indigo-200'
            }`}>
              <FileSpreadsheet className="w-5 h-5" />
            </span>
            <h2 className="text-lg font-bold text-slate-900 tracking-tight">
              {mode === 'processor' ? 'Remark Processor (Real Production Mode)' : 'Remark Intelligence Lab (Test Mode)'}
            </h2>
          </div>
          <p className="text-xs text-slate-500 mt-1">
            {mode === 'processor'
              ? 'Real-world field remarks processor. Extracts Concat column, formats bank-ready summaries, and provides editable CSU & RFD tools.'
              : 'Pure evaluation and prompt tuning lab. Benchmarks model accuracy against your manual entries and highlights discrepancies.'}
          </p>
        </div>

        <div className="flex items-center gap-2">
          <span className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold ${
            mode === 'processor'
              ? 'bg-emerald-50 text-emerald-800 border border-emerald-200'
              : 'bg-amber-50 text-amber-800 border border-amber-200'
          }`}>
            <span className={`w-2 h-2 rounded-full ${mode === 'processor' ? 'bg-emerald-500' : 'bg-amber-500'}`}></span>
            Source: {mode === 'processor' ? 'FINAL REMARKS' : 'MESSAGE (Test Mode)'}
          </span>
        </div>
      </div>

      <form onSubmit={handleSubmit} className="space-y-4">
        {/* Drag and Drop Zone */}
        <div
          onDragOver={(e) => e.preventDefault()}
          onDrop={handleDrop}
          onClick={() => fileInputRef.current?.click()}
          className="border-2 border-dashed border-slate-200 hover:border-indigo-400 bg-slate-50 hover:bg-indigo-50/20 rounded-xl p-6 text-center cursor-pointer transition-colors flex flex-col items-center justify-center"
        >
          <input
            type="file"
            ref={fileInputRef}
            onChange={handleFileChange}
            accept=".xlsx,.xls,.csv"
            className="hidden"
          />
          <UploadCloud className="w-9 h-9 text-slate-400 mb-2" />
          {selectedFile ? (
            <div className="font-mono text-sm text-slate-800 font-semibold flex items-center space-x-2">
              <FileSpreadsheet className="w-4 h-4 text-emerald-600" />
              <span>{selectedFile.name}</span>
              <span className="text-xs text-slate-400 font-normal">
                ({(selectedFile.size / 1024).toFixed(1)} KB)
              </span>
            </div>
          ) : (
            <div>
              <p className="text-xs text-slate-700 font-medium">
                Click or drag & drop RCBC <span className="font-mono font-semibold">FIELD RSULT</span> workbook (.xlsx, .xls) or test .csv
              </p>
              <p className="text-[11px] text-slate-400 mt-1">Automatic alias column matching &amp; Concat column detection</p>
            </div>
          )}
        </div>

        {/* Filter Controls Row */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 pt-1">
          {/* Date From */}
          <div>
            <label className="block text-[11px] font-semibold text-slate-600 uppercase tracking-wider mb-1">
              Date Filter (From)
            </label>
            <div className="relative">
              <Calendar className="w-4 h-4 text-slate-400 absolute left-2.5 top-2.5 pointer-events-none" />
              <input
                type="date"
                value={dateFrom}
                onChange={(e) => setDateFrom(e.target.value)}
                className="w-full pl-8 pr-3 py-1.5 text-xs bg-slate-50 border border-slate-200 rounded-lg focus:outline-hidden focus:ring-1 focus:ring-indigo-500 focus:bg-white font-mono"
              />
            </div>
          </div>

          {/* Date To */}
          <div>
            <label className="block text-[11px] font-semibold text-slate-600 uppercase tracking-wider mb-1">
              Date Filter (To)
            </label>
            <div className="relative">
              <Calendar className="w-4 h-4 text-slate-400 absolute left-2.5 top-2.5 pointer-events-none" />
              <input
                type="date"
                value={dateTo}
                onChange={(e) => setDateTo(e.target.value)}
                className="w-full pl-8 pr-3 py-1.5 text-xs bg-slate-50 border border-slate-200 rounded-lg focus:outline-hidden focus:ring-1 focus:ring-indigo-500 focus:bg-white font-mono"
              />
            </div>
          </div>
        </div>

        {/* Submit Button */}
        <div className="flex justify-end pt-2">
          <button
            type="submit"
            disabled={!selectedFile || isLoading}
            className={`inline-flex items-center space-x-2 px-5 py-2 rounded-lg text-xs font-bold text-white transition-all shadow-sm ${
              !selectedFile || isLoading
                ? 'bg-slate-300 cursor-not-allowed'
                : mode === 'processor'
                ? 'bg-emerald-600 hover:bg-emerald-700 active:bg-emerald-800'
                : 'bg-indigo-600 hover:bg-indigo-700 active:bg-indigo-800'
            }`}
          >
            {isLoading ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                <span>Processing Rows with AI...</span>
              </>
            ) : (
              <>
                <Filter className="w-4 h-4" />
                <span>{mode === 'processor' ? 'Process Field Remarks' : 'Analyze & Benchmark'}</span>
              </>
            )}
          </button>
        </div>
      </form>
    </div>
  );
};
