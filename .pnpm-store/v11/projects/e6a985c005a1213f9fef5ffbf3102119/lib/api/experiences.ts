import { apiDelete, apiFetch, apiPatch, apiPost, apiPut } from './client';

export type ExperienceKind =
  'work' | 'internship' | 'project' | 'research' | 'campus' | 'volunteer' | 'other';
export type ExperienceStatus = 'draft' | 'ready' | 'archived';
export type ExperienceListStatus = 'active' | ExperienceStatus;
export type ExperienceSort = 'updated_at_desc' | 'created_at_desc' | 'created_at_asc';

export interface EvidenceItem {
  id: number;
  action: string;
  result: string | null;
  metrics: string | null;
  created_at: string;
  updated_at: string;
}

export interface ExperienceRead {
  experience_id: number;
  kind: ExperienceKind;
  title: string;
  organization: string | null;
  role: string | null;
  location: string | null;
  start_date: string | null;
  end_date: string | null;
  is_current: boolean;
  background: string | null;
  evidence_ids: number[];
  technologies: string[];
  tags: string[];
  notes: string | null;
  status: ExperienceStatus;
  completeness: number;
  archived_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ExperienceDetail extends ExperienceRead {
  evidence_items: EvidenceItem[];
  missing_dimensions: string[];
  suggested_questions: string[];
  field_states: ExperienceFieldState[];
}

export interface ExperienceFieldState {
  key: string;
  ref_id: number | null;
  status: 'complete' | 'incomplete';
  revision: number;
}

export interface ExperienceCreate {
  kind?: ExperienceKind;
  title?: string;
  organization?: string | null;
  role?: string | null;
  location?: string | null;
  start_date?: string | null;
  end_date?: string | null;
  is_current?: boolean;
  background?: string | null;
  technologies?: string[];
  tags?: string[];
  notes?: string | null;
}

export interface ExperienceUpdate extends ExperienceCreate {
  expected_field_revisions?: Record<string, number>;
}

export interface EvidenceCreate {
  action: string;
  result?: string | null;
  metrics?: string | null;
}

export interface EvidenceUpdate {
  action?: string | null;
  result?: string | null;
  metrics?: string | null;
  expected_revision?: number;
}

export interface ExperienceEvidenceSave {
  evidence_id: number;
  action: string;
  result: string | null;
  metrics: string | null;
  expected_revision: number;
}

export interface ExperienceGlobalSave {
  experience: ExperienceUpdate;
  evidence_items: ExperienceEvidenceSave[];
  new_evidence: EvidenceCreate | null;
  expected_collection_revision: number;
}

export interface ExperienceEvidenceSave {
  evidence_id: number;
  action: string;
  result: string | null;
  metrics: string | null;
  expected_revision: number;
}

export interface ExperienceGlobalSave {
  experience: ExperienceUpdate;
  evidence_items: ExperienceEvidenceSave[];
  new_evidence: EvidenceCreate | null;
  expected_collection_revision: number;
}

export interface ExperienceListQuery {
  q?: string;
  kind?: ExperienceKind;
  status?: ExperienceListStatus;
  sort?: ExperienceSort;
}

export interface ExperienceListResponse {
  items: ExperienceRead[];
  total: number;
}

export interface DeletionImpactResponse {
  affected_matches: Array<{ match_id: number; job_title: string }>;
  affected_resumes: string[];
}

export interface ReadyConflictResponse {
  completeness: number;
  missing_dimensions: string[];
}

export class ExperienceReadyConflictError extends Error {
  constructor(public readonly conflict: ReadyConflictResponse) {
    super('Experience is not complete enough to mark ready');
    this.name = 'ExperienceReadyConflictError';
  }
}

function responseErrorMessage(payload: unknown): string | null {
  if (!payload || typeof payload !== 'object') return null;
  const detail = (payload as { detail?: unknown }).detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) =>
        item && typeof item === 'object' && 'msg' in item
          ? String((item as { msg: unknown }).msg)
          : null
      )
      .filter((message): message is string => Boolean(message));
    return messages.length ? messages.join('; ') : null;
  }
  if (detail && typeof detail === 'object') {
    try {
      return JSON.stringify(detail);
    } catch {
      return null;
    }
  }
  return null;
}

async function parseResponse<T>(response: Response, fallback: string): Promise<T> {
  const text = await response.text();
  let payload: unknown = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = null;
    }
  }
  if (!response.ok) {
    throw new Error(
      responseErrorMessage(payload) || text || `${fallback} (status ${response.status}).`
    );
  }
  return payload as T;
}

function experiencePath(experienceId: number): string {
  return `/experiences/${encodeURIComponent(String(experienceId))}`;
}

