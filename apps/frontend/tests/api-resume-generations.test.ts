import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { confirmResumeGeneration, previewResumeGeneration } from '@/lib/api/resume-generations';

describe('resume generation API client', () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => vi.unstubAllGlobals());

  it('posts JD, mode and constraints to the preview endpoint', async () => {
    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify({
          run_id: 'run-1',
          status: 'previewed',
          plan: {},
          resume_data: {},
          provenance: {},
          validation: {},
        })
      )
    );

    await previewResumeGeneration({
      jd_information_id: 7,
      mode: 'deterministic',
      constraints: { page_count: 1 },
    });

    const [url, options] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain('/api/v1/resume-generations/preview');
    expect(options.method).toBe('POST');
    expect(JSON.parse(String(options.body))).toEqual({
      jd_information_id: 7,
      mode: 'deterministic',
      constraints: { page_count: 1 },
    });
  });

  it('confirms the same persisted run instead of resending ResumeData', async () => {
    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify({
          run_id: 'run-1',
          status: 'confirmed',
          resume_id: 'resume-generation-run-1',
        })
      )
    );

    await expect(confirmResumeGeneration('run-1', 'Example - Engineer')).resolves.toEqual({
      run_id: 'run-1',
      status: 'confirmed',
      resume_id: 'resume-generation-run-1',
    });
    const [url, options] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain('/resume-generations/run-1/confirm');
    expect(JSON.parse(String(options.body))).toEqual({ title: 'Example - Engineer' });
  });

  it('surfaces backend domain errors', async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ detail: 'no ready experiences' }), { status: 422 })
    );

    await expect(
      previewResumeGeneration({ jd_information_id: 7, mode: 'deterministic' })
    ).rejects.toThrow('no ready experiences');
  });
});
