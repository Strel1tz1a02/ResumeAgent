export interface RuntimeEvent {
  type: string;
  run_id?: number | string;
  sequence: number;
  payload: Record<string, unknown>;
}

interface RuntimeEnvelope {
  type?: unknown;
  run_id?: unknown;
  sequence?: unknown;
  payload?: unknown;
}

function parseEnvelope(eventType: string, value: unknown): RuntimeEvent {
  if (!eventType) throw new Error('Runtime event has no type');
  if (!value || typeof value !== 'object') throw new Error('Runtime event has no envelope');
  const envelope = value as RuntimeEnvelope;
  if (envelope.type !== eventType) throw new Error('Runtime event type does not match SSE');
  if (
    !envelope.payload ||
    typeof envelope.payload !== 'object' ||
    Array.isArray(envelope.payload)
  ) {
    throw new Error('Runtime event has no payload');
  }
  if (
    envelope.run_id !== undefined &&
    !(
      (typeof envelope.run_id === 'number' &&
        Number.isSafeInteger(envelope.run_id) &&
        envelope.run_id > 0) ||
      (typeof envelope.run_id === 'string' && envelope.run_id.trim().length > 0)
    )
  ) {
    throw new Error('Runtime event has an invalid run id');
  }
  if (
    typeof envelope.sequence !== 'number' ||
    !Number.isSafeInteger(envelope.sequence) ||
    envelope.sequence <= 0
  ) {
    throw new Error('Runtime event has an invalid sequence');
  }
  return {
    type: eventType,
    run_id: envelope.run_id as number | string | undefined,
    sequence: envelope.sequence,
    payload: envelope.payload as Record<string, unknown>,
  };
}

export async function* parseRuntimeSse(response: Response): AsyncGenerator<RuntimeEvent> {
  if (!response.ok || !response.body) {
    const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(payload?.detail || `Runtime stream failed (${response.status})`);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  try {
    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      let separator = /\r?\n\r?\n/.exec(buffer);
      while (separator) {
        const block = buffer.slice(0, separator.index);
        buffer = buffer.slice(separator.index + separator[0].length);
        let eventType = 'message';
        const data: string[] = [];
        for (const line of block.split(/\r?\n/)) {
          if (line.startsWith('event:')) eventType = line.slice(6).trim();
          if (line.startsWith('data:')) data.push(line.slice(5).trimStart());
        }
        if (data.length) {
          yield parseEnvelope(eventType, JSON.parse(data.join('\n')) as unknown);
        }
        separator = /\r?\n\r?\n/.exec(buffer);
      }
      if (done) break;
    }
  } finally {
    reader.releaseLock();
  }
}
