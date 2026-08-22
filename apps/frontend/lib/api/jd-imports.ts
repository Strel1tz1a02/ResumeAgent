import { apiDelete, apiFetch, apiPatch, apiPost, apiStream } from './client';
import { parseRuntimeSse, type RuntimeEvent } from './runtime-events';

export type JDStatus = 'incomplete' | 'confirmed';
export type JDRequirementPriority = 'required' | 'preferred' | 'normal';

export interface JDRequirement {
  id: number;
  jd_information_id: number;
  priority: JDRequirementPriority;
  content: string;
  sort_order: number;
  revision: number;
}

export interface JDImport {
  id: number;
  source_url: string | null;
  company: string;
  job_name: string;
  type: string;
  location: string;
  status: JDStatus;
  revision: number;
  requirements: JDRequirement[];
}

export interface JDImportList {
  items: JDImport[];
  total: number;
}

export interface JDQuestion {
  question_id: string;
  question_key: string;
  kind: 'ownership' | 'missing' | 'conflict' | 'source_access';
  target_jd_keys: string[];
  field: string;
  prompt: string;
  mode: 'choice' | 'text';
  options: string[];
  allow_custom: boolean;
}

export interface JDQuestionBatch {
  run_id: number;
  interaction_id: number;
  batch_id: string;
  round: number;
  questions: JDQuestion[];
}

export interface JDQuestionAnswer {
  question_id: string;
  value?: string;
  skipped?: boolean;
}

async function parseJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(payload?.detail || `JD request failed (${response.status})`);
  }
  return (await response.json()) as T;
}

export async function listJDImports(): Promise<JDImportList> {
  return parseJson(await apiFetch('/jd-imports'));
}

export async function getJDImport(id: number): Promise<JDImport> {
  return parseJson(await apiFetch(`/jd-imports/${id}`));
}

export async function updateJDImport(
  id: number,
  values: Partial<
    Pick<JDImport, 'source_url' | 'company' | 'job_name' | 'type' | 'location' | 'status'>
  >,
  expectedRevision: number
): Promise<JDImport> {
  return parseJson(
    await apiPatch(`/jd-imports/${id}`, { ...values, expected_revision: expectedRevision })
  );
}

export async function deleteJDImport(id: number): Promise<void> {
  const response = await apiDelete(`/jd-imports/${id}`);
  if (!response.ok) throw new Error(`JD delete failed (${response.status})`);
}

export async function addJDRequirement(
  informationId: number,
  values: Pick<JDRequirement, 'priority' | 'content' | 'sort_order'>,
  expectedInformationRevision: number
): Promise<JDImport> {
  return parseJson(
    await apiPost(`/jd-imports/${informationId}/requirements`, {
      ...values,
      expected_information_revision: expectedInformationRevision,
    })
  );
}

export async function updateJDRequirement(
  informationId: number,
  requirement: JDRequirement,
  values: Pick<JDRequirement, 'priority' | 'content' | 'sort_order'>,
  expectedInformationRevision: number
): Promise<JDImport> {
  return parseJson(
    await apiPatch(`/jd-imports/${informationId}/requirements/${requirement.id}`, {
      ...values,
      expected_revision: requirement.revision,
      expected_information_revision: expectedInformationRevision,
    })
  );
}

export async function deleteJDRequirement(
  informationId: number,
  requirement: JDRequirement,
  expectedInformationRevision: number
): Promise<JDImport> {
  const query = new URLSearchParams({
    expected_revision: String(requirement.revision),
    expected_information_revision: String(expectedInformationRevision),
  });
  return parseJson(
    await apiFetch(`/jd-imports/${informationId}/requirements/${requirement.id}?${query}`, {
      method: 'DELETE',
    })
  );
}

export async function createJDConversation(): Promise<number> {
  const result = await parseJson<{ conversation_id: number }>(
    await apiPost('/jd-imports/conversations', {})
  );
  return result.conversation_id;
}

function streamPost(endpoint: string, body: unknown, signal: AbortSignal): Promise<Response> {
  return apiStream(endpoint, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  });
}

export async function* streamJDImport(
  conversationId: number,
  content: string,
  clientMessageId: string,
  signal: AbortSignal
): AsyncGenerator<RuntimeEvent> {
  yield* parseRuntimeSse(
    await streamPost(
      `/jd-imports/conversations/${conversationId}/imports`,
      { content, client_message_id: clientMessageId },
      signal
    )
  );
}

export async function* resolveJDQuestions(
  runId: number,
  interactionId: number,
  batchId: string,
  answers: JDQuestionAnswer[],
  clientResolutionId: string,
  signal: AbortSignal
): AsyncGenerator<RuntimeEvent> {
  yield* parseRuntimeSse(
    await streamPost(
      `/jd-imports/runs/${runId}/interactions/${interactionId}/resolve`,
      {
        batch_id: batchId,
        client_resolution_id: clientResolutionId,
        answers,
      },
      signal
    )
  );
}
