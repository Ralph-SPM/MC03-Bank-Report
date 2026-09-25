import React, { useState, useEffect, useRef } from 'react';
import { Navbar } from './components/Navbar';
import { UploadSection } from './components/UploadSection';
import { RemarkProcessorView } from './components/RemarkProcessorView';
import { RemarkLabView } from './components/RemarkLabView';
import { SettingsModal } from './components/SettingsModal';
import { ProcessingProgressModal, type ProcessingStage } from './components/ProcessingProgressModal';
import type { RowItem, Taxonomies, ProcessResponse, TestResponse, PromptProfile, ProfilesData } from './types';
import { CheckCircle2 } from 'lucide-react';

export const App: React.FC = () => {
  const getInitialTab = (): 'processor' | 'lab' => {
    if (typeof window !== 'undefined') {
      if (
        window.location.pathname.includes('lab') ||
        window.location.search.includes('tab=lab') ||
        window.location.hash.includes('lab')
      ) {
        return 'lab';
      }
    }
    return 'processor';
  };

  const [activeTab, setActiveTabState] = useState<'processor' | 'lab'>(getInitialTab);

  const setActiveTab = (tab: 'processor' | 'lab') => {
    setActiveTabState(tab);
    if (typeof window !== 'undefined') {
      const targetPath = tab === 'processor' ? '/remark-processor' : '/remark-processor?tab=lab';
      window.history.pushState({}, '', targetPath);
    }
  };

  const [taxonomies, setTaxonomies] = useState<Taxonomies | null>(null);
  const [activeProfile, setActiveProfile] = useState<PromptProfile | null>(null);
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);

  // Processor state
  const [processorRows, setProcessorRows] = useState<RowItem[]>([]);
  const [processorStats, setProcessorStats] = useState<Partial<ProcessResponse>>({});

  // Lab state
  const [labReport, setLabReport] = useState<TestResponse | null>(null);
  const [labRows, setLabRows] = useState<RowItem[]>([]);

  // Processing Progress Modal state
  const [isProcessingModalOpen, setIsProcessingModalOpen] = useState(false);
  const [processPercent, setProcessPercent] = useState(0);
  const [processStage, setProcessStage] = useState<ProcessingStage>('parsing');
  const [processStatusMessage, setProcessStatusMessage] = useState('');
  const [processCompletedRows, setProcessCompletedRows] = useState(0);
  const [processTotalRows, setProcessTotalRows] = useState(0);
  const [processElapsedSeconds, setProcessElapsedSeconds] = useState(0);
  const [estimatedSecondsRemaining, setEstimatedSecondsRemaining] = useState<number | null>(null);
  const [lastElapsedSeconds, setLastElapsedSeconds] = useState<number | null>(null);
  const abortControllerRef = useRef<AbortController | null>(null);

  // UI state
  const [isLoading, setIsLoading] = useState(false);
  const [isExporting, setIsExporting] = useState(false);
  const [toastMessage, setToastMessage] = useState<string | null>(null);

  const showToast = (msg: string) => {
    setToastMessage(msg);
    setTimeout(() => setToastMessage(null), 3500);
  };

  // Fetch taxonomies and active profile on mount
  useEffect(() => {
    fetch('/api/remarks/taxonomies')
      .then((res) => res.json())
      .then((data) => setTaxonomies(data))
      .catch((err) => console.error('Failed to load taxonomies:', err));

    fetch('/api/remarks/profiles')
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then((data: ProfilesData) => {
        if (data && Array.isArray(data.profiles) && data.profiles.length > 0) {
          const found = data.profiles.find((p) => p.id === data.active_profile_id) || data.profiles[0];
          if (found) {
            setActiveProfile(found);
            setProcessorStats((prev) => ({
              ...prev,
              model_tagalog: found.model_tagalog,
              model_english: found.model_english,
            }));
          }
        }
      })
      .catch((err) => console.error('Failed to load active profile:', err));
  }, []);

  const applyProcessedData = (data: any) => {
    if (activeTab === 'processor') {
      setProcessorRows(data.rows || []);
      setProcessorStats(data);
      showToast(`Successfully processed ${data.total_rows} field remarks!`);
    } else {
      setLabReport(data);
      setLabRows(data.rows || []);
      showToast(`Completed benchmark analysis on ${data.total_rows} test records!`);
    }
  };

  const handleProcessWorkbook = async (
    file: File,
    dateFrom: string,
    dateTo: string
  ) => {
    setIsLoading(true);
    setIsProcessingModalOpen(true);
    setProcessPercent(5);
    setProcessStage('parsing');
    setProcessStatusMessage('Uploading workbook and extracting column headers...');
    setProcessCompletedRows(0);
    setProcessTotalRows(0);
    setProcessElapsedSeconds(0);
    setEstimatedSecondsRemaining(null);

    const abortController = new AbortController();
    abortControllerRef.current = abortController;

    const startTime = Date.now();
    const timerInterval = setInterval(() => {
      const elapsed = Math.floor((Date.now() - startTime) / 1000);
      setProcessElapsedSeconds(elapsed);
    }, 1000);

    const formData = new FormData();
    formData.append('workbook', file);
    if (dateFrom) formData.append('date_from', dateFrom);
    if (dateTo) formData.append('date_to', dateTo);
    formData.append('run_ai', 'true');

    try {
      const endpoint = activeTab === 'processor' ? '/api/remarks/process-stream' : '/api/remarks/test-stream';
      const res = await fetch(endpoint, {
        method: 'POST',
        body: formData,
        signal: abortController.signal,
      });

      if (!res.ok) {
        // Fallback to standard endpoint
        const fallbackEndpoint = activeTab === 'processor' ? '/api/remarks/process' : '/api/remarks/test';
        const fallbackRes = await fetch(fallbackEndpoint, {
          method: 'POST',
          body: formData,
          signal: abortController.signal,
        });
        if (!fallbackRes.ok) {
          const errData = await fallbackRes.json().catch(() => ({}));
          throw new Error(errData.error || `Server returned ${fallbackRes.status}`);
        }
        const data = await fallbackRes.json();
        const finalElapsed = Math.max(1, Math.round((Date.now() - startTime) / 1000));
        setLastElapsedSeconds(finalElapsed);
        applyProcessedData(data);
        setIsProcessingModalOpen(false);
        return;
      }

      // Read SSE stream
      const reader = res.body?.getReader();
      if (!reader) {
        throw new Error('ReadableStream is not supported by your browser.');
      }

      const decoder = new TextDecoder('utf-8');
      let buffer = '';

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        const lines = buffer.split('\n\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed || trimmed.startsWith(':')) continue; // comment / keep-alive

          if (trimmed.startsWith('data: ')) {
            try {
              const payload = JSON.parse(trimmed.slice(6));
              if (payload.type === 'progress') {
                if (payload.stage) setProcessStage(payload.stage);
                if (typeof payload.percent === 'number') setProcessPercent(payload.percent);
                if (payload.message) setProcessStatusMessage(payload.message);
                if (typeof payload.completed === 'number') setProcessCompletedRows(payload.completed);
                if (typeof payload.total === 'number') {
                  setProcessTotalRows(payload.total);
                  const elapsed = (Date.now() - startTime) / 1000;
                  if (payload.completed > 0) {
                    const rate = payload.completed / elapsed;
                    const remaining = payload.total - payload.completed;
                    setEstimatedSecondsRemaining(remaining > 0 ? remaining / rate : 0);
                  }
                }
              } else if (payload.type === 'complete') {
                const finalElapsed = Math.max(1, Math.round((Date.now() - startTime) / 1000));
                setLastElapsedSeconds(finalElapsed);
                setProcessPercent(100);
                setProcessStage('complete');
                setProcessStatusMessage('Processing complete! Rendering results...');
                applyProcessedData(payload.data);
                setTimeout(() => {
                  setIsProcessingModalOpen(false);
                }, 400);
                return;
              } else if (payload.type === 'error') {
                throw new Error(payload.error || 'Server error occurred during processing.');
              }
            } catch (jsonErr: any) {
              if (jsonErr.message && !jsonErr.message.includes('JSON')) {
                throw jsonErr;
              }
            }
          }
        }
      }
    } catch (err: any) {
      if (err.name === 'AbortError') {
        showToast('Processing cancelled by user.');
      } else {
        alert(`Error processing file: ${err.message}`);
      }
      setIsProcessingModalOpen(false);
    } finally {
      clearInterval(timerInterval);
      setIsLoading(false);
      abortControllerRef.current = null;
    }
  };

  const handleExportProcessed = async () => {
    if (processorRows.length === 0) return;
    setIsExporting(true);
    try {
      const res = await fetch('/api/remarks/export-processed', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ rows: processorRows, test_mode: false }),
      });

      if (!res.ok) {
        throw new Error(`Export failed with status ${res.status}`);
      }

      const blob = await res.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `RCBC_FIELD_PROCESSED_${new Date().toISOString().slice(0, 10)}.xlsx`;
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
      document.body.removeChild(a);
      showToast('Bank-ready Excel file exported successfully!');
    } catch (err: any) {
      alert(`Failed to export Excel: ${err.message}`);
    } finally {
      setIsExporting(false);
    }
  };

  const handleExportLab = async () => {
    if (labRows.length === 0) return;
    setIsExporting(true);
    try {
      const res = await fetch('/api/remarks/export-processed', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ rows: labRows, test_mode: true }),
      });

      if (!res.ok) {
        throw new Error(`Export failed with status ${res.status}`);
      }

      const blob = await res.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `RCBC_FIELD_EVALUATED_${new Date().toISOString().slice(0, 10)}.xlsx`;
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
      document.body.removeChild(a);
      showToast('Evaluated benchmark Excel file exported successfully!');
    } catch (err: any) {
      alert(`Failed to export Excel: ${err.message}`);
    } finally {
      setIsExporting(false);
    }
  };

  return (
    <div className="min-h-screen bg-slate-50 text-slate-800 flex flex-col font-sans">
      <Navbar
        activeTab={activeTab}
        setActiveTab={setActiveTab}
        modelTagalog={processorStats.model_tagalog || activeProfile?.model_tagalog}
        modelEnglish={processorStats.model_english || activeProfile?.model_english}
        activeProfileName={activeProfile?.name || 'Default RCBC'}
        onOpenSettings={() => setIsSettingsOpen(true)}
      />

      <main className="flex-1 max-w-7xl w-full mx-auto px-4 sm:px-6 lg:px-8 py-6 space-y-6">
        <UploadSection
          mode={activeTab}
          onProcess={handleProcessWorkbook}
          isLoading={isLoading}
        />

        {activeTab === 'processor' ? (
          processorRows.length > 0 ? (
            <RemarkProcessorView
              rows={processorRows}
              setRows={setProcessorRows}
              taxonomies={taxonomies}
              totalRows={processorStats.total_rows || 0}
              detectionLatencyMs={processorStats.detection_latency_ms}
              englishCount={processorStats.english_count}
              tagalogCount={processorStats.tagalog_count}
              elapsedSeconds={lastElapsedSeconds ?? undefined}
              modelTagalog={processorStats.model_tagalog || activeProfile?.model_tagalog}
              modelEnglish={processorStats.model_english || activeProfile?.model_english}
              onExport={handleExportProcessed}
              isExporting={isExporting}
            />
          ) : null
        ) : (
          labReport && (
            <RemarkLabView
              report={labReport}
              rows={labRows}
              setRows={setLabRows}
              elapsedSeconds={lastElapsedSeconds ?? undefined}
              onExport={handleExportLab}
              isExporting={isExporting}
              onShowToast={showToast}
            />
          )
        )}
      </main>

      {/* Real-Time Processing Progress Modal */}
      <ProcessingProgressModal
        isOpen={isProcessingModalOpen}
        mode={activeTab}
        percent={processPercent}
        stage={processStage}
        statusMessage={processStatusMessage}
        completedRows={processCompletedRows}
        totalRows={processTotalRows}
        elapsedSeconds={processElapsedSeconds}
        estimatedSecondsRemaining={estimatedSecondsRemaining}
        modelEnglish={activeProfile?.model_english || processorStats.model_english || 'glm-5'}
        modelTagalog={activeProfile?.model_tagalog || processorStats.model_tagalog || 'minimax-m2.5'}
        onCancel={() => {
          if (abortControllerRef.current) {
            abortControllerRef.current.abort();
          }
        }}
      />

      {/* Settings Modal */}
      <SettingsModal
        isOpen={isSettingsOpen}
        onClose={() => setIsSettingsOpen(false)}
        onProfileUpdated={(updated) => {
          setActiveProfile(updated);
          setProcessorStats((prev) => ({
            ...prev,
            model_tagalog: updated.model_tagalog,
            model_english: updated.model_english,
          }));
        }}
        showToast={showToast}
      />

      {/* Global Notification Toast */}
      {toastMessage && (
        <div className="fixed bottom-5 right-5 z-50 bg-slate-900 text-white text-xs px-4 py-3 rounded-xl shadow-lg border border-slate-800 flex items-center space-x-2 animate-in fade-in slide-in-from-bottom-3 duration-200">
          <CheckCircle2 className="w-4 h-4 text-emerald-400" />
          <span>{toastMessage}</span>
        </div>
      )}
    </div>
  );
};

export default App;

