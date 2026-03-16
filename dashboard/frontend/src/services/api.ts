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
export const markAlertRead = (id: number) => api.post(`/api/alerts/${id}/read`);

// 套利相关 API
export const getPairCostOpportunities = () => api.get('/api/arbitrage/pair-cost');
export const getCrossMarketOpportunities = () => api.get('/api/arbitrage/cross-market');

// 设置相关 API
export const getSettings = () => api.get('/api/settings/');
export const saveSettings = (settings: any) => api.post('/api/settings/', settings);

export default api;
