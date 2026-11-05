import { describe, expect, it } from 'vitest';
import { pickUploadFile } from '../../../services/uploadFile';

describe('pickUploadFile', () => {
  it('从 UploadFile 列表剥出 originFileObj', () => {
    const file = new File(['a,b\n1,2\n'], 'x.csv', { type: 'text/csv' });
    expect(pickUploadFile([{ uid: '1', name: 'x.csv', originFileObj: file }])).toBe(file);
  });

  it('裸 File 直接透传', () => {
    const file = new File(['x'], 'y.csv');
    expect(pickUploadFile(file)).toBe(file);
  });

  it('空列表/非 File 返回 undefined（触发重新选择提示）', () => {
    expect(pickUploadFile([])).toBeUndefined();
    expect(pickUploadFile(undefined)).toBeUndefined();
    expect(pickUploadFile([{ uid: '1', name: 'z.csv' }])).toBeUndefined();
    expect(pickUploadFile('not-a-file')).toBeUndefined();
  });
});
