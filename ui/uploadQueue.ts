export type UploadReceipt = {id: string; inspection?: string; [key: string]: unknown};
export type UploadItem = {
  id: string;
  file: File;
  status: 'queued' | 'uploading' | 'received' | 'failed';
  bytesSent: number;
  bytesTotal: number;
  error: string;
  receipt: UploadReceipt | null;
  attempts: number;
};
export type UploadTransport = (file: File, onProgress: (sent: number, total: number) => void) => Promise<UploadReceipt>;

export function createUploadItems(files: ArrayLike<File> | Iterable<File>): UploadItem[] {
  return Array.from(files).map(file => ({
    id: crypto.randomUUID(), file, status: 'queued', bytesSent: 0, bytesTotal: file.size,
    error: '', receipt: null, attempts: 0,
  }));
}

export async function runUploadQueue(
  items: readonly UploadItem[],
  upload: UploadTransport,
  onUpdate: (items: UploadItem[]) => void = () => {},
): Promise<UploadItem[]> {
  const current = items.map(item => ({...item}));
  const emit = () => onUpdate(current.map(item => ({...item})));
  for (let index = 0; index < current.length; index++) {
    if (current[index].status === 'received') continue;
    const size = current[index].file.size;
    const invalid = size <= 0 ? '文件为空，请重新选择' : size > 1073741824 ? '单文件超过 1 GiB 限制，请压缩后重试' : '';
    if (invalid) {
      current[index] = {...current[index], status: 'failed', error: invalid};
      emit();
      continue;
    }
    current[index] = {...current[index], status: 'uploading', bytesSent: 0, error: '', attempts: current[index].attempts + 1};
    emit();
    try {
      const receipt = await upload(current[index].file, (sent, total) => {
        if (current[index].status !== 'uploading') return;
        const bytesTotal = Number.isFinite(total) && total > 0 ? total : current[index].file.size;
        current[index] = {...current[index], bytesTotal, bytesSent: Math.max(0, Math.min(bytesTotal, sent))};
        emit();
      });
      current[index] = {
        ...current[index], status: 'received', receipt, bytesSent: current[index].file.size,
        bytesTotal: current[index].file.size,
      };
      emit();
    } catch (error) {
      // A lost response can have persisted successfully. Keep it for explicit
      // retry, which the server reconciles by project, source and content hash.
      current[index] = {...current[index], status: 'failed', error: error instanceof Error ? error.message : '文件导入失败，请重试'};
      emit();
    }
  }
  return current;
}
