import { apiPost, apiStream } from './client';
import { parseRuntimeSse, type RuntimeEvent } from './runtime-events';
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

export interface ExperienceProposal {
  run_id: number;
  interaction_id: number;
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
): AsyncGenerator<RuntimeEvent> {
  yield* parseRuntimeSse(
    await streamPost(`/experience-ai-chat/conversations/${conversationId}/opening`, {}, signal)
  );
}

export async function* streamExperienceMessage(
  conversationId: number,
  content: string,
  clientMessageId: string,
  signal: AbortSignal
): AsyncGenerator<RuntimeEvent> {
  yield* parseRuntimeSse(
    await streamPost(
      `/experience-ai-chat/conversations/${conversationId}/messages`,
      { content, client_message_id: clientMessageId },
      signal
    )
  );
}

export async function* resolveExperienceInteraction(
  runId: number,
  interactionId: number,
  decision: 'approve' | 'reject',
  clientResolutionId: string,
  signal: AbortSignal
): AsyncGenerator<RuntimeEvent> {
  yield* parseRuntimeSse(
    await streamPost(
      `/experience-ai-chat/runs/${runId}/interactions/${interactionId}/resolve`,
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

export function eventExperienceDetail(event: RuntimeEvent): ExperienceDetail | null {
  if (event.type !== 'result.available' || event.payload.kind !== 'tool_result') return null;
  const result = event.payload.result;
  const detail =
    result && typeof result === 'object' ? (result as Record<string, unknown>).experience : null;
  return detail && typeof detail === 'object' ? (detail as ExperienceDetail) : null;
}

export function eventExperienceProposal(event: RuntimeEvent): ExperienceProposal | null {
  if (
    event.type !== 'interaction.requested' ||
    event.payload.kind !== 'approval' ||
    typeof event.run_id !== 'number' ||
    typeof event.payload.interaction_id !== 'number'
  ) {
    return null;
  }
  const request = event.payload.request;
  if (!request || typeof request !== 'object') return null;
  const value = request as Record<string, unknown>;
  if (
    value.tool_name !== 'content_change' ||
    !value.proposal ||
    typeof value.proposal !== 'object'
  ) {
    return null;
  }
  return {
    run_id: event.run_id,
    interaction_id: event.payload.interaction_id,
    tool_name: 'content_change',
    proposal: value.proposal as ExperienceProposal['proposal'],
  };
}
