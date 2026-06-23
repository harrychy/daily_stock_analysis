import apiClient from './index';
import { toCamelCase } from './utils';

// ============================================================
// 类型定义
// ============================================================

export interface StartScreeningRequest {
  stock_codes: string[];
}

export interface StartScreeningResponse {
  screeningId: string;
  parentTaskId: string;
  totalCount: number;
}

export interface ScreeningResultItem {
  code: string;
  name: string;
  signalScore: number;
  trendStrength: number;
  trendStatus: string;
  buySignal: string;
  maAlignment: string;
  currentPrice: number | null;
  volumeRatio: number | null;
  errorMsg: string;
}

export interface ScreeningStatusResponse {
  screeningId: string;
  status: 'pending' | 'running' | 'completed' | 'failed';
  totalCount: number;
  successCount: number;
  failedCount: number;
  startedAt: string | null;
  completedAt: string | null;
  results: ScreeningResultItem[];
}

export interface PromoteRequest {
  top_n: number;
  report_type?: 'simple' | 'detailed' | 'full' | 'brief';
}

export interface PromoteAcceptedTask {
  taskId: string;
  stockCode: string;
  status: string;
}

export interface PromoteResponse {
  screeningId: string;
  requestedTopN: number;
  promotedCount: number;
  promotedCodes: string[];
  skippedAlreadyAnalyzed: string[];
  acceptedTasks: PromoteAcceptedTask[];
  duplicateTasks: Array<Record<string, unknown>>;
}

// ============================================================
// API
// ============================================================

export const screeningApi = {
  /**
   * 启动技术面预筛（异步）。返回 screening_id 后通过 SSE 或轮询拿结果。
   */
  start: async (codes: string[]): Promise<StartScreeningResponse> => {
    const response = await apiClient.post('/api/v1/screening/technical', {
      stock_codes: codes,
    } satisfies StartScreeningRequest);
    return toCamelCase<StartScreeningResponse>(response.data);
  },

  /**
   * 查询单次筛选状态（轮询兜底）。
   */
  getStatus: async (screeningId: string): Promise<ScreeningStatusResponse> => {
    const response = await apiClient.get(`/api/v1/screening/${screeningId}`);
    return toCamelCase<ScreeningStatusResponse>(response.data);
  },

  /**
   * 提升 Top N 到 LLM 综合分析队列。
   */
  promote: async (
    screeningId: string,
    topN: number,
    reportType: PromoteRequest['report_type'] = 'detailed',
  ): Promise<PromoteResponse> => {
    const response = await apiClient.post(`/api/v1/screening/${screeningId}/promote`, {
      top_n: topN,
      report_type: reportType,
    } satisfies PromoteRequest);
    return toCamelCase<PromoteResponse>(response.data);
  },
};
