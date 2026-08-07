import { QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react';
import type { PropsWithChildren } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ExperienceDetail, ExperienceListResponse } from '@/lib/api/experiences';
import { createExperienceQueryClient } from '@/lib/queries/experiences/provider';
import { experienceKeys } from '@/lib/queries/experiences/keys';
import { removeExperienceFromCache, writeExperienceDetail } from '@/lib/queries/experiences/cache';
import { useExperienceDetail } from '@/lib/queries/experiences/queries';
import {
  useCreateEvidenceMutation,
  useCreateExperienceMutation,
  useExperienceCreationPending,
  useImportExperienceMutation,
  usePatchExperienceMutation,
} from '@/lib/queries/experiences/mutations';

const api = vi.hoisted(() => ({
  fetchExperience: vi.fn(),
  patchExperience: vi.fn(),
  createEvidence: vi.fn(),
  createExperience: vi.fn(),
  importExperienceText: vi.fn(),
}));

vi.mock('@/lib/api/experiences', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api/experiences')>()),
  fetchExperience: api.fetchExperience,
  patchExperience: api.patchExperience,
  createEvidence: api.createEvidence,
  createExperience: api.createExperience,
  importExperienceText: api.importExperienceText,
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
  field_states: [],
  ...overrides,
});

const list = (items: ExperienceDetail[]): ExperienceListResponse => ({
  items,
  total: items.length,
});

