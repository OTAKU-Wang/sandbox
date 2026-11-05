/**
 * A246（补录同机制的 7 处手拼 URL）：id 直接插进路径段/查询串时，
 * 含 `/`、`?`、`&`、`..` 的 id 可以改写实际命中的路径或注入额外查询参数。
 * 台账 ocr-verified-findings.md §A246：同机制共 8 处，本 spec 锁服务层其余各点
 * （mpcApi :47/:53/:58、fieldExposureApi :40/:46/:55/:58/:61、
 * outputControlApi :50/:53）；`/open/v1/${slug}` 两处已由 services/flow.ts 的
 * `openApiCallPathForSlug`（encodeURIComponent）收口。
 * 口径：路径段一律 encodeURIComponent（与 openApiCallPathForSlug 同一编码口径）；
 * 查询参数一律走 axios `params`（服务层既有编码通道，如 listOpsNetworkPolicies）。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../api', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
  },
}));

import api from '../api';
import { mpcApi } from '../mpcApi';
import { fieldExposureApi } from '../fieldExposureApi';
import { outputControlApi } from '../outputControlApi';

const get = vi.mocked(api.get);
const post = vi.mocked(api.post);
const put = vi.mocked(api.put);

beforeEach(() => {
  get.mockReset();
  post.mockReset();
  put.mockReset();
  get.mockResolvedValue({} as never);
  post.mockResolvedValue({} as never);
  put.mockResolvedValue({} as never);
});

/** 试图逃逸出路径段的 id：`/` 与 `..` 会改写命中的路由。 */
const ESCAPE = '../../admin/secrets';
/** 试图在路径里开出一个查询串的 id。 */
const QUERY = 's-1?admin=true';
/** 试图注入额外查询参数的 id。 */
const AMP = 'a&epsilon=999';

describe('A246 mpcApi：key_id 必须留在自己的路径段里', () => {
  it('getShares：`/` 与 `..` 被编码，命中的仍是 /mpc/keys/<id>/shares', async () => {
    await mpcApi.getShares(ESCAPE);
    expect(get).toHaveBeenCalledWith('/mpc/keys/..%2F..%2Fadmin%2Fsecrets/shares');
    expect(get.mock.calls[0][0]).not.toContain('/admin/secrets');
  });

  it('getShares：`?` 不得在路径里开出查询串', async () => {
    await mpcApi.getShares(QUERY);
    expect(get).toHaveBeenCalledWith('/mpc/keys/s-1%3Fadmin%3Dtrue/shares');
  });

  it('rotateKey：key_id 编码后进 URL', async () => {
    const payload = { reason: '轮转原因至少八个字', ticket_id: null };
    await mpcApi.rotateKey(ESCAPE, payload);
    expect(post).toHaveBeenCalledWith(
      '/mpc/keys/..%2F..%2Fadmin%2Fsecrets/rotate',
      payload,
    );
  });

  it('destroyKey：key_id 编码后进 URL', async () => {
    const payload = { reason: '销毁原因至少八个字', ticket_id: null };
    await mpcApi.destroyKey(QUERY, payload);
    expect(post).toHaveBeenCalledWith(
      '/mpc/keys/s-1%3Fadmin%3Dtrue/destroy',
      payload,
    );
  });

  it('正常 key_id（字母数字-_）编码前后不变，真实调用零变化', async () => {
    await mpcApi.getShares('key-2026_10');
    expect(get).toHaveBeenCalledWith('/mpc/keys/key-2026_10/shares');
  });
});

describe('A246 fieldExposureApi：product_id / request_id 编码', () => {
  it('getVisibility：product_id 编码', async () => {
    await fieldExposureApi.getVisibility(ESCAPE);
    expect(get).toHaveBeenCalledWith(
      '/field-exposure/products/..%2F..%2Fadmin%2Fsecrets/field-visibility',
    );
  });

  it('setVisibility：product_id 编码，body 原样', async () => {
    const body = { default_sensitivity: 'internal' as const, field_rules: {} };
    await fieldExposureApi.setVisibility(QUERY, body);
    expect(put).toHaveBeenCalledWith(
      '/field-exposure/products/s-1%3Fadmin%3Dtrue/field-visibility',
      body,
    );
  });

  it('reviewRequest：request_id 编码，`/` 不再改写命中路径', async () => {
    const body = { approved_fields: ['amount'] };
    await fieldExposureApi.reviewRequest(ESCAPE, body);
    expect(post).toHaveBeenCalledWith(
      '/field-exposure/exposure-requests/..%2F..%2Fadmin%2Fsecrets/review',
      body,
    );
  });

  it('revokeRequest：request_id 编码', async () => {
    await fieldExposureApi.revokeRequest(AMP);
    expect(post).toHaveBeenCalledWith(
      '/field-exposure/exposure-requests/a%26epsilon%3D999/revoke',
    );
  });

  it('getApprovedFields：product_id 编码', async () => {
    await fieldExposureApi.getApprovedFields(ESCAPE);
    expect(get).toHaveBeenCalledWith(
      '/field-exposure/products/..%2F..%2Fadmin%2Fsecrets/approved-fields',
    );
  });
});

describe('A246 outputControlApi：查询串不得手拼，路径段必须编码', () => {
  it('initDPBudget：URL 里没有手拼查询串，值走 axios params（`&` 注入不出去）', async () => {
    await outputControlApi.initDPBudget(AMP, 0.5);
    const call = post.mock.calls[0];
    expect(call[0]).toBe('/output-control/dp/budget/init');
    expect(String(call[0])).not.toContain('?');
    expect((call[2] as { params?: Record<string, unknown> }).params).toEqual({
      session_id: AMP,
      epsilon: 0.5,
    });
  });

  it('initDPBudget：正常入参同样只走 params', async () => {
    await outputControlApi.initDPBudget('sess-7', 1.0);
    expect(post).toHaveBeenCalledWith('/output-control/dp/budget/init', undefined, {
      params: { session_id: 'sess-7', epsilon: 1 },
    });
  });

  it('getDPBudget：session_id 编码', async () => {
    await outputControlApi.getDPBudget(ESCAPE);
    expect(get).toHaveBeenCalledWith('/output-control/dp/budget/..%2F..%2Fadmin%2Fsecrets');
  });
});
