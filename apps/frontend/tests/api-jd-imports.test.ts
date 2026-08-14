import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  addJDRequirement,
  deleteJDRequirement,
  listJDImports,
  streamJDImport,
  updateJDImport,
  type JDRequirement,
} from '@/lib/api/jd-imports';

describe('JD import API client', () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => vi.unstubAllGlobals());

  const lastCall = () => {
    const [url, options] = fetchMock.mock.calls.at(-1)!;
    return { url: String(url), options: (options ?? {}) as RequestInit };
  };

  it('lists saved JDs from the independent JD endpoint', async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ items: [], total: 0 })));
    await expect(listJDImports()).resolves.toEqual({ items: [], total: 0 });
    expect(lastCall().url).toContain('/api/v1/jd-imports');
  });

  it('includes the information revision when updating metadata', async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ id: 4 })));
    await updateJDImport(4, { company: 'Acme', status: 'confirmed' }, 7);
    const { url, options } = lastCall();
    expect(url).toContain('/jd-imports/4');
    expect(options.method).toBe('PATCH');
    expect(JSON.parse(String(options.body))).toEqual({
      company: 'Acme',
      status: 'confirmed',
      expected_revision: 7,
    });
  });

  it('uses both revisions when deleting a requirement', async () => {
    const requirement: JDRequirement = {
      id: 9,
      jd_information_id: 4,
      priority: 'required',
      content: 'Python',
      sort_order: 0,
      revision: 3,
    };
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ id: 4 })));
    await deleteJDRequirement(4, requirement, 7);
    const { url, options } = lastCall();
    expect(options.method).toBe('DELETE');
    expect(url).toContain('/jd-imports/4/requirements/9?');
    expect(url).toContain('expected_revision=3');
    expect(url).toContain('expected_information_revision=7');
  });

  it('adds a requirement against the current information revision', async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ id: 4 })));
    await addJDRequirement(4, { priority: 'preferred', content: 'FastAPI', sort_order: 2 }, 7);
    expect(JSON.parse(String(lastCall().options.body))).toEqual({
      priority: 'preferred',
      content: 'FastAPI',
      sort_order: 2,
      expected_information_revision: 7,
    });
  });

  it('parses JD-specific SSE events from the import stream', async () => {
    fetchMock.mockResolvedValue(
      new Response(
        'event: jd.questions.requested\ndata: {"batch_id":"b1","round":1,"questions":[]}\n\n' +
          'event: jd.import.completed\ndata: {"persisted_ids":[12],"errors":[]}\n\n',
        { headers: { 'Content-Type': 'text/event-stream' } }
      )
    );
    const events = [];
    for await (const event of streamJDImport(
      2,
      'JD text',
      'message-1',
      new AbortController().signal
    )) {
      events.push(event);
    }
    expect(events.map((event) => event.event)).toEqual([
      'jd.questions.requested',
      'jd.import.completed',
    ]);
    expect(events[1].data.persisted_ids).toEqual([12]);
  });
});
