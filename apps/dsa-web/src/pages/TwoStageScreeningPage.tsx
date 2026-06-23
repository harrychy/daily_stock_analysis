/**
 * 两阶段股票筛选页面
 *
 * 工作流：
 * 1. 用户从 watchlist 多选 + 文本框手输 → 合并去重得到股票池
 * 2. 点击"开始扫描"调 POST /screening/technical
 * 3. EventSource 订阅 /analysis/tasks/stream，按 screening_score 实时刷新表格
 * 4. 完成后输入 Top N，点击"提升到 LLM"调 POST /screening/{id}/promote
 * 5. promote 返回的 task_id 列表展示成功提交、跳过的（今日已分析）
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowLeft, ListChecks, Play, Sparkles } from 'lucide-react';

import apiClient from '../api/index';
import {
  screeningApi,
  type PromoteResponse,
  type ScreeningResultItem,
} from '../api/screening';
import { AppPage, Button } from '../components/common';
import { useWatchlist } from '../hooks/useWatchlist';

interface StreamScorePayload {
  screening_id: string;
  code: string;
  name: string;
  signal_score: number;
  trend_strength: number;
  trend_status: string;
  buy_signal: string;
  ma_alignment: string;
  current_price: number | null;
  volume_ratio: number | null;
  error_msg: string;
  progress: number;
}

const parsePastedCodes = (text: string): string[] => {
  return text
    .split(/[\s,，;；\n\r]+/)
    .map((s) => s.trim().toUpperCase())
    .filter(Boolean);
};

const formatNumber = (v: number | null | undefined, digits = 2): string => {
  if (v == null || Number.isNaN(Number(v))) return '-';
  return Number(v).toFixed(digits);
};

const TwoStageScreeningPage: React.FC = () => {
  const navigate = useNavigate();
  const { watchlistCodes, isLoading: watchlistLoading } = useWatchlist();

  // 股票池输入
  const [selectedFromWatchlist, setSelectedFromWatchlist] = useState<Set<string>>(new Set());
  const [pastedCodes, setPastedCodes] = useState<string>('');

  // 筛选状态
  const [screeningId, setScreeningId] = useState<string | null>(null);
  const [status, setStatus] = useState<'idle' | 'running' | 'completed' | 'failed'>('idle');
  const [results, setResults] = useState<Map<string, ScreeningResultItem>>(new Map());
  const [progress, setProgress] = useState(0);
  const [errorMsg, setErrorMsg] = useState<string>('');

  // promote 状态
  const [topN, setTopN] = useState<string>('');  // string 是为了允许空白；提交时转 number
  const [promoteResult, setPromoteResult] = useState<PromoteResponse | null>(null);
  const [isPromoting, setIsPromoting] = useState(false);

  const eventSourceRef = useRef<EventSource | null>(null);
  const pollingTimerRef = useRef<number | null>(null);

  // 合并后的最终股票池
  const finalCodes = useMemo(() => {
    const fromList = Array.from(selectedFromWatchlist);
    const fromText = parsePastedCodes(pastedCodes);
    const merged = new Set<string>([...fromList, ...fromText]);
    return Array.from(merged);
  }, [selectedFromWatchlist, pastedCodes]);

  // ========== 轮询兜底 ==========
  const startPolling = useCallback((sid: string) => {
    const tick = async () => {
      try {
        const data = await screeningApi.getStatus(sid);
        const newResults = new Map<string, ScreeningResultItem>();
        for (const r of data.results) newResults.set(r.code, r);
        setResults(newResults);
        if (data.totalCount > 0) {
          setProgress(Math.round((100 * (data.successCount + data.failedCount)) / data.totalCount));
        }
        if (data.status === 'completed' || data.status === 'failed') {
          setStatus(data.status);
          if (pollingTimerRef.current != null) {
            window.clearInterval(pollingTimerRef.current);
            pollingTimerRef.current = null;
          }
        }
      } catch (e) {
        console.error('[Screening] polling error', e);
      }
    };
    void tick();
    pollingTimerRef.current = window.setInterval(tick, 3000);
  }, []);

  // ========== SSE 订阅 ==========
  const startSSE = useCallback((sid: string) => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
    }
    const baseUrl = apiClient.defaults.baseURL || '';
    const es = new EventSource(`${baseUrl}/api/v1/analysis/tasks/stream`, {
      withCredentials: true,
    });
    eventSourceRef.current = es;

    es.addEventListener('screening_started', (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data);
        if (data.screening_id !== sid) return;
        setStatus('running');
      } catch {
        /* ignore */
      }
    });

    es.addEventListener('screening_score', (e: MessageEvent) => {
      try {
        const data: StreamScorePayload = JSON.parse(e.data);
        if (data.screening_id !== sid) return;
        setResults((prev) => {
          const next = new Map(prev);
          next.set(data.code, {
            code: data.code,
            name: data.name,
            signalScore: data.signal_score,
            trendStrength: data.trend_strength,
            trendStatus: data.trend_status,
            buySignal: data.buy_signal,
            maAlignment: data.ma_alignment,
            currentPrice: data.current_price,
            volumeRatio: data.volume_ratio,
            errorMsg: data.error_msg,
          });
          return next;
        });
        setProgress(data.progress);
      } catch {
        /* ignore */
      }
    });

    es.addEventListener('screening_completed', (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data);
        if (data.screening_id !== sid) return;
        setStatus('completed');
        setProgress(100);
      } catch {
        /* ignore */
      }
    });

    es.addEventListener('screening_failed', (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data);
        if (data.screening_id !== sid) return;
        setStatus('failed');
        setErrorMsg(data.error || '筛选失败');
      } catch {
        /* ignore */
      }
    });

    es.onerror = () => {
      // SSE 不可用时降级到轮询
      console.warn('[Screening] SSE error, fallback to polling');
      es.close();
      eventSourceRef.current = null;
      startPolling(sid);
    };
  }, [startPolling]);

  // 清理
  useEffect(() => {
    return () => {
      eventSourceRef.current?.close();
      if (pollingTimerRef.current != null) {
        window.clearInterval(pollingTimerRef.current);
      }
    };
  }, []);

  // ========== 启动筛选 ==========
  const handleStart = async () => {
    if (finalCodes.length === 0) {
      setErrorMsg('请至少选择或输入一只股票');
      return;
    }
    setErrorMsg('');
    setResults(new Map());
    setProgress(0);
    setStatus('running');
    setPromoteResult(null);
    try {
      const resp = await screeningApi.start(finalCodes);
      setScreeningId(resp.screeningId);
      startSSE(resp.screeningId);
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      setErrorMsg(`启动失败: ${message}`);
      setStatus('failed');
    }
  };

  // ========== 提升到 LLM ==========
  const handlePromote = async () => {
    if (!screeningId) return;
    const n = parseInt(topN, 10);
    if (!Number.isFinite(n) || n <= 0) {
      setErrorMsg('请输入有效的 Top N（正整数）');
      return;
    }
    setIsPromoting(true);
    setErrorMsg('');
    try {
      const resp = await screeningApi.promote(screeningId, n);
      setPromoteResult(resp);
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      setErrorMsg(`提交失败: ${message}`);
    } finally {
      setIsPromoting(false);
    }
  };

  // 排序后的结果（按 signalScore desc）
  const sortedResults = useMemo(() => {
    return Array.from(results.values()).sort((a, b) => {
      if (a.errorMsg && !b.errorMsg) return 1;
      if (!a.errorMsg && b.errorMsg) return -1;
      return b.signalScore - a.signalScore;
    });
  }, [results]);

  return (
    <AppPage>
      <div className="flex flex-col gap-4 p-4 md:p-6">
        {/* 顶部 */}
        <div className="flex items-center gap-3">
          <Button variant="ghost" size="sm" onClick={() => navigate('/')}>
            <ArrowLeft className="h-4 w-4 mr-1" />
            返回首页
          </Button>
          <h1 className="text-xl font-semibold flex items-center gap-2">
            <Sparkles className="h-5 w-5 text-blue-500" />
            两阶段筛选
          </h1>
          <span className="text-xs text-gray-500">
            技术面快速预筛 → Top N 进入 LLM 综合分析
          </span>
        </div>

        {/* 错误提示 */}
        {errorMsg && (
          <div className="rounded border border-red-300 bg-red-50 p-3 text-sm text-red-700">
            {errorMsg}
          </div>
        )}

        {/* 第一阶段：股票池输入 */}
        <section className="rounded border border-gray-200 bg-white p-4">
          <h2 className="mb-3 flex items-center gap-2 font-medium">
            <ListChecks className="h-4 w-4 text-gray-600" />
            1. 选择股票池（共 {finalCodes.length} 只）
          </h2>

          <div className="mb-3">
            <div className="text-xs text-gray-600 mb-1">
              从自选股选择（{watchlistLoading ? '加载中...' : `${watchlistCodes.length} 只`}）
            </div>
            <div className="flex flex-wrap gap-2">
              {watchlistCodes.map((code) => (
                <label
                  key={code}
                  className={`cursor-pointer rounded border px-2 py-1 text-xs ${
                    selectedFromWatchlist.has(code)
                      ? 'border-blue-500 bg-blue-50 text-blue-700'
                      : 'border-gray-300 bg-white text-gray-700'
                  }`}
                >
                  <input
                    type="checkbox"
                    className="hidden"
                    checked={selectedFromWatchlist.has(code)}
                    onChange={(e) => {
                      setSelectedFromWatchlist((prev) => {
                        const next = new Set(prev);
                        if (e.target.checked) next.add(code);
                        else next.delete(code);
                        return next;
                      });
                    }}
                  />
                  {code}
                </label>
              ))}
              {watchlistCodes.length > 0 && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setSelectedFromWatchlist(new Set(watchlistCodes))}
                >
                  全选
                </Button>
              )}
            </div>
          </div>

          <div>
            <div className="text-xs text-gray-600 mb-1">
              或手输（逗号 / 换行 / 空格 分隔；A股6位 / 港股hk前缀 / 美股代号）
            </div>
            <textarea
              className="w-full rounded border border-gray-300 p-2 text-sm font-mono"
              rows={3}
              placeholder="例如：600519, AAPL, hk00700"
              value={pastedCodes}
              onChange={(e) => setPastedCodes(e.target.value)}
              disabled={status === 'running'}
            />
          </div>

          <div className="mt-3">
            <Button
              variant="primary"
              onClick={handleStart}
              disabled={finalCodes.length === 0 || status === 'running'}
            >
              <Play className="h-4 w-4 mr-1" />
              {status === 'running' ? '扫描中...' : '开始技术面扫描'}
            </Button>
          </div>
        </section>

        {/* 中部：实时结果表格 */}
        {(status !== 'idle' || results.size > 0) && (
          <section className="rounded border border-gray-200 bg-white p-4">
            <h2 className="mb-3 font-medium">
              2. 技术面评分结果
              <span className="ml-2 text-xs text-gray-500">
                ({results.size} / {finalCodes.length} - 进度 {progress}%)
              </span>
            </h2>

            {/* 进度条 */}
            <div className="mb-3 h-2 w-full overflow-hidden rounded bg-gray-100">
              <div
                className="h-full bg-blue-500 transition-all"
                style={{ width: `${progress}%` }}
              />
            </div>

            {/* 表格 */}
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead className="bg-gray-50 text-gray-600">
                  <tr>
                    <th className="px-2 py-1 text-left">代码</th>
                    <th className="px-2 py-1 text-left">名称</th>
                    <th className="px-2 py-1 text-right">技术面分</th>
                    <th className="px-2 py-1 text-left">趋势</th>
                    <th className="px-2 py-1 text-left">买入信号</th>
                    <th className="px-2 py-1 text-right">现价</th>
                    <th className="px-2 py-1 text-right">量比</th>
                    <th className="px-2 py-1 text-left">均线</th>
                  </tr>
                </thead>
                <tbody>
                  {sortedResults.map((r) => (
                    <tr key={r.code} className="border-t border-gray-100">
                      <td className="px-2 py-1 font-mono">{r.code}</td>
                      <td className="px-2 py-1">{r.name || '-'}</td>
                      <td className="px-2 py-1 text-right">
                        {r.errorMsg ? (
                          <span className="text-red-500">失败</span>
                        ) : (
                          <span
                            className={
                              r.signalScore >= 65
                                ? 'font-semibold text-green-600'
                                : r.signalScore >= 50
                                ? 'text-blue-600'
                                : 'text-gray-500'
                            }
                          >
                            {r.signalScore}
                          </span>
                        )}
                      </td>
                      <td className="px-2 py-1">{r.errorMsg ? '-' : r.trendStatus}</td>
                      <td className="px-2 py-1">{r.errorMsg ? '-' : r.buySignal}</td>
                      <td className="px-2 py-1 text-right">{formatNumber(r.currentPrice)}</td>
                      <td className="px-2 py-1 text-right">{formatNumber(r.volumeRatio)}</td>
                      <td className="px-2 py-1 text-gray-500">{r.errorMsg ? r.errorMsg : r.maAlignment}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}

        {/* 下部：promote 到 LLM */}
        {status === 'completed' && results.size > 0 && (
          <section className="rounded border border-gray-200 bg-white p-4">
            <h2 className="mb-3 font-medium">3. 选择 Top N 进入 LLM 综合分析</h2>
            <div className="flex items-center gap-3">
              <input
                type="number"
                min={1}
                max={finalCodes.length}
                placeholder="Top N（请输入正整数）"
                className="w-40 rounded border border-gray-300 px-2 py-1 text-sm"
                value={topN}
                onChange={(e) => setTopN(e.target.value)}
              />
              <span className="text-xs text-gray-500">共 {finalCodes.length} 只可选</span>
              <Button
                variant="primary"
                onClick={handlePromote}
                disabled={!topN || isPromoting}
              >
                {isPromoting ? '提交中...' : '提升到 LLM 分析'}
              </Button>
            </div>

            {promoteResult && (
              <div className="mt-3 rounded bg-gray-50 p-3 text-xs">
                <div>
                  ✅ 已提交 <strong>{promoteResult.promotedCount}</strong> 只到 LLM 队列：
                  <span className="ml-2 font-mono">
                    {promoteResult.promotedCodes.join(', ') || '(无)'}
                  </span>
                </div>
                {promoteResult.skippedAlreadyAnalyzed.length > 0 && (
                  <div className="mt-1 text-gray-600">
                    ⏭ 跳过 {promoteResult.skippedAlreadyAnalyzed.length} 只（今日已分析过）：
                    <span className="ml-2 font-mono">
                      {promoteResult.skippedAlreadyAnalyzed.join(', ')}
                    </span>
                  </div>
                )}
                {promoteResult.acceptedTasks.length > 0 && (
                  <div className="mt-2">
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => navigate('/')}
                    >
                      去首页查看 LLM 分析进度 →
                    </Button>
                  </div>
                )}
              </div>
            )}
          </section>
        )}
      </div>
    </AppPage>
  );
};

export default TwoStageScreeningPage;