const backgroundRevision = (revision: number) => [
  { key: 'background', ref_id: null, status: 'complete' as const, revision },
];

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function queryWrapper(client = createExperienceQueryClient()) {
  return {
    client,
    wrapper: ({ children }: PropsWithChildren) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  };
}

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
    expect(experienceKeys.deletionImpact(7)).toEqual(['experiences', 'deletion-impact', 7]);
  });

  it('moves an authoritative detail between status lists without accepting a stale revision', () => {
    const client = createExperienceQueryClient();
    const newer = detail({
      title: 'New title',
      updated_at: '2025-01-03T00:00:00Z',
      field_states: backgroundRevision(2),
    });
    client.setQueryData(experienceKeys.list('active'), list([detail()]));
    client.setQueryData(experienceKeys.list('archived'), list([]));

    writeExperienceDetail(client, newer);
    writeExperienceDetail(
      client,
      detail({
        title: 'Late old title',
        updated_at: '2025-01-05T00:00:00Z',
        field_states: backgroundRevision(1),
      })
    );

    expect(client.getQueryData<ExperienceDetail>(experienceKeys.detail(1))?.title).toBe(
      'New title'
    );
    expect(
      client.getQueryData<ExperienceListResponse>(experienceKeys.list('active'))?.items[0].title
    ).toBe('New title');

    const archived = detail({
      status: 'archived',
      title: 'Archived title',
      archived_at: '2025-01-05T00:00:00Z',
      updated_at: '2025-01-05T00:00:00Z',
    });
    writeExperienceDetail(client, archived);

    expect(
      client.getQueryData<ExperienceListResponse>(experienceKeys.list('active'))?.items
    ).toEqual([]);
    expect(
      client.getQueryData<ExperienceListResponse>(experienceKeys.list('archived'))?.items[0].title
    ).toBe('Archived title');
  });

  it('uses the highest-revision detail when a late response fills an empty list cache', () => {
    const client = createExperienceQueryClient();
    const newest = detail({
      title: 'Newest title',
      updated_at: '2025-01-03T00:00:00Z',
      field_states: backgroundRevision(2),
    });
    client.setQueryData(experienceKeys.detail(1), newest);
    client.setQueryData(experienceKeys.list('active'), list([]));

    writeExperienceDetail(
      client,
      detail({
        title: 'Late old title',
        updated_at: '2025-01-05T00:00:00Z',
        field_states: backgroundRevision(1),
      })
    );

    expect(
      client.getQueryData<ExperienceListResponse>(experienceKeys.list('active'))?.items[0].title
    ).toBe('Newest title');
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

    expect(
      client.getQueryData<ExperienceListResponse>(experienceKeys.list('active'))?.items
    ).toEqual([]);
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

  it('serializes different writes for the same experience', async () => {
    const metadata = deferred<ExperienceDetail>();
    api.patchExperience.mockReturnValue(metadata.promise);
    api.createEvidence.mockResolvedValue(detail({ updated_at: '2025-01-04T00:00:00Z' }));
    const { wrapper } = queryWrapper();
    const { result } = renderHook(
      () => ({
        patch: usePatchExperienceMutation(1),
        evidence: useCreateEvidenceMutation(1),
      }),
      { wrapper }
    );

    act(() => {
      result.current.patch.mutate({ title: 'Queued metadata' });
      result.current.evidence.mutate({
        action: 'Queued evidence',
        expected_collection_revision: 0,
      });
    });

    await waitFor(() => expect(api.patchExperience).toHaveBeenCalledTimes(1));
    expect(api.createEvidence).not.toHaveBeenCalled();

    await act(async () => {
      metadata.resolve(detail({ title: 'Queued metadata', updated_at: '2025-01-03T00:00:00Z' }));
      await metadata.promise;
    });
    await waitFor(() => expect(api.createEvidence).toHaveBeenCalledTimes(1));
  });

  it('allows writes for different experiences to run independently', async () => {
    const first = deferred<ExperienceDetail>();
    const second = deferred<ExperienceDetail>();
    api.patchExperience.mockImplementation((experienceId: number) =>
      experienceId === 1 ? first.promise : second.promise
    );
    const { wrapper } = queryWrapper();
    const { result } = renderHook(
      () => ({
        first: usePatchExperienceMutation(1),
        second: usePatchExperienceMutation(2),
      }),
      { wrapper }
    );

    act(() => {
      result.current.first.mutate({ title: 'First' });
      result.current.second.mutate({ title: 'Second' });
    });

    await waitFor(() => expect(api.patchExperience).toHaveBeenCalledTimes(2));
    first.resolve(detail({ title: 'First' }));
    second.resolve(detail({ experience_id: 2, title: 'Second' }));
  });

  it('does not cancel another experience detail read when a mutation succeeds', async () => {
    const pendingOtherDetail = deferred<ExperienceDetail>();
    let otherSignal: AbortSignal | undefined;
    api.fetchExperience.mockImplementation((experienceId: number, signal?: AbortSignal) => {
      expect(experienceId).toBe(2);
      otherSignal = signal;
      return pendingOtherDetail.promise;
    });
    api.patchExperience.mockResolvedValue(
      detail({ title: 'Saved A', updated_at: '2025-01-06T00:00:00Z' })
    );
    const { wrapper } = queryWrapper();
    const { result } = renderHook(
      () => ({
        otherDetail: useExperienceDetail(2),
        patchA: usePatchExperienceMutation(1),
      }),
      { wrapper }
    );

    await waitFor(() => expect(api.fetchExperience).toHaveBeenCalledTimes(1));
    await act(async () => {
      await result.current.patchA.mutateAsync({ title: 'Saved A' });
    });

    expect(otherSignal?.aborted).toBe(false);
    pendingOtherDetail.resolve(detail({ experience_id: 2, title: 'Loaded B' }));
    await waitFor(() => expect(result.current.otherDetail.data?.title).toBe('Loaded B'));
  });

  it('writes an authoritative mutation response into detail and list caches without refetching', async () => {
    const updated = detail({ title: 'Saved title', updated_at: '2025-01-06T00:00:00Z' });
    api.patchExperience.mockResolvedValue(updated);
    const { client, wrapper } = queryWrapper();
    client.setQueryData(experienceKeys.list('active'), list([detail()]));
    const { result } = renderHook(() => usePatchExperienceMutation(1), { wrapper });

    await act(async () => {
      await result.current.mutateAsync({ title: 'Saved title' });
    });

    expect(client.getQueryData<ExperienceDetail>(experienceKeys.detail(1))?.title).toBe(
      'Saved title'
    );
    expect(
      client.getQueryData<ExperienceListResponse>(experienceKeys.list('active'))?.items[0].title
    ).toBe('Saved title');
    expect(api.fetchExperience).not.toHaveBeenCalled();
  });

  it('shares one creation queue and pending state between manual create and text import', async () => {
    const manual = deferred<ExperienceDetail>();
    api.createExperience.mockReturnValue(manual.promise);
    api.importExperienceText.mockResolvedValue(
      detail({ experience_id: 2, title: 'Imported', background: 'Imported text' })
    );
    const { wrapper } = queryWrapper();
    const { result } = renderHook(
      () => ({
        create: useCreateExperienceMutation(),
        importText: useImportExperienceMutation(),
        creationPending: useExperienceCreationPending(),
      }),
      { wrapper }
    );

    act(() => {
      result.current.create.mutate({});
      result.current.importText.mutate('Imported text');
    });

    await waitFor(() => expect(result.current.creationPending).toBe(true));
    expect(api.importExperienceText).not.toHaveBeenCalled();
    manual.resolve(detail());
    await waitFor(() => expect(api.importExperienceText).toHaveBeenCalledWith('Imported text'));
  });
});
