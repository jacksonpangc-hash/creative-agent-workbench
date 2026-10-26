import {describe, expect, it} from 'vitest';
import * as queue from './uploadQueue';

describe('local upload queue', () => {
  it('continues through all twelve files after one file fails and retains successful receipts', async () => {
    expect(typeof queue.createUploadItems).toBe('function');
    expect(typeof queue.runUploadQueue).toBe('function');
    const files = Array.from({length: 12}, (_, i) => new File([`local clip ${i}`], `clip-${i}.mp4`, {type: 'video/mp4'}));
    const items = queue.createUploadItems(files);
    const result = await queue.runUploadQueue(items, async file => {
      if (file.name === 'clip-2.mp4') throw new Error('video is not supported');
      return {id: file.name, inspection: 'pending'};
    });
    expect(result.map(item => item.status)).toEqual([
      'received', 'received', 'failed', 'received', 'received', 'received',
      'received', 'received', 'received', 'received', 'received', 'received',
    ]);
    expect(result[2].error).toBe('video is not supported');
    expect(result[11].receipt?.id).toBe('clip-11.mp4');
    expect(result[2].file).toBe(files[2]);
  });

  it('retries only failed files and preserves the original selection when the picker clears', async () => {
    const selection = [new File(['one'], 'one.mp4'), new File(['two'], 'two.mp4')];
    const items = queue.createUploadItems(selection);
    selection.length = 0;
    const first = await queue.runUploadQueue(items, async file => {
      if (file.name === 'two.mp4') throw new Error('local service unavailable');
      return {id: 'one', inspection: 'pending'};
    });
    const retried = await queue.runUploadQueue(first, async file => {
      if (file.name === 'one.mp4') throw new Error('successful file must never upload again');
      return {id: 'two', inspection: 'pending'};
    });
    expect(retried.map(item => [item.receipt?.id, item.status])).toEqual([
      ['one', 'received'], ['two', 'received'],
    ]);
    expect(retried[1].error).toBe('');
  });

  it('reports per-file byte progress while distinguishing receipt from completed indexing', async () => {
    const items = queue.createUploadItems([new File(['video-bytes'], 'local.mp4')]);
    const updates: queue.UploadItem[][] = [];
    const result = await queue.runUploadQueue(items, async (_file, onProgress) => {
      onProgress(5, 11);
      return {id: 'accepted', inspection: 'pending'};
    }, update => updates.push(update));
    expect(updates.some(update => update[0].status === 'uploading' && update[0].bytesSent === 5)).toBe(true);
    expect(result[0].status).toBe('received');
    expect(result[0].receipt?.inspection).toBe('pending');
    expect(result[0].bytesSent).toBe(11);
    expect(updates[0][0].status).toBe('uploading');
  });

  it('limits repeated network failure to this file instead of retrying indefinitely', async () => {
    const items = queue.createUploadItems([new File(['one'], 'one.mp4'), new File(['two'], 'two.mp4')]);
    const result = await queue.runUploadQueue(items, async file => {
      if (file.name === 'one.mp4') throw new TypeError('Load failed');
      return {id: 'two'};
    });
    expect(result[0].status).toBe('failed');
    expect(result[0].attempts).toBe(1);
    expect(result[1].status).toBe('received');
  });

  it('keeps an uncertain network result for explicit retry without requiring files to be selected again', async () => {
    const items = queue.createUploadItems([new File(['one'], 'one.mp4')]);
    let networkUnavailable = true;
    const transport = async () => {
      if (networkUnavailable) {
        networkUnavailable = false;
        throw new TypeError('Load failed');
      }
      return {id: 'already-persisted-receipt', inspection: 'pending'};
    };
    const failed = await queue.runUploadQueue(items, transport);
    expect(failed[0].status).toBe('failed');
    expect(failed[0].error).toBe('Load failed');
    const result = await queue.runUploadQueue(failed, transport);
    expect(result[0].receipt?.id).toBe('already-persisted-receipt');
    expect(result[0].status).toBe('received');
  });

  it('rejects an empty file locally and continues with the remaining video', async () => {
    const items = queue.createUploadItems([new File([], 'empty.mp4'), new File(['local video'], 'valid.mp4')]);
    const result = await queue.runUploadQueue(items, async file => ({id: file.name}));
    expect(result[0].status).toBe('failed');
    expect(result[0].file.name).toBe('empty.mp4');
    expect(result[0].error).toBe('文件为空，请重新选择');
    expect(result[0].attempts).toBe(0);
    expect(result[1].receipt?.id).toBe('valid.mp4');
  });

  it('rejects a file above one GiB before sending but permits the exact boundary', async () => {
    const tooLarge = new File(['size metadata only'], 'large.mp4');
    const boundary = new File(['size metadata only'], 'boundary.mp4');
    Object.defineProperty(tooLarge, 'size', {value: 1073741825});
    Object.defineProperty(boundary, 'size', {value: 1073741824});
    const result = await queue.runUploadQueue(queue.createUploadItems([tooLarge, boundary]), async file => ({id: file.name}));
    expect(result[0].status).toBe('failed');
    expect(result[0].error).toBe('单文件超过 1 GiB 限制，请压缩后重试');
    expect(result[0].attempts).toBe(0);
    expect(result[1].receipt?.id).toBe('boundary.mp4');
  });
});
