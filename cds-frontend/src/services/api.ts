import axios from 'axios';
import { useAuthStore } from '../stores/authStore';

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '/api/v1',
  timeout: 30000,
  headers: { 'Content-Type': 'application/json' },
});

api.interceptors.request.use((config) => {
  const token = useAuthStore.getState().token;
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

let isRefreshing = false;
let failedQueue: Array<{ resolve: (token: string) => void; reject: (err: unknown) => void }> = [];

const processQueue = (error: unknown, token: string | null = null) => {
  failedQueue.forEach((prom) => {
    if (error) {
      prom.reject(error);
    } else {
      prom.resolve(token!);
    }
  });
  failedQueue = [];
};

api.interceptors.response.use(
  (response) => response.data,
  async (error) => {
    const originalRequest = error.config;
    const status = error.response?.status;

    // PZ-15: 408 REQUEST_TIMEOUT — 按 Retry-After 头做单次自动重试。
    // 只对幂等请求（GET/HEAD/OPTIONS）自动重发：408 语义是"同步等待超时、
    // 操作可能仍在沙箱内完成"，非幂等 POST 自动重发会导致执行两次。
    if (status === 408 && originalRequest && !originalRequest._timeoutRetried) {
      const method = String(originalRequest.method ?? 'get').toLowerCase();
      if (['get', 'head', 'options'].includes(method)) {
        const ra = Number(error.response?.headers?.['retry-after']);
        const delayMs = Math.min(Math.max(Number.isFinite(ra) ? ra * 1000 : 2000, 500), 10000);
        originalRequest._timeoutRetried = true;
        await new Promise((resolve) => setTimeout(resolve, delayMs));
        return api(originalRequest);
      }
    }

    if (status === 401 && !originalRequest._retry) {
      const refreshToken = useAuthStore.getState().refreshToken;

      if (!refreshToken) {
        useAuthStore.getState().logout();
        window.location.href = '/login';
        return Promise.reject(error.response?.data || error);
      }

      if (isRefreshing) {
        return new Promise((resolve, reject) => {
          failedQueue.push({ resolve, reject });
        }).then((token) => {
          originalRequest.headers.Authorization = `Bearer ${token}`;
          return api(originalRequest);
        });
      }

      originalRequest._retry = true;
      isRefreshing = true;

      try {
        const response = await axios.post(
          `${import.meta.env.VITE_API_BASE_URL || '/api/v1'}/auth/refresh`,
          { refresh_token: refreshToken },
        );
        const { access_token, refresh_token: newRefreshToken } = response.data;
        useAuthStore.getState().setTokens(access_token, newRefreshToken);
        processQueue(null, access_token);
        originalRequest.headers.Authorization = `Bearer ${access_token}`;
        return api(originalRequest);
      } catch (refreshError) {
        processQueue(refreshError, null);
        useAuthStore.getState().logout();
        window.location.href = '/login';
        return Promise.reject(refreshError);
      } finally {
        isRefreshing = false;
      }
    }

    return Promise.reject(error.response?.data || error);
  },
);

export default api;