export async function listExperiences(
  query: ExperienceListQuery = {},
  signal?: AbortSignal
): Promise<ExperienceListResponse> {
  const params = new URLSearchParams();
  if (query.q) params.set('q', query.q);
  if (query.kind) params.set('kind', query.kind);
  if (query.status) params.set('status', query.status);
  if (query.sort) params.set('sort', query.sort);
  const suffix = params.size ? `?${params.toString()}` : '';
  return parseResponse<ExperienceListResponse>(
    await apiFetch(`/experiences${suffix}`, signal ? { signal } : undefined),
    'Failed to load experiences'
  );
}

export async function importExperienceText(text: string): Promise<ExperienceDetail> {
  return parseResponse<ExperienceDetail>(
    await apiPost('/experiences/import-text', { text }),
    'Failed to import experience text'
  );
}

export async function createExperience(payload: ExperienceCreate): Promise<ExperienceDetail> {
  return parseResponse<ExperienceDetail>(
    await apiPost('/experiences', payload),
    'Failed to create experience'
  );
}

export async function fetchExperience(
  experienceId: number,
  signal?: AbortSignal
): Promise<ExperienceDetail> {
  return parseResponse<ExperienceDetail>(
    await apiFetch(experiencePath(experienceId), signal ? { signal } : undefined),
    'Failed to load experience'
  );
}

export async function patchExperience(
  experienceId: number,
  payload: ExperienceUpdate
): Promise<ExperienceDetail> {
  return parseResponse<ExperienceDetail>(
    await apiPatch(experiencePath(experienceId), payload),
    'Failed to update experience'
  );
}

export async function saveExperience(
  experienceId: number,
  payload: ExperienceGlobalSave
): Promise<ExperienceDetail> {
  return parseResponse<ExperienceDetail>(
    await apiPut(`${experiencePath(experienceId)}/save`, payload),
    'Failed to save experience'
  );
}

export async function markExperienceReady(experienceId: number): Promise<ExperienceDetail> {
  const response = await apiPost(`${experiencePath(experienceId)}/mark-ready`, {});
  if (response.status === 409) {
    const payload = (await response.json()) as ReadyConflictResponse;
    throw new ExperienceReadyConflictError(payload);
  }
  return parseResponse<ExperienceDetail>(response, 'Failed to mark experience ready');
}

export async function archiveExperience(experienceId: number): Promise<ExperienceDetail> {
  return parseResponse<ExperienceDetail>(
    await apiPost(`${experiencePath(experienceId)}/archive`, {}),
    'Failed to archive experience'
  );
}

export async function restoreExperience(experienceId: number): Promise<ExperienceDetail> {
  return parseResponse<ExperienceDetail>(
    await apiPost(`${experiencePath(experienceId)}/restore`, {}),
    'Failed to restore experience'
  );
}

export async function getDeletionImpact(
  experienceId: number,
  signal?: AbortSignal
): Promise<DeletionImpactResponse> {
  return parseResponse<DeletionImpactResponse>(
    await apiFetch(
      `${experiencePath(experienceId)}/deletion-impact`,
      signal ? { signal } : undefined
    ),
    'Failed to load deletion impact'
  );
}

export async function deleteExperiencePermanently(experienceId: number): Promise<void> {
  await parseResponse<unknown>(
    await apiDelete(`${experiencePath(experienceId)}/permanent`),
    'Failed to permanently delete experience'
  );
}

export async function createEvidence(
  experienceId: number,
  payload: EvidenceCreate
): Promise<ExperienceDetail> {
  return parseResponse<ExperienceDetail>(
    await apiPost(`${experiencePath(experienceId)}/evidence`, payload),
    'Failed to create evidence'
  );
}

export async function patchEvidence(
  experienceId: number,
  evidenceId: number,
  payload: EvidenceUpdate
): Promise<ExperienceDetail> {
  return parseResponse<ExperienceDetail>(
    await apiPatch(
      `${experiencePath(experienceId)}/evidence/${encodeURIComponent(String(evidenceId))}`,
      payload
    ),
    'Failed to update evidence'
  );
}

export async function deleteEvidence(
  experienceId: number,
  evidenceId: number
): Promise<ExperienceDetail> {
  return parseResponse<ExperienceDetail>(
    await apiDelete(
      `${experiencePath(experienceId)}/evidence/${encodeURIComponent(String(evidenceId))}`
    ),
    'Failed to delete evidence'
  );
}

export async function reorderEvidence(
  experienceId: number,
  evidenceIds: number[]
): Promise<ExperienceDetail> {
  return parseResponse<ExperienceDetail>(
    await apiPut(`${experiencePath(experienceId)}/evidence-order`, { evidence_ids: evidenceIds }),
    'Failed to reorder evidence'
  );
}
