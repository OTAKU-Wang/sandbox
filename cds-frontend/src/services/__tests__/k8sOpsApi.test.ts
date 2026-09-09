import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../api', () => ({
  default: {
    get: vi.fn(),
  },
}));

import api from '../api';
import {
  listDeployments,
  listOpsNetworkPolicies,
  listOpsPvcs,
  getOpsLogs,
} from '../k8sOpsApi';

const mockedGet = vi.mocked(api.get);

describe('k8sOpsApi', () => {
  beforeEach(() => {
    mockedGet.mockReset();
  });

  it('listDeployments hits /ops/deployments', async () => {
    mockedGet.mockResolvedValue({
      cluster_available: false,
      namespace: 'cds-sandbox',
      items: [],
      count: 0,
    });
    const res = await listDeployments();
    expect(mockedGet).toHaveBeenCalledWith('/ops/deployments');
    expect(res.cluster_available).toBe(false);
  });

  it('listOpsNetworkPolicies passes skip/limit params', async () => {
    mockedGet.mockResolvedValue({ cluster_available: false, items: [], total: 0, page: 1, page_size: 20 });
    await listOpsNetworkPolicies(20, 20);
    expect(mockedGet).toHaveBeenCalledWith('/ops/network-policies', { params: { skip: 20, limit: 20 } });
  });

  it('listOpsPvcs hits /ops/pvcs', async () => {
    mockedGet.mockResolvedValue({ cluster_available: false, items: [], count: 0 });
    const res = await listOpsPvcs();
    expect(mockedGet).toHaveBeenCalledWith('/ops/pvcs');
    expect(res.count).toBe(0);
  });

  it('getOpsLogs hits /ops/logs/:id with tail', async () => {
    mockedGet.mockResolvedValue({
      session_id: 's1',
      source: 'audit',
      pod_name: null,
      cluster_available: false,
      logs: [],
      count: 0,
    });
    const res = await getOpsLogs('s1', 100);
    expect(mockedGet).toHaveBeenCalledWith('/ops/logs/s1', { params: { tail: 100 } });
    expect(res.source).toBe('audit');
  });
});
