import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  closeExperienceConversation,
  createExperienceConversation,
  eventExperienceDetail,
  resolveExperienceInteraction,
  streamExperienceMessage,
  streamExperienceOpening,
} from '@/lib/api/experience-ai-chat';
import { parseRuntimeSse } from '@/lib/api/runtime-events';

function sseResponse(chunks: string[]): Response {
  const encoder = new TextEncoder();
  return new Response(
    new ReadableStream({
      start(controller) {
        for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
        controller.close();
      },
    }),
    { status: 200, headers: { 'Content-Type': 'text/event-stream' } }
  );
}

async function collect<T>(stream: AsyncGenerator<T>): Promise<T[]> {
  const values: T[] = [];
  for await (const value of stream) values.push(value);
  return values;
}

describe('experience AI chat API', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('creates and closes a field-bound conversation through the business router', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            conversation_id: 8,
            scope: { field: 'background' },
            field_status: 'incomplete',
            revision: 2,
          }),
          { status: 200 }
        )
      )
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal('fetch', fetchMock);

    await expect(createExperienceConversation(7, { field: 'background' })).resolves.toMatchObject({
      conversation_id: 8,
      revision: 2,
    });
    await closeExperienceConversation(8, 'left_field');

    expect(fetchMock.mock.calls.map(([url]) => String(url))).toEqual([
      '/api/v1/experience-ai-chat/conversations',
      '/api/v1/experience-ai-chat/conversations/8/close',
    ]);
    const createOptions = fetchMock.mock.calls[0][1] as RequestInit;
    expect(createOptions.body).toBe(
      JSON.stringify({ experience_id: 7, scope: { field: 'background' } })
    );
  });

  it('parses fragmented text and atomic proposal SSE events', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        sseResponse([
          ': 代理刷新填充\r\n\r\nevent: run.started\r\ndata: {"type":"run.started","run_id":12,"sequence":1,"payload":{"output_id":1}}\r\n\r\nevent: output.',
          'delta\ndata: {"type":"output.delta","run_id":12,"sequence":2,"payload":{"text":"你好"}}\n\nevent: interaction.requested\n',
          'data: {"type":"interaction.requested","run_id":12,"sequence":3,"payload":{"interaction_id":4,"kind":"approval","request":{"tool_name":"content_change","proposal":{"scope":{"field":"background","evidence_id":null},"suggested_content":"新值"}}}}\n\n',
        ])
      );
    vi.stubGlobal('fetch', fetchMock);
    const controller = new AbortController();

    const events = await collect(streamExperienceOpening(8, controller.signal));

    expect(events.map((event) => event.type)).toEqual([
      'run.started',
      'output.delta',
      'interaction.requested',
    ]);
    expect(events[2].payload).toMatchObject({ interaction_id: 4, kind: 'approval' });
  });

  it('sends one user turn and one approval resolution with caller-owned signals', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        sseResponse([
          'event: run.completed\ndata: {"type":"run.completed","run_id":12,"sequence":1,"payload":{}}\n\n',
        ])
      )
      .mockResolvedValueOnce(
        sseResponse([
          'event: run.completed\ndata: {"type":"run.completed","run_id":12,"sequence":1,"payload":{}}\n\n',
        ])
      );
    vi.stubGlobal('fetch', fetchMock);
    const messageController = new AbortController();
    const resolutionController = new AbortController();

    await collect(streamExperienceMessage(8, '继续完善', 'message-1', messageController.signal));
    await collect(
      resolveExperienceInteraction(12, 4, 'approve', 'resolution-1', resolutionController.signal)
    );

    const messageOptions = fetchMock.mock.calls[0][1] as RequestInit;
    const resolutionOptions = fetchMock.mock.calls[1][1] as RequestInit;
    expect(messageOptions.signal).toBe(messageController.signal);
    expect(messageOptions.body).toBe(
      JSON.stringify({ content: '继续完善', client_message_id: 'message-1' })
    );
    expect(resolutionOptions.signal).toBe(resolutionController.signal);
    expect(String(fetchMock.mock.calls[1][0])).toContain('/runs/12/interactions/4/resolve');
    expect(resolutionOptions.body).toBe(
      JSON.stringify({ decision: 'approve', client_resolution_id: 'resolution-1' })
    );
  });

  it('extracts an authoritative experience only from business result events', () => {
    const experience = { experience_id: 7, title: 'Updated' };

    expect(
      eventExperienceDetail({
        type: 'result.available',
        run_id: 12,
        sequence: 1,
        payload: { kind: 'tool_result', result: { experience } },
      })
    ).toBe(experience);
    expect(eventExperienceDetail({ type: 'run.completed', sequence: 1, payload: {} })).toBeNull();
  });

  it('rejects envelopes that bypass the unified event contract', async () => {
    const malformed = [
      'event: run.completed\ndata: {"type":"run.completed","payload":{}}\n\n',
      'event: run.completed\ndata: {"type":"run.completed","sequence":1,"payload":[]}\n\n',
    ];

    for (const record of malformed) {
      await expect(collect(parseRuntimeSse(sseResponse([record])))).rejects.toThrow(
        /Runtime event/
      );
    }
  });

  it('parses CRLF record boundaries split across network chunks', async () => {
    const events = await collect(
      parseRuntimeSse(
        sseResponse([
          'event: run.completed\r',
          '\ndata: {"type":"run.completed","run_id":12,"sequence":1,"payload":{}}\r',
          '\n\r',
          '\n',
        ])
      )
    );

    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({ type: 'run.completed', run_id: 12, sequence: 1 });
  });
});
