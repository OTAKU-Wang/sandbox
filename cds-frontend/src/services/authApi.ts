import api from './api';
import type { LoginRequest, LoginResponse, RegisterRequest } from '../types/api';
import type { User } from '../types/models';

/**
 * `POST /auth/generate-sm2-keys` 的**下行响应体**：后端下发一对临时 SM2 密钥，
 * 私钥字段只在本地使用（签名走 utils/crypto.ts 的 sm2Sign 本地算），**绝不回到任何上行 body**。
 * 单独命名并放到所有 `api.post(...)` 之前：A258 静态门（services/__tests__/authApi.test.ts）
 * 扫的是整个文件源文，把带私钥字段的类型写成响应形状、与上行代码隔开，才能让人和门都分清上行/下行。
 */
export interface SM2KeyPairResponse {
  public_key: string;
  /** 下行临时私钥：只进本地密钥存储，不得出现在任何请求体里 */
  private_key: string;
  message: string;
}

export const authApi = {
  login: (data: LoginRequest): Promise<LoginResponse> =>
    api.post('/auth/login', data),

  register: (data: RegisterRequest): Promise<LoginResponse> =>
    api.post('/auth/register', data),

  refresh: (refreshToken: string): Promise<LoginResponse> =>
    api.post('/auth/refresh', { refresh_token: refreshToken }),

  getMe: (): Promise<User> => api.get('/auth/me'),

  generateSM2Keys: (): Promise<SM2KeyPairResponse> =>
    api.post('/auth/generate-sm2-keys'),

  // A258：原有的「服务端签名」入口已删除（全仓零调用方的死代码，它把 SM2 私钥放进**上行请求体**）。
  // 前端签名一律在本地做（utils/crypto.ts 的 sm2Sign + contractSignPayload），私钥绝不出浏览器：
  // services/ 这一层由 src/services/__tests__/authApi.test.ts 静态钉住这条不变量（所以上面那个响应类型里
  // 的私钥字段只能写成下行形状）。后端那个签名端点的下线是另一件事（尚未排期），不在本次前端改动内。
};
