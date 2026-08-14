import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({
  listJDImports: vi.fn(),
  updateJDImport: vi.fn(),
  deleteJDImport: vi.fn(),
  addJDRequirement: vi.fn(),
  updateJDRequirement: vi.fn(),
  deleteJDRequirement: vi.fn(),
  createJDConversation: vi.fn(),
  streamJDImport: vi.fn(),
  resolveJDQuestions: vi.fn(),
}));

vi.mock('@/lib/api/jd-imports', () => api);
vi.mock('@/lib/i18n', () => ({
  useTranslations: () => ({
    t: (key: string, params?: Record<string, string>) =>
      params
        ? Object.entries(params).reduce(
            (value, [name, replacement]) => value.replace(`{${name}}`, replacement),
            key
          )
        : key,
  }),
}));

import { JDImportWorkspace } from '@/components/jd-imports/jd-import-workspace';

const savedJD = {
  id: 3,
  source_url: 'https://example.com/job',
  company: 'Acme',
  job_name: 'Backend Engineer',
  type: 'Full-time',
  location: 'Shanghai',
  status: 'incomplete' as const,
  revision: 5,
  requirements: [],
};

describe('JDImportWorkspace', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.listJDImports.mockResolvedValue({ items: [savedJD], total: 1 });
    api.updateJDImport.mockResolvedValue({ ...savedJD, company: 'OpenAI', revision: 6 });
  });

  it('loads the JD library and saves metadata with the selected record revision', async () => {
    render(<JDImportWorkspace />);

    expect(screen.getByRole('tab', { name: 'jdImports.tabs.library' })).toHaveAttribute(
      'aria-selected',
      'true'
    );

    expect(await screen.findByText('Backend Engineer')).toBeInTheDocument();
    const company = screen.getByLabelText('jdImports.fields.company');
    fireEvent.change(company, { target: { value: 'OpenAI' } });
    fireEvent.click(screen.getByRole('button', { name: 'jdImports.library.saveMetadata' }));

    await waitFor(() =>
      expect(api.updateJDImport).toHaveBeenCalledWith(
        3,
        expect.objectContaining({ company: 'OpenAI', source_url: 'https://example.com/job' }),
        5
      )
    );
  });
});
