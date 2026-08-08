import { apiPost, apiStream } from './client';
import type { ExperienceDetail } from './experiences';

export interface ExperienceChatScope {
  field: string;
}

export interface ExperienceChangeScope {
  field: string;
  evidence_id: number | null;
}

export interface ExperienceConversation {
  conversation_id: number;
  scope: ExperienceChatScope;
  field_status: 'complete' | 'incomplete';
  revision: number;
}

export interface ExperienceChatEvent {
  event: string;
  data: Record<string, unknown>;
}

export interface ExperienceProposal {
  proposal_id: number;
  tool_name: 'content_change';
  proposal: {
    scope: ExperienceChangeScope;
    current_content?: unknown;
    suggested_content: unknown;
  };
}

async function parseJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(payload?.detail || `AI chat request failed (${response.status})`);
  }
  return (await response.json()) as T;
}

export async function createExperienceConversation(
  experienceId: number,
  scope: ExperienceChatScope
): Promise<ExperienceConversation> {
  return parseJson(
    await apiPost('/experience-ai-chat/conversations', {
      experience_id: experienceId,
      scope,
    })
  );
}

async function* parseSse(response: Response): AsyncGenerator<ExperienceChatEvent> {
  if (!response.ok || !response.body) {
    throw new Error(`AI chat stream failed (${response.status})`);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  try {
    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, '\n');
      let boundary = buffer.indexOf('\n\n');
      while (boundary >= 0) {
        const block = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        let event = 'message';
        const data: string[] = [];
        for (const line of block.split('\n')) {
          if (line.startsWith('event:')) event = line.slice(6).trim();
          if (line.startsWith('data:')) data.push(line.slice(5).trimStart());
        }
        if (data.length) {
          yield { event, data: JSON.parse(data.join('\n')) as Record<string, unknown> };
        }
        boundary = buffer.indexOf('\n\n');
      }
      if (done) break;
    }
  } finally {
    reader.releaseLock();
  }
}

function streamPost(endpoint: string, body: unknown, signal: AbortSignal) {
  return apiStream(endpoint, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  });
}

export async function* streamExperienceOpening(
  conversationId: number,
  signal: AbortSignal
): AsyncGenerator<ExperienceChatEvent> {
  yield* parseSse(
    await streamPost(`/experience-ai-chat/conversations/${conversationId}/opening`, {}, signal)
  );
}

export async function* streamExperienceMessage(
  conversationId: number,
  content: string,
  clientMessageId: string,
  signal: AbortSignal
): AsyncGenerator<ExperienceChatEvent> {
  yield* parseSse(
    await streamPost(
      `/experience-ai-chat/conversations/${conversationId}/messages`,
      { content, client_message_id: clientMessageId },
      signal
    )
  );
}

export async function* resolveExperienceProposal(
  proposalId: number,
  decision: 'approve' | 'reject',
  clientResolutionId: string,
  signal: AbortSignal
): AsyncGenerator<ExperienceChatEvent> {
  yield* parseSse(
    await streamPost(
      `/experience-ai-chat/proposals/${proposalId}/resolve`,
      { decision, client_resolution_id: clientResolutionId },
      signal
    )
  );
}

export async function closeExperienceConversation(
  conversationId: number,
  reason = 'left_field'
): Promise<void> {
  const response = await apiPost(`/experience-ai-chat/conversations/${conversationId}/close`, {
    reason,
  });
  if (!response.ok) throw new Error(`Failed to close AI conversation (${response.status})`);
}

export function eventExperienceDetail(event: ExperienceChatEvent): ExperienceDetail | null {
  const detail = event.data.experience;
  return detail && typeof detail === 'object' ? (detail as ExperienceDetail) : null;
}
