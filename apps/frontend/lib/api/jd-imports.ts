import { apiDelete, apiFetch, apiPatch, apiPost, apiStream } from './client';

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
  batch_id: string;
  round: number;
  questions: JDQuestion[];
}

export interface JDQuestionAnswer {
  question_id: string;
  value?: string;
  skipped?: boolean;
}

export interface JDImportEvent {
  event: string;
  data: Record<string, unknown>;
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

async function* parseSse(response: Response): AsyncGenerator<JDImportEvent> {
  if (!response.ok || !response.body) {
    const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(payload?.detail || `JD import stream failed (${response.status})`);
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
): AsyncGenerator<JDImportEvent> {
  yield* parseSse(
    await streamPost(
      `/jd-imports/conversations/${conversationId}/imports`,
      { content, client_message_id: clientMessageId },
      signal
    )
  );
}

export async function* resolveJDQuestions(
  conversationId: number,
  batchId: string,
  answers: JDQuestionAnswer[],
  clientResolutionId: string,
  signal: AbortSignal
): AsyncGenerator<JDImportEvent> {
  yield* parseSse(
    await streamPost(
      `/jd-imports/conversations/${conversationId}/question-batches/${batchId}/resolve`,
      { type: 'question_batch_answer', client_resolution_id: clientResolutionId, answers },
      signal
    )
  );
}
