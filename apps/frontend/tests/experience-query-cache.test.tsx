import { QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import type { PropsWithChildren } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ExperienceDetail, ExperienceListResponse } from '@/lib/api/experiences';
import { createExperienceQueryClient } from '@/lib/queries/experiences/provider';
import { experienceKeys } from '@/lib/queries/experiences/keys';
import {
  removeExperienceFromCache,
  writeExperienceDetail,
} from '@/lib/queries/experiences/cache';
import { useExperienceDetail } from '@/lib/queries/experiences/queries';

const api = vi.hoisted(() => ({
  fetchExperience: vi.fn(),
}));

vi.mock('@/lib/api/experiences', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api/experiences')>()),
  fetchExperience: api.fetchExperience,
}));

const detail = (overrides: Partial<ExperienceDetail> = {}): ExperienceDetail => ({
  experience_id: 1,
  kind: 'project',
  title: 'Search service',
  organization: 'Acme',
  role: 'Engineer',
  location: null,
  start_date: '2025-01',
  end_date: null,
  is_current: true,
  raw_input: 'Built search',
  background: null,
  evidence_ids: [],
  technologies: ['TypeScript'],
  tags: ['search'],
  notes: null,
  status: 'draft',
  completeness: 40,
  archived_at: null,
  created_at: '2025-01-01T00:00:00Z',
  updated_at: '2025-01-02T00:00:00Z',
  evidence_items: [],
  missing_dimensions: ['result'],
  suggested_questions: ['What changed?'],
  ...overrides,
});

const list = (items: ExperienceDetail[]): ExperienceListResponse => ({
  items,
  total: items.length,
});

describe('experience query cache', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('uses canonical keys that isolate view, detail, and deletion impact', () => {
    expect(experienceKeys.all).toEqual(['experiences']);
    expect(experienceKeys.lists()).toEqual(['experiences', 'list']);
    expect(experienceKeys.list('active')).toEqual(['experiences', 'list', 'active']);
    expect(experienceKeys.list('archived')).toEqual(['experiences', 'list', 'archived']);
    expect(experienceKeys.detail(7)).toEqual(['experiences', 'detail', 7]);
    expect(experienceKeys.deletionImpact(7)).toEqual([
      'experiences',
      'deletion-impact',
      7,
    ]);
  });

  it('moves an authoritative detail between status lists without accepting an older version', () => {
    const client = createExperienceQueryClient();
    const newer = detail({ title: 'New title', updated_at: '2025-01-04T00:00:00Z' });
    client.setQueryData(experienceKeys.list('active'), list([detail()]));
    client.setQueryData(experienceKeys.list('archived'), list([]));

    writeExperienceDetail(client, newer);
    writeExperienceDetail(
      client,
      detail({ title: 'Late old title', updated_at: '2025-01-03T00:00:00Z' })
    );

    expect(client.getQueryData<ExperienceDetail>(experienceKeys.detail(1))?.title).toBe('New title');
    expect(client.getQueryData<ExperienceListResponse>(experienceKeys.list('active'))?.items[0].title).toBe(
      'New title'
    );

    const archived = detail({
      status: 'archived',
      title: 'Archived title',
      archived_at: '2025-01-05T00:00:00Z',
      updated_at: '2025-01-05T00:00:00Z',
    });
    writeExperienceDetail(client, archived);

    expect(client.getQueryData<ExperienceListResponse>(experienceKeys.list('active'))?.items).toEqual(
      []
    );
    expect(client.getQueryData<ExperienceListResponse>(experienceKeys.list('archived'))?.items[0].title).toBe(
      'Archived title'
    );
  });

  it('uses the newest detail when a late response fills a previously empty list cache', () => {
    const client = createExperienceQueryClient();
    const newest = detail({ title: 'Newest title', updated_at: '2025-01-05T00:00:00Z' });
    client.setQueryData(experienceKeys.detail(1), newest);
    client.setQueryData(experienceKeys.list('active'), list([]));

    writeExperienceDetail(
      client,
      detail({ title: 'Late old title', updated_at: '2025-01-03T00:00:00Z' })
    );

    expect(client.getQueryData<ExperienceListResponse>(experienceKeys.list('active'))?.items[0].title).toBe(
      'Newest title'
    );
  });

  it('removes every cache entry owned by a permanently deleted experience', () => {
    const client = createExperienceQueryClient();
    client.setQueryData(experienceKeys.list('active'), list([detail()]));
    client.setQueryData(experienceKeys.list('archived'), list([]));
    client.setQueryData(experienceKeys.detail(1), detail());
    client.setQueryData(experienceKeys.deletionImpact(1), {
      affected_matches: [],
      affected_resumes: [],
    });

    removeExperienceFromCache(client, 1);

    expect(client.getQueryData<ExperienceListResponse>(experienceKeys.list('active'))?.items).toEqual(
      []
    );
    expect(client.getQueryData(experienceKeys.detail(1))).toBeUndefined();
    expect(client.getQueryData(experienceKeys.deletionImpact(1))).toBeUndefined();
  });

  it('aborts an obsolete detail read and exposes only the newly selected detail', async () => {
    let firstSignal: AbortSignal | undefined;
    api.fetchExperience.mockImplementation((experienceId: number, signal?: AbortSignal) => {
      if (experienceId === 1) {
        firstSignal = signal;
        return new Promise<ExperienceDetail>((_resolve, reject) => {
          signal?.addEventListener('abort', () => {
            const error = new Error('obsolete');
            error.name = 'AbortError';
            reject(error);
          });
        });
      }
      return Promise.resolve(detail({ experience_id: 2, title: 'Selected B' }));
    });
    const client = createExperienceQueryClient();
    const wrapper = ({ children }: PropsWithChildren) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );

    const { result, rerender } = renderHook(({ id }) => useExperienceDetail(id), {
      initialProps: { id: 1 as number | null },
      wrapper,
    });
    rerender({ id: 2 });

    await waitFor(() => expect(result.current.data?.title).toBe('Selected B'));
    expect(firstSignal?.aborted).toBe(true);
    expect(result.current.data?.experience_id).toBe(2);
  });
});
