import axios from 'axios';

const API_BASE_URL = process.env.REACT_APP_API_URL || 'http://localhost:5000';

const api = axios.create({
  baseURL: API_BASE_URL,
  timeout: 30000,  // 增加到30秒，DeepSeek API调用需要2-5秒
  headers: {
    'Content-Type': 'application/json',
  },
});

// 深度分析专用实例（更长的超时）
const deepAnalysisApi = axios.create({
  baseURL: API_BASE_URL,
  timeout: 60000,  // 60秒超时，用于AI分析
  headers: {
    'Content-Type': 'application/json',
  },
});

// 鲸鱼相关 API
export const getWhales = (params?: any) => api.get('/api/whales/', { params });
export const getWhaleDetail = (wallet: string) => api.get(`/api/whales/${wallet}`);
export const getWhaleHistory = (wallet: string) => api.get(`/api/whales/${wallet}/history`);
export const getWhaleAnalysis = (wallet: string) => api.get(`/api/whales/${wallet}/analysis`);

// 鲸鱼历史数据 API
export const getWhalePnLHistory = (wallet: string) => api.get(`/api/whales/${wallet}/pnl-history`);
export const getConcentrationTrend = () => api.get('/api/whales/concentration-trend');
export const getPnLRanking = () => api.get('/api/whales/pnl-ranking');

// 深度分析 API（使用更长的超时）
export const getWhaleDeepAnalysis = (wallet: string) => deepAnalysisApi.get(`/api/whales/${wallet}/deep-analysis`);
export const generateWhaleDeepAnalysis = (wallet: string, forceRefresh: boolean = false) => 
  deepAnalysisApi.post(`/api/whales/${wallet}/deep-analysis`, { force_refresh: forceRefresh });

// 汇总 API
export const getSummary = async () => {
  const response = await api.get('/api/summary/');
  return response.data;
};

// 警报相关 API
export const getAlerts = (params?: any) => api.get('/api/alerts/', { params });
export const getAlertDetail = (id: number) => api.get(`/api/alerts/${id}`);
export const markAlertRead = (id: number) => api.post(`/api/alerts/${id}/read`);
export const markAllAlertsRead = (type?: string) => api.post('/api/alerts/mark-all-read', null, { params: { type } });
export const getAlertStats = () => api.get('/api/alerts/stats');

// 套利相关 API
export const getPairCostOpportunities = () => api.get('/api/arbitrage/pair-cost');
export const getCrossMarketOpportunities = () => api.get('/api/arbitrage/cross-market');
export const getArbitrageStatistics = () => api.get('/api/arbitrage/statistics');
export const submitArbitrageFeedback = (data: any) => api.post('/api/arbitrage/feedback', data);
export const compareArbitragePrices = (data: any) => api.post('/api/arbitrage/compare', data);

// 设置相关 API
export const getSettings = () => api.get('/api/settings/');
export const saveSettings = (settings: any) => api.post('/api/settings/', settings);

// 市场数据 API
export const marketsAPI = {
  // 获取活跃市场
  getActive: (limit?: number) => {
    return api.get('/api/markets/active', { params: { limit } });
  },
  
  // 扫描市场
  scan: (markets: any[], scanType?: string) => {
    return api.post('/api/markets/scan', { markets, scan_type: scanType });
  },
  
  // 快速扫描
  quickScan: (limit?: number) => {
    return api.get('/api/markets/quick-scan', { params: { limit } });
  }
};

// 语义套利 API
export const semanticAPI = {
  // 执行扫描
  scan: (markets: any[]) => {
    return api.post('/api/semantic/scan', { markets });
  },
  
  // 获取预定义关系
  getRelationships: () => {
    return api.get('/api/semantic/relationships');
  },
  
  // 获取统计
  getStatistics: () => {
    return api.get('/api/semantic/statistics');
  },
  
  // 测试
  test: () => {
    return api.get('/api/semantic/test');
  },
};

// 信号追踪 API
export const signalsAPI = {
  // 获取信号统计
  getStats: (params?: any) => api.get('/api/signals/stats', { params }),
  
  // 按类型获取信号统计
  getByType: (params?: any) => api.get('/api/signals/by-type', { params }),
  
  // 获取信号列表
  getList: (params?: any) => api.get('/api/signals/list', { params }),
  
  // 获取待处理信号
  getPending: (params?: any) => api.get('/api/signals/pending', { params }),
  
  // 创建信号
  create: (data: any) => api.post('/api/signals/create', data),
  
  // 解决信号
  resolve: (signalId: string, data: any) => api.post(`/api/signals/${signalId}/resolve`, data),
  
  // 获取质量报告
  getReports: (params?: any) => api.get('/api/signals/reports', { params }),
  
  // 获取最新报告
  getLatestReport: (params?: any) => api.get('/api/signals/reports/latest', { params }),
  
  // 获取阈值设置
  getThresholds: () => api.get('/api/signals/thresholds'),
  
  // 获取阈值历史
  getThresholdHistory: (params?: any) => api.get('/api/signals/thresholds/history', { params }),
  
  // 记录阈值变更
  recordThreshold: (data: any) => api.post('/api/signals/thresholds/record', data),
  
  // 获取Dashboard数据
  getDashboard: (params?: any) => api.get('/api/signals/dashboard', { params }),
};

export default api;