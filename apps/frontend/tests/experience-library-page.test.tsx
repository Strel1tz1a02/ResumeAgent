import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ExperienceDetail, ExperienceRead } from '@/lib/api/experiences';

const api = vi.hoisted(() => ({
  listExperiences: vi.fn(),
  importExperienceText: vi.fn(),
  requestNextExperienceQuestion: vi.fn(),
}));

const translate = vi.hoisted(
  () => (key: string) =>
    ({
      'experiences.title': 'Experience library',
      'experiences.search': 'Search experiences',
      'experiences.kind': 'Kind',
      'experiences.kind.all': 'All kinds',
      'experiences.kind.project': 'Project',
      'experiences.import': 'Import',
      'experiences.import.title': 'Import experience',
      'experiences.import.description': 'Paste your experience notes.',
      'experiences.import.text': 'Experience text',
      'experiences.import.submit': 'Save import',
      'experiences.import.cancel': 'Cancel',
      'experiences.loading': 'Loading experiences',
      'experiences.empty': 'No experiences',
      'experiences.noResults': 'No matching experiences',
      'experiences.retry': 'Retry',
      'experiences.refresh': 'Refresh',
      'experiences.error': 'Could not load experiences',
      'experiences.rawInput': 'Raw input',
    })[key] ?? key
);

vi.mock('@/lib/api/experiences', () => api);

vi.mock('@/lib/i18n', () => ({
  useTranslations: () => ({ t: translate }),
}));

import { ExperienceLibraryPage } from '@/components/experiences/experience-library-page';

const listItem: ExperienceRead = {
  experience_id: 1,
  kind: 'project',
  title: 'Searchable project',
  organization: 'Acme',
  role: 'Engineer',
  location: null,
  start_date: null,
  end_date: null,
  is_current: false,
  raw_input: 'Initial note',
  background: null,
  evidence_ids: [],
  technologies: ['TypeScript'],
  tags: ['search'],
  notes: null,
  status: 'draft',
  completeness: 10,
  archived_at: null,
  created_at: '2025-01-01T00:00:00Z',
  updated_at: '2025-01-02T00:00:00Z',
};

const imported: ExperienceDetail = {
  ...listItem,
  experience_id: 9,
  title: 'Imported experience',
  raw_input: 'Exactly this valid text',
  evidence_items: [],
  missing_dimensions: ['evidence'],
  suggested_questions: ['What changed?'],
};

describe('ExperienceLibraryPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.listExperiences.mockResolvedValue({ items: [listItem], total: 1 });
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it('shows a loading state before the experience list resolves', () => {
    api.listExperiences.mockReturnValue(new Promise(() => {}));

    render(<ExperienceLibraryPage />);

    expect(screen.getByText('Loading experiences')).toBeInTheDocument();
  });

  it('offers retry after a list failure and loads the retry result', async () => {
    api.listExperiences
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValueOnce({ items: [listItem], total: 1 });

    render(<ExperienceLibraryPage />);

    expect(await screen.findByText('Could not load experiences')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByRole('button', { name: /Searchable project/ })).toBeInTheDocument();
  });

  it('renders an empty state when the library has no experiences', async () => {
    api.listExperiences.mockResolvedValueOnce({ items: [], total: 0 });
    render(<ExperienceLibraryPage />);
    expect(await screen.findAllByText('No experiences')).toHaveLength(2);
  });

  it('filters the active list by query and kind', async () => {
    const volunteer = {
      ...listItem,
      experience_id: 2,
      kind: 'volunteer' as const,
      title: 'Volunteer event',
    };
    api.listExperiences.mockResolvedValueOnce({ items: [listItem, volunteer], total: 2 });
    render(<ExperienceLibraryPage />);

    expect(await screen.findByRole('button', { name: /Searchable project/ })).toBeInTheDocument();
    expect(screen.getByText('Volunteer event')).toBeInTheDocument();
    fireEvent.change(screen.getByRole('textbox', { name: 'Search experiences' }), {
      target: { value: 'searchable' },
    });
    expect(screen.getByRole('button', { name: /Searchable project/ })).toBeInTheDocument();
    fireEvent.change(screen.getByRole('textbox', { name: 'Search experiences' }), {
      target: { value: 'other' },
    });
    expect(screen.getByText('No matching experiences')).toBeInTheDocument();
    fireEvent.change(screen.getByRole('textbox', { name: 'Search experiences' }), {
      target: { value: '' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Kind' }));
    fireEvent.click(screen.getByRole('menuitemradio', { name: 'Project' }));
    expect(screen.getByRole('button', { name: /Searchable project/ })).toBeInTheDocument();
    expect(screen.queryByText('Volunteer event')).not.toBeInTheDocument();
  });

  it('persists exact pasted text, selects the returned draft, and does not start questions', async () => {
    api.importExperienceText.mockResolvedValue(imported);
    render(<ExperienceLibraryPage />);
    await screen.findByRole('button', { name: /Searchable project/ });

    fireEvent.click(screen.getByRole('button', { name: 'Import' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Experience text' }), {
      target: { value: 'Exactly this valid text' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save import' }));

    await waitFor(() => {
      expect(api.importExperienceText).toHaveBeenCalledWith('Exactly this valid text');
    });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.getAllByText('Imported experience')).toHaveLength(2);
    expect(screen.getByText('Exactly this valid text')).toBeInTheDocument();
    expect(api.requestNextExperienceQuestion).not.toHaveBeenCalled();
  });

  it('keeps the persisted import selected when a later reload fails', async () => {
    api.importExperienceText.mockResolvedValue(imported);
    api.listExperiences
      .mockResolvedValueOnce({ items: [listItem], total: 1 })
      .mockRejectedValueOnce(new Error('offline'));
    render(<ExperienceLibraryPage />);
    await screen.findByRole('button', { name: /Searchable project/ });

    fireEvent.click(screen.getByRole('button', { name: 'Import' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Experience text' }), {
      target: { value: 'Exactly this valid text' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save import' }));
    await screen.findAllByText('Imported experience');

    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
    expect(await screen.findByText('Could not load experiences')).toBeInTheDocument();
    expect(screen.getAllByText('Imported experience')).toHaveLength(2);
    expect(screen.getByText('Exactly this valid text')).toBeInTheDocument();
  });
});
