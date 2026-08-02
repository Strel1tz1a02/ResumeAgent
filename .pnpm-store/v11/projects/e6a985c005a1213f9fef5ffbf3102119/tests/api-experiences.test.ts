import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  archiveExperience,
  createEvidence,
  createExperience,
  deleteEvidence,
  deleteExperiencePermanently,
  fetchExperience,
  getDeletionImpact,
  importExperienceText,
  listExperiences,
  markExperienceReady,
  patchEvidence,
  patchExperience,
  saveExperience,
  restoreExperience,
  reorderEvidence,
  type ExperienceDetail,
} from '@/lib/api/experiences';

const experience: ExperienceDetail = {
  experience_id: 7,
  kind: 'project',
  title: 'Agent platform',
  organization: 'Acme',
  role: 'Engineer',
  location: null,
  start_date: '2025-01',
  end_date: null,
  is_current: true,
  background: null,
  evidence_ids: [3],
  technologies: ['TypeScript'],
  tags: ['AI'],
  notes: null,
  status: 'draft',
  completeness: 42,
  archived_at: null,
  created_at: '2025-01-01T00:00:00Z',
  updated_at: '2025-01-01T00:00:00Z',
  evidence_items: [],
  missing_dimensions: [],
  suggested_questions: [],
  field_states: [],
};

describe('experience API client', () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi
      .fn()
      .mockImplementation(() =>
        Promise.resolve(new Response(JSON.stringify(experience), { status: 200 }))
      );
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  const latestCall = (): [string, RequestInit] => {
    const [url, options] = fetchMock.mock.calls.at(-1)!;
    return [String(url), options as RequestInit];
  };

  it('lists with encoded stable filters and returns the response items', async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ items: [experience], total: 1 }), { status: 200 })
    );

    await expect(
      listExperiences({ q: 'agent & ML', kind: 'project', status: 'ready', sort: 'created_at_asc' })
    ).resolves.toEqual({ items: [experience], total: 1 });

    const [url, options] = latestCall();
    expect(url).toBe(
      '/api/v1/experiences?q=agent+%26+ML&kind=project&status=ready&sort=created_at_asc'
    );
    expect(options.method).toBeUndefined();
  });

  it('forwards caller cancellation to list, detail, and deletion-impact reads', async () => {
    const controller = new AbortController();
    controller.abort('obsolete query');
    fetchMock
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ items: [experience], total: 1 }), { status: 200 })
      )
      .mockResolvedValueOnce(new Response(JSON.stringify(experience), { status: 200 }))
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ affected_matches: [], affected_resumes: [] }), {
          status: 200,
        })
      );

    await listExperiences({ status: 'active' }, controller.signal);
    await fetchExperience(7, controller.signal);
    await getDeletionImpact(7, controller.signal);

    expect(
      fetchMock.mock.calls.map(([, options]) => (options as RequestInit).signal?.aborted)
    ).toEqual([true, true, true]);
  });

  it('imports exact raw text through the central API path', async () => {
    await expect(importExperienceText('Built an agent')).resolves.toEqual(experience);

    const [url, options] = latestCall();
    expect(url).toBe('/api/v1/experiences/import-text');
    expect(options.method).toBe('POST');
    expect(options.body).toBe(JSON.stringify({ text: 'Built an agent' }));
  });

  it('uses typed CRUD and evidence request bodies', async () => {
    await createExperience({ kind: 'work', title: 'Developer', background: 'Worked' });
    await patchExperience(7, { title: 'Staff Developer' });
    await saveExperience(7, {
      experience: { title: 'Staff Developer', expected_field_revisions: { title: 2 } },
      evidence_items: [],
      new_evidence: null,
      expected_collection_revision: 1,
    });
    await createEvidence(7, { action: 'Built API', result: 'Launched', metrics: '20%' });
    await patchEvidence(7, 3, { metrics: '30%' });
    await reorderEvidence(7, [3, 4]);
    await deleteEvidence(7, 3);

    const calls = fetchMock.mock.calls.map(([url, options]) => ({
      url: String(url),
      method: (options as RequestInit).method,
      body: (options as RequestInit).body,
    }));
    expect(calls).toMatchObject([
      {
        url: '/api/v1/experiences',
        method: 'POST',
        body: JSON.stringify({ kind: 'work', title: 'Developer', background: 'Worked' }),
      },
      {
        url: '/api/v1/experiences/7',
        method: 'PATCH',
        body: JSON.stringify({ title: 'Staff Developer' }),
      },
      {
        url: '/api/v1/experiences/7/save',
        method: 'PUT',
        body: JSON.stringify({
          experience: { title: 'Staff Developer', expected_field_revisions: { title: 2 } },
          evidence_items: [],
          new_evidence: null,
          expected_collection_revision: 1,
        }),
      },
      {
        url: '/api/v1/experiences/7/evidence',
        method: 'POST',
        body: JSON.stringify({ action: 'Built API', result: 'Launched', metrics: '20%' }),
      },
      {
        url: '/api/v1/experiences/7/evidence/3',
        method: 'PATCH',
        body: JSON.stringify({ metrics: '30%' }),
      },
      {
        url: '/api/v1/experiences/7/evidence-order',
        method: 'PUT',
        body: JSON.stringify({ evidence_ids: [3, 4] }),
      },
      { url: '/api/v1/experiences/7/evidence/3', method: 'DELETE' },
    ]);
  });

  it('uses fetch, lifecycle, and deletion endpoints', async () => {
    await fetchExperience(7);
    await markExperienceReady(7);
    await archiveExperience(7);
    await restoreExperience(7);
    fetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          affected_matches: [{ match_id: 2, job_title: 'AI Engineer' }],
          affected_resumes: ['resume-1'],
        }),
        { status: 200 }
      )
    );
    await getDeletionImpact(7);
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));
    await deleteExperiencePermanently(7);

    expect(
      fetchMock.mock.calls.map(([url, options]) => [String(url), (options as RequestInit).method])
    ).toEqual([
      ['/api/v1/experiences/7', undefined],
      ['/api/v1/experiences/7/mark-ready', 'POST'],
      ['/api/v1/experiences/7/archive', 'POST'],
      ['/api/v1/experiences/7/restore', 'POST'],
      ['/api/v1/experiences/7/deletion-impact', undefined],
      ['/api/v1/experiences/7/permanent', 'DELETE'],
    ]);
  });

  it('surfaces backend error detail instead of a generic JSON error', async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: [{ msg: 'text must not be blank' }] }), { status: 422 })
    );

    await expect(importExperienceText('')).rejects.toThrow('text must not be blank');
  });
});
