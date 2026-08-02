import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  closeExperienceConversation,
  createExperienceConversation,
  eventExperienceDetail,
  resolveExperienceProposal,
  streamExperienceMessage,
  streamExperienceOpening,
} from '@/lib/api/experience-ai-chat';

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
            target: { key: 'background', ref_id: null },
            field_status: 'incomplete',
            revision: 2,
          }),
          { status: 200 }
        )
      )
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal('fetch', fetchMock);

    await expect(
      createExperienceConversation(7, { key: 'background', ref_id: null })
    ).resolves.toMatchObject({ conversation_id: 8, revision: 2 });
    await closeExperienceConversation(8, 'left_field');

    expect(fetchMock.mock.calls.map(([url]) => String(url))).toEqual([
      '/api/v1/experience-ai-chat/conversations',
      '/api/v1/experience-ai-chat/conversations/8/close',
    ]);
  });

  it('parses fragmented text and atomic proposal SSE events', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        sseResponse([
          ': 代理刷新填充\r\n\r\nevent: assistant.started\r\ndata: {"message_id":1}\r\n\r\nevent: assistant.',
          'delta\ndata: {"text":"你好"}\n\nevent: content_change.requested\n',
          'data: {"proposal_id":4,"tool_name":"content_change","proposal":{"operation":"content_change","target":{"key":"background","ref_id":null},"suggested_content":"新值"}}\n\n',
        ])
      );
    vi.stubGlobal('fetch', fetchMock);
    const controller = new AbortController();

    const events = await collect(streamExperienceOpening(8, controller.signal));

    expect(events.map((event) => event.event)).toEqual([
      'assistant.started',
      'assistant.delta',
      'content_change.requested',
    ]);
    expect(events[2].data).toMatchObject({ proposal_id: 4, tool_name: 'content_change' });
  });

  it('sends one user turn and one approval resolution with caller-owned signals', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(sseResponse(['event: assistant.completed\ndata: {}\n\n']))
      .mockResolvedValueOnce(sseResponse(['event: content_change.applied\ndata: {}\n\n']));
    vi.stubGlobal('fetch', fetchMock);
    const messageController = new AbortController();
    const resolutionController = new AbortController();

    await collect(streamExperienceMessage(8, '继续完善', 'message-1', messageController.signal));
    await collect(
      resolveExperienceProposal(4, 'approve', 'resolution-1', resolutionController.signal)
    );

    const messageOptions = fetchMock.mock.calls[0][1] as RequestInit;
    const resolutionOptions = fetchMock.mock.calls[1][1] as RequestInit;
    expect(messageOptions.signal).toBe(messageController.signal);
    expect(messageOptions.body).toBe(
      JSON.stringify({ content: '继续完善', client_message_id: 'message-1' })
    );
    expect(resolutionOptions.signal).toBe(resolutionController.signal);
    expect(resolutionOptions.body).toBe(
      JSON.stringify({ decision: 'approve', client_resolution_id: 'resolution-1' })
    );
  });

  it('extracts an authoritative experience only from business result events', () => {
    const experience = { experience_id: 7, title: 'Updated' };

    expect(eventExperienceDetail({ event: 'content_change.applied', data: { experience } })).toBe(
      experience
    );
    expect(eventExperienceDetail({ event: 'assistant.completed', data: {} })).toBeNull();
  });
});
