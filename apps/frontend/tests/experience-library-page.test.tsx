import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ExperienceDetail, ExperienceRead } from '@/lib/api/experiences';

const api = vi.hoisted(() => ({
  listExperiences: vi.fn(),
  fetchExperience: vi.fn(),
  importExperienceText: vi.fn(),
  patchExperience: vi.fn(),
  createEvidence: vi.fn(),
  patchEvidence: vi.fn(),
  deleteEvidence: vi.fn(),
  reorderEvidence: vi.fn(),
  markExperienceReady: vi.fn(),
  archiveExperience: vi.fn(),
  restoreExperience: vi.fn(),
  getDeletionImpact: vi.fn(),
  deleteExperiencePermanently: vi.fn(),
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
      'experiences.kind.volunteer': 'Volunteer',
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
      'experiences.backToList': 'Back to list',
      'experiences.status.draft': 'Draft status',
      'experiences.editor.titleField': 'Title',
      'experiences.editor.is_current': 'Current experience',
      'experiences.editor.save': 'Save experience',
      'experiences.completeness.score': '72% complete',
      'experiences.active': 'Active',
      'experiences.archive': 'Recycle bin',
      'experiences.lifecycle.archive': 'Archive experience',
      'experiences.lifecycle.restore': 'Restore experience',
      'experiences.lifecycle.permanent': 'Delete permanently',
      'experiences.evidence.action': 'Action',
      'experiences.evidence.result': 'Result',
      'experiences.evidence.metrics': 'Metrics',
      'experiences.evidence.add': 'Add evidence',
      'experiences.evidence.save': 'Save evidence',
      'experiences.evidence.moveUp': 'Move evidence up',
      'experiences.evidence.moveDown': 'Move evidence down',
      'experiences.evidence.delete': 'Delete evidence',
      'experiences.unsaved.title': 'Discard unsaved changes?',
      'experiences.unsaved.description': 'Your edits will be lost.',
      'experiences.unsaved.discard': 'Discard edits',
      'experiences.ready.action': 'Mark ready',
      'experiences.ready.conflict': 'Not ready',
      'experiences.permanent.title': 'Delete permanently?',
      'experiences.permanent.description': 'This cannot be undone.',
      'experiences.permanent.loadingImpact': 'Loading impact',
      'experiences.permanent.affectedMatches': '1 affected matches',
      'experiences.permanent.affectedResumes': '1 affected resumes',
      'experiences.permanent.confirm': 'Delete permanently',
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

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe('ExperienceLibraryPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.listExperiences.mockResolvedValue({ items: [listItem], total: 1 });
    api.fetchExperience.mockResolvedValue({
      ...listItem,
      evidence_items: [],
      missing_dimensions: ['evidence'],
      suggested_questions: ['What changed?'],
    });
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

  it('finds experiences from raw input and technologies', async () => {
    const rawOnly = {
      ...listItem,
      experience_id: 2,
      title: 'Untitled note',
      raw_input: 'Migrated a legacy warehouse',
      technologies: [],
      tags: [],
    };
    const technologyOnly = {
      ...listItem,
      experience_id: 3,
      title: 'Implementation',
      raw_input: '',
      technologies: ['Rust'],
      tags: [],
    };
    api.listExperiences.mockResolvedValueOnce({ items: [rawOnly, technologyOnly], total: 2 });
    render(<ExperienceLibraryPage />);
    await screen.findByRole('button', { name: /Untitled note/ });

    fireEvent.change(screen.getByRole('textbox', { name: 'Search experiences' }), {
      target: { value: 'legacy warehouse' },
    });
    expect(screen.getByRole('button', { name: /Untitled note/ })).toBeInTheDocument();

    fireEvent.change(screen.getByRole('textbox', { name: 'Search experiences' }), {
      target: { value: 'rust' },
    });
    expect(screen.getByRole('button', { name: /Implementation/ })).toBeInTheDocument();
  });

  it('keeps an imported draft when an older initial list request resolves later', async () => {
    const initialList = deferred<{ items: ExperienceRead[]; total: number }>();
    api.listExperiences.mockReturnValueOnce(initialList.promise);
    api.importExperienceText.mockResolvedValue(imported);
    render(<ExperienceLibraryPage />);

    fireEvent.click(screen.getByRole('button', { name: 'Import' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Experience text' }), {
      target: { value: 'Exactly this valid text' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save import' }));
    await screen.findByRole('heading', { name: 'Imported experience' });

    initialList.resolve({ items: [listItem], total: 1 });
    await waitFor(() => expect(api.listExperiences).toHaveBeenCalledTimes(1));
    expect(screen.getByRole('heading', { name: 'Imported experience' })).toBeInTheDocument();
    expect(screen.getAllByText('Exactly this valid text').length).toBeGreaterThan(0);
  });

  it('reselects the first visible item when a filter hides the selected detail', async () => {
    const volunteer = {
      ...listItem,
      experience_id: 2,
      kind: 'volunteer' as const,
      title: 'Volunteer event',
    };
    api.listExperiences.mockResolvedValueOnce({ items: [listItem, volunteer], total: 2 });
    render(<ExperienceLibraryPage />);
    await screen.findByRole('heading', { name: 'Searchable project' });

    fireEvent.change(screen.getByRole('textbox', { name: 'Search experiences' }), {
      target: { value: 'volunteer' },
    });
    expect(screen.getByRole('heading', { name: 'Volunteer event' })).toBeInTheDocument();
  });

  it('clears incompatible filters and opens the imported draft detail pane', async () => {
    api.importExperienceText.mockResolvedValue(imported);
    render(<ExperienceLibraryPage />);
    await screen.findByRole('button', { name: /Searchable project/ });
    fireEvent.change(screen.getByRole('textbox', { name: 'Search experiences' }), {
      target: { value: 'only-existing-item' },
    });

    fireEvent.click(screen.getByRole('button', { name: 'Import' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Experience text' }), {
      target: { value: 'Exactly this valid text' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save import' }));

    expect(await screen.findByRole('heading', { name: 'Imported experience' })).toBeInTheDocument();
    expect(screen.getByTestId('experience-detail-pane')).toHaveClass('block');
    expect(screen.getByTestId('experience-list-pane')).toHaveClass('hidden');
  });

  it('switches mobile panes on list selection and returns to the list with Back', async () => {
    render(<ExperienceLibraryPage />);
    await screen.findByRole('button', { name: /Searchable project/ });

    expect(screen.getByTestId('experience-list-pane')).toHaveClass('block');
    expect(screen.getByTestId('experience-detail-pane')).toHaveClass('hidden');
    fireEvent.click(screen.getByRole('button', { name: /Searchable project/ }));
    expect(screen.getByTestId('experience-list-pane')).toHaveClass('hidden');
    expect(screen.getByTestId('experience-detail-pane')).toHaveClass('block');
    fireEvent.click(screen.getByRole('button', { name: 'Back to list' }));
    expect(screen.getByTestId('experience-list-pane')).toHaveClass('block');
    expect(screen.getByTestId('experience-detail-pane')).toHaveClass('hidden');
  });

  it('localizes the status label and stops Enter from escaping the import textarea', async () => {
    const documentKeydown = vi.fn();
    document.addEventListener('keydown', documentKeydown);
    render(<ExperienceLibraryPage />);
    await screen.findByRole('button', { name: /Searchable project/ });
    expect(screen.getByText(/Draft status/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Import' }));
    fireEvent.keyDown(screen.getByRole('textbox', { name: 'Experience text' }), { key: 'Enter' });
    expect(documentKeydown).not.toHaveBeenCalled();
    document.removeEventListener('keydown', documentKeydown);
  });

  it('returns focus to Import when the dialog closes with Escape', async () => {
    render(<ExperienceLibraryPage />);
    const importButton = screen.getByRole('button', { name: 'Import' });
    importButton.focus();
    fireEvent.click(importButton);
    await screen.findByRole('textbox', { name: 'Experience text' });

    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(importButton).toHaveFocus();
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
    expect(screen.getAllByText('Exactly this valid text').length).toBeGreaterThan(0);
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
    expect(screen.getAllByText('Exactly this valid text').length).toBeGreaterThan(0);
  });

  it('saves controlled metadata with current experiences clearing the end date', async () => {
    const refreshed = {
      ...listItem,
      title: 'Current platform',
      is_current: true,
      end_date: null,
      completeness: 72,
      evidence_items: [],
      missing_dimensions: ['evidence'],
      suggested_questions: [],
    };
    api.patchExperience.mockResolvedValue(refreshed);
    render(<ExperienceLibraryPage />);

    await screen.findByRole('heading', { name: 'Searchable project' });
    fireEvent.change(await screen.findByRole('textbox', { name: 'Title' }), {
      target: { value: 'Current platform' },
    });
    fireEvent.click(screen.getByRole('checkbox', { name: 'Current experience' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save experience' }));

    await waitFor(() =>
      expect(api.patchExperience).toHaveBeenCalledWith(
        1,
        expect.objectContaining({ title: 'Current platform', is_current: true, end_date: null })
      )
    );
    expect(screen.getByText('72% complete')).toBeInTheDocument();
  });

  it('keeps action, result, and metrics together when adding an evidence card', async () => {
    const withEvidence = {
      ...listItem,
      completeness: 60,
      evidence_ids: [4],
      evidence_items: [
        {
          id: 4,
          action: 'Automated imports',
          result: 'Faster review',
          metrics: '40%',
          created_at: '2025-01-01',
          updated_at: '2025-01-01',
        },
      ],
      missing_dimensions: [],
      suggested_questions: [],
    };
    api.createEvidence.mockResolvedValue(withEvidence);
    render(<ExperienceLibraryPage />);

    await screen.findByLabelText('Action new');
    fireEvent.change(screen.getByLabelText('Action new'), {
      target: { value: 'Automated imports' },
    });
    fireEvent.change(screen.getByLabelText('Result new'), { target: { value: 'Faster review' } });
    fireEvent.change(screen.getByLabelText('Metrics new'), { target: { value: '40%' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add evidence' }));

    await waitFor(() =>
      expect(api.createEvidence).toHaveBeenCalledWith(1, {
        action: 'Automated imports',
        result: 'Faster review',
        metrics: '40%',
      })
    );
    expect(screen.getByLabelText('Action 4')).toHaveValue('Automated imports');
    expect(screen.getByLabelText('Result 4')).toHaveValue('Faster review');
    expect(screen.getByLabelText('Metrics 4')).toHaveValue('40%');
  });

  it('keeps the edited metadata visible after a save failure', async () => {
    api.patchExperience.mockRejectedValue(new Error('offline'));
    render(<ExperienceLibraryPage />);

    const title = await screen.findByRole('textbox', { name: 'Title' });
    fireEvent.change(title, { target: { value: 'Still editing locally' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save experience' }));

    expect(await screen.findByText('offline')).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: 'Title' })).toHaveValue('Still editing locally');
  });

  it('renders the server 409 completeness and missing guidance without changing the draft', async () => {
    api.fetchExperience.mockResolvedValue({
      ...listItem,
      completeness: 20,
      evidence_items: [],
      missing_dimensions: [],
      suggested_questions: [],
    });
    api.markExperienceReady.mockRejectedValue({
      conflict: { completeness: 44, missing_dimensions: ['metrics'] },
    });
    render(<ExperienceLibraryPage />);

    await screen.findByRole('button', { name: 'Mark ready' });
    fireEvent.click(screen.getByRole('button', { name: 'Mark ready' }));

    expect(await screen.findByText('metrics')).toBeInTheDocument();
    expect(screen.getByText('Not ready')).toBeInTheDocument();
  });

  it('archives from the active list and restores from the recycle bin', async () => {
    const archived = {
      ...listItem,
      status: 'archived' as const,
      archived_at: '2025-02-01T00:00:00Z',
      evidence_items: [],
      missing_dimensions: [],
      suggested_questions: [],
    };
    api.listExperiences.mockImplementation(({ status }: { status?: string } = {}) =>
      Promise.resolve(
        status === 'archived' ? { items: [archived], total: 1 } : { items: [listItem], total: 1 }
      )
    );
    api.fetchExperience.mockImplementation((id: number) =>
      Promise.resolve(
        id === 1 && api.listExperiences.mock.calls.at(-1)?.[0]?.status === 'archived'
          ? archived
          : { ...listItem, evidence_items: [], missing_dimensions: [], suggested_questions: [] }
      )
    );
    api.archiveExperience.mockResolvedValue(archived);
    api.restoreExperience.mockResolvedValue({
      ...listItem,
      evidence_items: [],
      missing_dimensions: [],
      suggested_questions: [],
    });
    render(<ExperienceLibraryPage />);

    await screen.findByRole('button', { name: 'Archive experience' });
    fireEvent.click(screen.getByRole('button', { name: 'Archive experience' }));
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: /Searchable project/ })).not.toBeInTheDocument()
    );
    fireEvent.click(screen.getByRole('button', { name: 'Recycle bin' }));
    await screen.findByRole('button', { name: 'Restore experience' });
    fireEvent.click(screen.getByRole('button', { name: 'Restore experience' }));
    await waitFor(() => expect(api.restoreExperience).toHaveBeenCalledWith(1));
    expect(screen.queryByRole('button', { name: 'Restore experience' })).not.toBeInTheDocument();
  });

  it('loads deletion impact before allowing an archived experience to be permanently deleted', async () => {
    const archived = {
      ...listItem,
      status: 'archived' as const,
      archived_at: '2025-02-01T00:00:00Z',
      evidence_items: [],
      missing_dimensions: [],
      suggested_questions: [],
    };
    api.listExperiences.mockResolvedValue({ items: [archived], total: 1 });
    api.fetchExperience.mockResolvedValue(archived);
    api.getDeletionImpact.mockResolvedValue({
      affected_matches: [7],
      affected_resumes: ['resume-1'],
    });
    api.deleteExperiencePermanently.mockResolvedValue(undefined);
    render(<ExperienceLibraryPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Recycle bin' }));

    fireEvent.click(await screen.findByRole('button', { name: 'Delete permanently' }));
    expect(await screen.findByText('1 affected matches')).toBeInTheDocument();
    expect(screen.getByText('resume-1')).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole('button', { name: 'Delete permanently' }).at(-1)!);
    await waitFor(() => expect(api.deleteExperiencePermanently).toHaveBeenCalledWith(1));
    expect(screen.queryByRole('button', { name: /Searchable project/ })).not.toBeInTheDocument();
  });

  it('prompts before changing selection with a dirty draft and registers beforeunload only while dirty', async () => {
    const second = { ...listItem, experience_id: 2, title: 'Second experience' };
    api.listExperiences.mockResolvedValue({ items: [listItem, second], total: 2 });
    api.fetchExperience.mockImplementation((id: number) =>
      Promise.resolve({
        ...(id === 2 ? second : listItem),
        evidence_items: [],
        missing_dimensions: [],
        suggested_questions: [],
      })
    );
    render(<ExperienceLibraryPage />);

    const title = await screen.findByRole('textbox', { name: 'Title' });
    fireEvent.change(title, { target: { value: 'Unsaved title' } });
    const beforeUnload = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(beforeUnload);
    expect(beforeUnload.defaultPrevented).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: /Second experience/ }));
    expect(
      await screen.findByRole('dialog', { name: /Discard unsaved changes/ })
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Discard edits' }));
    expect(await screen.findByRole('heading', { name: 'Second experience' })).toBeInTheDocument();
  });
});
