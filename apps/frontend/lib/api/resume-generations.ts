import type { ResumeData } from '@/components/dashboard/resume-component';
import { apiFetch, apiPost } from './client';

export type ResumeGenerationMode = 'auto' | 'llm' | 'deterministic';
export type CoverageImportance = 'must' | 'should' | 'nice';

export interface ResumeGenerationConstraints {
  page_count?: 1 | 2;
  max_work_experiences?: number;
  max_project_experiences?: number;
  max_bullets_per_experience?: number;
  top_k_per_task?: number;
  max_search_rounds?: number;
  min_coverage_ratio?: number;
}

export interface PlannedExperience {
  experience_id: number;
  section: 'workExperience' | 'personalProjects';
  evidence_ids: number[];
  coverage_item_ids: string[];
  bullet_budget: number;
  score: number;
  reason: string;
}

export interface PromotedSkill {
  skill: string;
  evidence_ids: number[];
  coverage_item_ids: string[];
  reason: string;
}

export interface ResumePlan {
  version: '1';
  selected_experiences: PlannedExperience[];
  promoted_skills: PromotedSkill[];
  coverage: Array<{
    coverage_id: string;
    importance: CoverageImportance;
    covered: boolean;
    evidence_ids: number[];
  }>;
  uncovered_requirements: string[];
  omitted_candidates: Array<{
    experience_id: number;
    evidence_ids: number[];
    reason: string;
  }>;
  search_rounds: number;
  coverage_ratio: number;
  review_actions: string[];
  review_warnings: string[];
}

export interface ResumeGenerationPreview {
  run_id: string;
  status: 'previewed';
  plan: ResumePlan;
  resume_data: ResumeData;
  provenance: {
    bullets: Array<{
      section: 'workExperience' | 'personalProjects';
      item_id: number;
      bullet_index: number;
      evidence_ids: number[];
    }>;
    skills: Array<{ skill: string; evidence_ids: number[] }>;
  };
  validation: {
    valid: boolean;
    coverage_ratio: number;
    uncovered_requirements: string[];
    warnings: string[];
    errors: string[];
  };
}

export interface ResumeGenerationRun {
  run_id: string;
  status: 'running' | 'previewed' | 'failed' | 'confirmed';
  jd_information_id: number;
  request: {
    jd_information_id: number;
    mode: ResumeGenerationMode;
    constraints: ResumeGenerationConstraints;
  };
  plan: ResumePlan | null;
  resume_data: ResumeData | null;
  provenance: ResumeGenerationPreview['provenance'] | null;
  validation: ResumeGenerationPreview['validation'] | null;
  resume_id: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
}

async function parseJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(payload?.detail || `Resume generation request failed (${response.status})`);
  }
  return (await response.json()) as T;
}

export async function previewResumeGeneration(input: {
  jd_information_id: number;
  mode?: ResumeGenerationMode;
  constraints?: ResumeGenerationConstraints;
}): Promise<ResumeGenerationPreview> {
  return parseJson(await apiPost('/resume-generations/preview', input));
}

export async function getResumeGeneration(runId: string): Promise<ResumeGenerationRun> {
  return parseJson(await apiFetch(`/resume-generations/${encodeURIComponent(runId)}`));
}

export async function confirmResumeGeneration(
  runId: string,
  title?: string
): Promise<{ run_id: string; status: 'confirmed'; resume_id: string }> {
  return parseJson(
    await apiPost(`/resume-generations/${encodeURIComponent(runId)}/confirm`, { title })
  );
}
