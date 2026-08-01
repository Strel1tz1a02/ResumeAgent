import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ExperienceDetail, ExperienceRead } from '@/lib/api/experiences';

const api = vi.hoisted(() => ({
  listExperiences: vi.fn(),
  fetchExperience: vi.fn(),
  importExperienceText: vi.fn(),
  createExperience: vi.fn(),
  patchExperience: vi.fn(),
  saveExperience: vi.fn(),
  createEvidence: vi.fn(),
  patchEvidence: vi.fn(),
  deleteEvidence: vi.fn(),
  reorderEvidence: vi.fn(),
  markExperienceReady: vi.fn(),
  archiveExperience: vi.fn(),
  restoreExperience: vi.fn(),
  getDeletionImpact: vi.fn(),
  deleteExperiencePermanently: vi.fn(),
}));

const chatApi = vi.hoisted(() => ({
  createExperienceConversation: vi.fn(),
  closeExperienceConversation: vi.fn(),
  streamExperienceOpening: vi.fn(),
  streamExperienceMessage: vi.fn(),
  resolveExperienceProposal: vi.fn(),
  eventExperienceDetail: vi.fn((event: { data: { experience?: unknown } }) =>
    event.data.experience ? event.data.experience : null
  ),
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
      'experiences.createFromTemplate': 'Create from Template',
      'experiences.import.button': 'Import Text',
      'experiences.creating': 'Creating',
      'experiences.backToDashboard': 'Back to dashboard',
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
      'experiences.editor.background': 'Background',
      'experiences.editor.save': 'Save experience',
      'experiences.editor.saveField': 'Save field',
      'experiences.completeness.score': '72% complete',
      'experiences.completeness.dimension.metrics': 'metrics',
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
      'experiences.ready.error': 'Could not mark ready',
      'experiences.permanent.title': 'Delete permanently?',
      'experiences.permanent.description': 'This cannot be undone.',
      'experiences.permanent.loadingImpact': 'Loading impact',
      'experiences.permanent.affectedMatches': '1 affected matches',
      'experiences.permanent.affectedResumes': '1 affected resumes',
      'experiences.permanent.confirm': 'Delete permanently',
      'experiences.ai.title': 'Organize with AI',
      'experiences.ai.start': 'Help me organize with AI',
      'experiences.ai.startField': 'Start AI chat',
      'experiences.ai.startConfirm': 'Start an AI chat for this field?',
      'experiences.ai.startConfirmAction': 'Start',
      'experiences.ai.answer': 'Your answer',
      'experiences.ai.submit': 'Apply answer',
      'experiences.ai.next': 'Ask another question',
      'experiences.ai.loading': 'Preparing question',
      'experiences.ai.answering': 'Applying answer',
      'experiences.ai.retry': 'Try again',
      'experiences.ai.error':
        'AI could not organize this experience. You can keep editing it manually.',
      'experiences.ai.fallback': 'Suggested question',
      'experiences.ai.manual': 'You can edit this experience manually at any time.',
      'common.cancel': 'Cancel',
    })[key] ?? key
);

vi.mock('@/lib/api/experiences', () => api);
vi.mock('@/lib/api/experience-ai-chat', () => chatApi);

vi.mock('@/lib/i18n', () => ({
  useTranslations: () => ({ t: translate }),
}));

import { ExperienceLibraryPage } from '@/components/experiences/experience-library-page';
import { createExperienceQueryClient } from '@/lib/queries/experiences/provider';

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
  background: 'Exactly this valid text',
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
    api.saveExperience.mockImplementation(
      (experienceId: number, payload: { experience: Record<string, unknown> }) =>
        api.patchExperience(experienceId, payload.experience)
    );
    chatApi.createExperienceConversation.mockResolvedValue({
      conversation_id: 12,
      target: { key: 'background', ref_id: null },
      field_status: 'incomplete',
      revision: 0,
    });
    chatApi.closeExperienceConversation.mockResolvedValue(undefined);
    chatApi.streamExperienceOpening.mockImplementation(async function* () {
      yield { event: 'assistant.completed', data: {} };
    });
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it('provides a route-scoped query client with safe request defaults', async () => {
    const client = createExperienceQueryClient();

    expect(client.getDefaultOptions().queries).toMatchObject({
      retry: false,
      refetchOnWindowFocus: false,
      refetchOnReconnect: false,
    });
    expect(client.getDefaultOptions().mutations).toMatchObject({ retry: false });

    render(<ExperienceLibraryPage />);
    expect((await screen.findAllByText('Searchable project')).length).toBeGreaterThan(0);
  });

  it('labels both creation entry points by their input method', async () => {
    render(<ExperienceLibraryPage />);

    expect(await screen.findByRole('button', { name: 'Create from Template' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Import Text' })).toBeInTheDocument();
  });

  it('asks for compact confirmation before opening a field AI conversation', async () => {
    render(<ExperienceLibraryPage />);
    const background = await screen.findByRole('textbox', { name: 'Background' });

    fireEvent.focus(background);
    fireEvent.click(screen.getByRole('button', { name: 'Start AI chat' }));

    expect(screen.getByText('Start an AI chat for this field?')).toBeInTheDocument();
    expect(chatApi.createExperienceConversation).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Start' }));
    await waitFor(() =>
      expect(chatApi.createExperienceConversation).toHaveBeenCalledWith(1, {
        key: 'background',
        ref_id: null,
      })
    );
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

  it('creates and selects a blank manual draft without requiring pasted text', async () => {
    const blank: ExperienceDetail = {
      ...listItem,
      experience_id: 12,
      title: '',
      evidence_items: [],
      missing_dimensions: ['identity'],
      suggested_questions: ['What concise title best describes this experience?'],
    };
    api.createExperience.mockResolvedValue(blank);
    render(<ExperienceLibraryPage />);

    fireEvent.click(await screen.findByRole('button', { name: 'Create from Template' }));

    await waitFor(() => expect(api.createExperience).toHaveBeenCalledWith({}));
    expect(await screen.findByRole('textbox', { name: 'Title' })).toHaveValue('');
    expect(screen.getByRole('link', { name: 'Back to dashboard' })).toHaveAttribute(
      'href',
      '/dashboard'
    );
  });

  it('disables both creation entry points while either creation mutation is pending', async () => {
    const pending = deferred<ExperienceDetail>();
    api.createExperience.mockReturnValue(pending.promise);
    render(<ExperienceLibraryPage />);
    await screen.findAllByText('Searchable project');

    fireEvent.click(screen.getByRole('button', { name: 'Create from Template' }));
    await waitFor(() => expect(api.createExperience).toHaveBeenCalledWith({}));

    expect(screen.getByRole('button', { name: 'Import Text' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Creating' })).toBeDisabled();
    pending.resolve(imported);
    await screen.findAllByText('Imported experience');
  });

  it('aborts the obsolete list request when switching library views', async () => {
    const active = deferred<{ items: ExperienceRead[]; total: number }>();
    let activeSignal: AbortSignal | undefined;
    api.listExperiences.mockImplementation((query: { status?: string }, signal?: AbortSignal) => {
      if (query.status === 'active') {
        activeSignal = signal;
        return active.promise;
      }
      return Promise.resolve({ items: [], total: 0 });
    });
    render(<ExperienceLibraryPage />);

    fireEvent.click(screen.getByRole('tab', { name: 'Recycle bin' }));
    await waitFor(() =>
      expect(api.listExperiences).toHaveBeenCalledWith(
        { status: 'archived' },
        expect.any(AbortSignal)
      )
    );

    expect(activeSignal?.aborted).toBe(true);
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

  it('finds experiences from background and technologies', async () => {
    const rawOnly = {
      ...listItem,
      experience_id: 2,
      title: 'Untitled note',
      background: 'Migrated a legacy warehouse',
      technologies: [],
      tags: [],
    };
    const technologyOnly = {
      ...listItem,
      experience_id: 3,
      title: 'Implementation',
      background: null,
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

    fireEvent.click(screen.getByRole('button', { name: 'Import Text' }));
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

    fireEvent.click(screen.getByRole('button', { name: 'Import Text' }));
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

    fireEvent.click(screen.getByRole('button', { name: 'Import Text' }));
    fireEvent.keyDown(screen.getByRole('textbox', { name: 'Experience text' }), { key: 'Enter' });
    expect(documentKeydown).not.toHaveBeenCalled();
    document.removeEventListener('keydown', documentKeydown);
  });

  it('returns focus to Import when the dialog closes with Escape', async () => {
    render(<ExperienceLibraryPage />);
    const importButton = screen.getByRole('button', { name: 'Import Text' });
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

    fireEvent.click(screen.getByRole('button', { name: 'Import Text' }));
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
  });

  it('keeps the persisted import selected when a later reload fails', async () => {
    api.importExperienceText.mockResolvedValue(imported);
    api.listExperiences
      .mockResolvedValueOnce({ items: [listItem], total: 1 })
      .mockRejectedValueOnce(new Error('offline'));
    render(<ExperienceLibraryPage />);
    await screen.findByRole('button', { name: /Searchable project/ });

    fireEvent.click(screen.getByRole('button', { name: 'Import Text' }));
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
        expect.objectContaining({
          title: 'Current platform',
          is_current: true,
        })
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

  it('globally saves metadata, existing evidence, and appended evidence together', async () => {
    const detail: ExperienceDetail = {
      ...listItem,
      evidence_ids: [7],
      evidence_items: [
        {
          id: 7,
          action: 'Original action',
          result: null,
          metrics: null,
          created_at: '2025-01-01T00:00:00Z',
          updated_at: '2025-01-01T00:00:00Z',
        },
      ],
      missing_dimensions: [],
      suggested_questions: [],
      field_states: [
        { key: 'title', ref_id: null, status: 'complete', revision: 2 },
        { key: 'action', ref_id: 7, status: 'complete', revision: 3 },
        { key: 'result', ref_id: 7, status: 'incomplete', revision: 3 },
        { key: 'metrics', ref_id: 7, status: 'incomplete', revision: 3 },
        { key: 'evidence_new', ref_id: null, status: 'complete', revision: 4 },
      ],
    };
    api.fetchExperience.mockResolvedValue(detail);
    api.saveExperience.mockResolvedValue({
      ...detail,
      title: 'Saved together',
      evidence_items: [
        { ...detail.evidence_items[0], action: 'Updated action' },
        {
          id: 8,
          action: 'Appended action',
          result: null,
          metrics: null,
          created_at: '2025-01-02T00:00:00Z',
          updated_at: '2025-01-02T00:00:00Z',
        },
      ],
    });
    render(<ExperienceLibraryPage />);

    fireEvent.change(await screen.findByRole('textbox', { name: 'Title' }), {
      target: { value: 'Saved together' },
    });
    fireEvent.change(screen.getByLabelText('Action 7'), {
      target: { value: 'Updated action' },
    });
    fireEvent.change(screen.getByLabelText('Action new'), {
      target: { value: 'Appended action' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save experience' }));

    await waitFor(() =>
      expect(api.saveExperience).toHaveBeenCalledWith(
        1,
        expect.objectContaining({
          experience: expect.objectContaining({
            title: 'Saved together',
            expected_field_revisions: expect.objectContaining({ title: 2 }),
          }),
          evidence_items: [
            {
              evidence_id: 7,
              action: 'Updated action',
              result: null,
              metrics: null,
              expected_revision: 3,
            },
          ],
          new_evidence: { action: 'Appended action', result: null, metrics: null },
          expected_collection_revision: 4,
        })
      )
    );
  });

  it('keeps the original metadata concurrency token when evidence updates the cache', async () => {
    api.createEvidence.mockResolvedValue({
      ...listItem,
      updated_at: '2025-01-03T00:00:00Z',
      evidence_ids: [4],
      evidence_items: [
        {
          id: 4,
          action: 'Added evidence',
          result: null,
          metrics: null,
          created_at: '2025-01-03T00:00:00Z',
          updated_at: '2025-01-03T00:00:00Z',
        },
      ],
      missing_dimensions: [],
      suggested_questions: [],
    });
    api.patchExperience.mockResolvedValue({
      ...listItem,
      title: 'Unsaved local title',
      updated_at: '2025-01-04T00:00:00Z',
      evidence_items: [],
      missing_dimensions: [],
      suggested_questions: [],
    });
    render(<ExperienceLibraryPage />);

    fireEvent.change(await screen.findByRole('textbox', { name: 'Title' }), {
      target: { value: 'Unsaved local title' },
    });
    fireEvent.change(screen.getByLabelText('Action new'), {
      target: { value: 'Added evidence' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Add evidence' }));
    await screen.findByDisplayValue('Added evidence');
    fireEvent.click(screen.getByRole('button', { name: 'Save experience' }));

    await waitFor(() =>
      expect(api.patchExperience).toHaveBeenCalledWith(
        1,
        expect.objectContaining({ title: 'Unsaved local title' })
      )
    );
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
    fireEvent.click(screen.getByRole('tab', { name: 'Recycle bin' }));
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
      affected_matches: [{ match_id: 7, job_title: 'AI Engineer' }],
      affected_resumes: ['resume-1'],
    });
    api.deleteExperiencePermanently.mockResolvedValue(undefined);
    render(<ExperienceLibraryPage />);
    fireEvent.click(await screen.findByRole('tab', { name: 'Recycle bin' }));

    fireEvent.click(await screen.findByRole('button', { name: 'Delete permanently' }));
    expect(await screen.findByText('1 affected matches')).toBeInTheDocument();
    expect(screen.getByText('AI Engineer (#7)')).toBeInTheDocument();
    expect(screen.getByText('resume-1')).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole('button', { name: 'Delete permanently' }).at(-1)!);
    await waitFor(() => expect(api.deleteExperiencePermanently).toHaveBeenCalledWith(1));
    expect(screen.queryByRole('button', { name: /Searchable project/ })).not.toBeInTheDocument();
  });

  it('does not carry a permanent-delete error to a different experience', async () => {
    const first = {
      ...listItem,
      status: 'archived' as const,
      archived_at: '2025-02-01T00:00:00Z',
      evidence_items: [],
      missing_dimensions: [],
      suggested_questions: [],
    };
    const second = { ...first, experience_id: 2, title: 'Second archived experience' };
    api.listExperiences.mockImplementation(({ status }: { status?: string }) =>
      Promise.resolve(
        status === 'archived' ? { items: [first, second], total: 2 } : { items: [], total: 0 }
      )
    );
    api.fetchExperience.mockImplementation((id: number) =>
      Promise.resolve(id === 1 ? first : second)
    );
    api.getDeletionImpact.mockResolvedValue({ affected_matches: [], affected_resumes: [] });
    api.deleteExperiencePermanently.mockRejectedValueOnce(new Error('first delete failed'));
    render(<ExperienceLibraryPage />);
    fireEvent.click(screen.getByRole('tab', { name: 'Recycle bin' }));

    fireEvent.click(await screen.findByRole('button', { name: 'Delete permanently' }));
    await waitFor(() =>
      expect(api.getDeletionImpact).toHaveBeenCalledWith(1, expect.any(AbortSignal))
    );
    await screen.findByText('1 affected matches');
    fireEvent.click(screen.getAllByRole('button', { name: 'Delete permanently' }).at(-1)!);
    expect(await screen.findByText('first delete failed')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));

    fireEvent.click(screen.getByRole('button', { name: /Second archived experience/ }));
    await screen.findByRole('heading', { name: 'Second archived experience' });
    fireEvent.click(await screen.findByRole('button', { name: 'Delete permanently' }));

    expect(screen.queryByText('first delete failed')).not.toBeInTheDocument();
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

  it('keeps a dirty metadata draft and query unchanged until filter discard is confirmed', async () => {
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

    fireEvent.change(await screen.findByRole('textbox', { name: 'Title' }), {
      target: { value: 'Unsaved title' },
    });
    const search = screen.getByRole('textbox', { name: 'Search experiences' });
    fireEvent.change(search, { target: { value: 'Second' } });

    expect(
      await screen.findByRole('dialog', { name: /Discard unsaved changes/ })
    ).toBeInTheDocument();
    expect(search).toHaveValue('');
    fireEvent.click(screen.getByRole('button', { name: 'Discard edits' }));
    expect(search).toHaveValue('Second');
    expect(await screen.findByRole('heading', { name: 'Second experience' })).toBeInTheDocument();
  });

  it('keeps a dirty metadata draft and kind unchanged when filter discard is cancelled', async () => {
    const volunteer = {
      ...listItem,
      experience_id: 2,
      kind: 'volunteer' as const,
      title: 'Volunteer event',
    };
    api.listExperiences.mockResolvedValue({ items: [listItem, volunteer], total: 2 });
    render(<ExperienceLibraryPage />);

    fireEvent.change(await screen.findByRole('textbox', { name: 'Title' }), {
      target: { value: 'Unsaved title' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Kind' }));
    fireEvent.click(screen.getByRole('menuitemradio', { name: 'Volunteer' }));

    expect(
      await screen.findByRole('dialog', { name: /Discard unsaved changes/ })
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.getByRole('button', { name: 'Kind' })).toHaveTextContent('All kinds');
    expect(screen.getByRole('heading', { name: 'Searchable project' })).toBeInTheDocument();
  });

  it('protects unsaved evidence edits during selection and beforeunload', async () => {
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

    fireEvent.change(await screen.findByLabelText('Action new'), {
      target: { value: 'Unsaved evidence' },
    });
    const beforeUnload = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(beforeUnload);
    expect(beforeUnload.defaultPrevented).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: /Second experience/ }));
    expect(
      await screen.findByRole('dialog', { name: /Discard unsaved changes/ })
    ).toBeInTheDocument();
  });

  it('does not let an older A detail response replace a saved A response after A to B to A selection', async () => {
    const second = { ...listItem, experience_id: 2, title: 'Second experience' };
    const firstA = deferred<ExperienceDetail>();
    const savedA = {
      ...listItem,
      title: 'Saved A title',
      updated_at: '2025-02-01T00:00:00Z',
      evidence_items: [],
      missing_dimensions: [],
      suggested_questions: [],
    };
    const staleA = {
      ...listItem,
      title: 'Stale A title',
      updated_at: '2025-01-01T00:00:00Z',
      evidence_items: [],
      missing_dimensions: [],
      suggested_questions: [],
    };
    api.listExperiences.mockResolvedValue({ items: [listItem, second], total: 2 });
    api.fetchExperience
      .mockReturnValueOnce(firstA.promise)
      .mockResolvedValueOnce({
        ...second,
        evidence_items: [],
        missing_dimensions: [],
        suggested_questions: [],
      })
      .mockResolvedValueOnce({
        ...listItem,
        evidence_items: [],
        missing_dimensions: [],
        suggested_questions: [],
      });
    api.patchExperience.mockResolvedValue(savedA);
    render(<ExperienceLibraryPage />);

    await screen.findByRole('button', { name: /Searchable project/ });
    fireEvent.click(screen.getByRole('button', { name: /Second experience/ }));
    await screen.findByRole('heading', { name: 'Second experience' });
    fireEvent.click(screen.getByRole('button', { name: /Searchable project/ }));
    const title = await screen.findByRole('textbox', { name: 'Title' });
    fireEvent.change(title, { target: { value: 'Saved A title' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save experience' }));
    await screen.findByDisplayValue('Saved A title');

    firstA.resolve(staleA);
    await waitFor(() =>
      expect(screen.queryByDisplayValue('Stale A title')).not.toBeInTheDocument()
    );
  });

  it('does not let a late metadata save for A replace the draft shown for B', async () => {
    const second = { ...listItem, experience_id: 2, title: 'Second experience' };
    const pendingSave = deferred<ExperienceDetail>();
    api.listExperiences.mockResolvedValue({ items: [listItem, second], total: 2 });
    api.fetchExperience.mockImplementation((id: number) =>
      Promise.resolve({
        ...(id === 2 ? second : listItem),
        evidence_items: [],
        missing_dimensions: [],
        suggested_questions: [],
      })
    );
    api.patchExperience.mockReturnValue(pendingSave.promise);
    render(<ExperienceLibraryPage />);

    const title = await screen.findByRole('textbox', { name: 'Title' });
    fireEvent.change(title, { target: { value: 'Saving A' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save experience' }));
    fireEvent.click(screen.getByRole('button', { name: /Second experience/ }));
    fireEvent.click(await screen.findByRole('button', { name: 'Discard edits' }));
    expect(await screen.findByRole('textbox', { name: 'Title' })).toHaveValue('Second experience');

    pendingSave.resolve({
      ...listItem,
      title: 'Saving A',
      updated_at: '2025-02-01T00:00:00Z',
      evidence_items: [],
      missing_dimensions: [],
      suggested_questions: [],
    });

    await waitFor(() =>
      expect(screen.getByRole('textbox', { name: 'Title' })).toHaveValue('Second experience')
    );
  });

  it('stops Enter from escaping experience textareas', async () => {
    const parentKeyDown = vi.fn();
    render(
      <div onKeyDown={parentKeyDown}>
        <ExperienceLibraryPage />
      </div>
    );

    fireEvent.keyDown(await screen.findByRole('textbox', { name: 'Background' }), {
      key: 'Enter',
    });

    expect(parentKeyDown).not.toHaveBeenCalled();
  });

  it('ignores a late ready conflict for A after B becomes selected', async () => {
    const second = { ...listItem, experience_id: 2, title: 'Second experience' };
    const pendingReady = deferred<ExperienceDetail>();
    api.listExperiences.mockResolvedValue({ items: [listItem, second], total: 2 });
    api.fetchExperience.mockImplementation((id: number) =>
      Promise.resolve({
        ...(id === 2 ? second : listItem),
        evidence_items: [],
        missing_dimensions: [],
        suggested_questions: [],
      })
    );
    api.markExperienceReady.mockReturnValue(pendingReady.promise);
    render(<ExperienceLibraryPage />);

    fireEvent.click(await screen.findByRole('button', { name: 'Mark ready' }));
    fireEvent.click(screen.getByRole('button', { name: /Second experience/ }));
    await screen.findByRole('heading', { name: 'Second experience' });
    pendingReady.reject({ conflict: { completeness: 44, missing_dimensions: ['metrics'] } });

    await waitFor(() => expect(screen.queryByText('Not ready')).not.toBeInTheDocument());
    expect(screen.queryByText('metrics')).not.toBeInTheDocument();
  });

  it('does not clear B when a late archive for A completes and prevents double archive submits', async () => {
    const second = { ...listItem, experience_id: 2, title: 'Second experience' };
    const pendingArchive = deferred<ExperienceDetail>();
    api.listExperiences.mockResolvedValue({ items: [listItem, second], total: 2 });
    api.fetchExperience.mockImplementation((id: number) =>
      Promise.resolve({
        ...(id === 2 ? second : listItem),
        evidence_items: [],
        missing_dimensions: [],
        suggested_questions: [],
      })
    );
    api.archiveExperience.mockReturnValue(pendingArchive.promise);
    render(<ExperienceLibraryPage />);

    const archive = await screen.findByRole('button', { name: 'Archive experience' });
    fireEvent.click(archive);
    fireEvent.click(archive);
    await waitFor(() => expect(api.archiveExperience).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole('button', { name: /Second experience/ }));
    await screen.findByRole('heading', { name: 'Second experience' });
    pendingArchive.resolve({
      ...listItem,
      status: 'archived',
      archived_at: '2025-02-01T00:00:00Z',
      evidence_items: [],
      missing_dimensions: [],
      suggested_questions: [],
    });

    expect(await screen.findByRole('heading', { name: 'Second experience' })).toBeInTheDocument();
    expect(api.archiveExperience).toHaveBeenCalledTimes(1);
  });

  it('preserves a dirty evidence card through an unrelated metadata save and still guards navigation', async () => {
    const second = { ...listItem, experience_id: 2, title: 'Second experience' };
    const detail = {
      ...listItem,
      evidence_ids: [7],
      evidence_items: [
        {
          id: 7,
          action: 'Original action',
          result: 'Original result',
          metrics: null,
          created_at: '2025-01-01T00:00:00Z',
          updated_at: '2025-01-01T00:00:00Z',
        },
      ],
      missing_dimensions: [],
      suggested_questions: [],
    };
    api.listExperiences.mockResolvedValue({ items: [listItem, second], total: 2 });
    api.fetchExperience.mockImplementation((id: number) =>
      Promise.resolve(
        id === 1
          ? detail
          : { ...second, evidence_items: [], missing_dimensions: [], suggested_questions: [] }
      )
    );
    api.patchExperience.mockResolvedValue({
      ...detail,
      title: 'Metadata saved',
      evidence_items: [...detail.evidence_items],
    });
    render(<ExperienceLibraryPage />);

    fireEvent.change(await screen.findByLabelText('Action 7'), {
      target: { value: 'Unsaved action' },
    });
    fireEvent.change(screen.getByRole('textbox', { name: 'Title' }), {
      target: { value: 'Metadata saved' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save experience' }));

    expect(await screen.findByLabelText('Action 7')).toHaveValue('Unsaved action');
    fireEvent.click(screen.getByRole('button', { name: /Second experience/ }));
    expect(
      await screen.findByRole('dialog', { name: /Discard unsaved changes/ })
    ).toBeInTheDocument();
  });

  it('keeps focused field save and global metadata save available for dirty units', async () => {
    const withEvidence: ExperienceDetail = {
      ...listItem,
      evidence_ids: [7],
      evidence_items: [
        {
          id: 7,
          action: 'Original action',
          result: null,
          metrics: null,
          created_at: '2025-01-01T00:00:00Z',
          updated_at: '2025-01-01T00:00:00Z',
        },
      ],
      missing_dimensions: ['result'],
      suggested_questions: ['What changed?'],
    };
    api.fetchExperience.mockResolvedValue(withEvidence);
    api.patchExperience.mockResolvedValue({
      ...withEvidence,
      title: 'Edited metadata',
      updated_at: '2025-01-03T00:00:00Z',
    });
    api.patchEvidence.mockResolvedValue({
      ...withEvidence,
      updated_at: '2025-01-04T00:00:00Z',
      evidence_items: [{ ...withEvidence.evidence_items[0], action: 'Edited action' }],
    });
    render(<ExperienceLibraryPage />);

    fireEvent.change(await screen.findByRole('textbox', { name: 'Title' }), {
      target: { value: 'Edited metadata' },
    });
    fireEvent.change(screen.getByLabelText('Action 7'), {
      target: { value: 'Edited action' },
    });
    fireEvent.focus(screen.getByLabelText('Action 7'));
    fireEvent.click(screen.getByRole('button', { name: 'Save field' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save experience' }));

    await waitFor(() => {
      expect(api.patchExperience).toHaveBeenCalledTimes(1);
      expect(api.patchEvidence).toHaveBeenCalledTimes(1);
    });
  });

  it('preserves a dirty evidence card through mark-ready and resets it on explicit discard', async () => {
    const detail = {
      ...listItem,
      evidence_ids: [7],
      evidence_items: [
        {
          id: 7,
          action: 'Original action',
          result: 'Original result',
          metrics: null,
          created_at: '2025-01-01T00:00:00Z',
          updated_at: '2025-01-01T00:00:00Z',
        },
      ],
      missing_dimensions: [],
      suggested_questions: [],
    };
    api.fetchExperience.mockResolvedValue(detail);
    api.markExperienceReady.mockResolvedValue({
      ...detail,
      status: 'ready',
      evidence_items: [...detail.evidence_items],
    });
    render(<ExperienceLibraryPage />);

    fireEvent.change(await screen.findByLabelText('Action 7'), {
      target: { value: 'Unsaved action' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Mark ready' }));
    expect(await screen.findByLabelText('Action 7')).toHaveValue('Unsaved action');
    fireEvent.click(screen.getByRole('button', { name: 'Back to list' }));
    expect(
      await screen.findByRole('dialog', { name: /Discard unsaved changes/ })
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Discard edits' }));
    expect(screen.getByLabelText('Action 7')).toHaveValue('Original action');
  });

  it('does not show a late A conflict as B generic ready error', async () => {
    const second = { ...listItem, experience_id: 2, title: 'Second experience' };
    const pendingReady = deferred<ExperienceDetail>();
    api.listExperiences.mockResolvedValue({ items: [listItem, second], total: 2 });
    api.fetchExperience.mockImplementation((id: number) =>
      Promise.resolve({
        ...(id === 2 ? second : listItem),
        evidence_items: [],
        missing_dimensions: [],
        suggested_questions: [],
      })
    );
    api.markExperienceReady.mockReturnValue(pendingReady.promise);
    render(<ExperienceLibraryPage />);

    fireEvent.click(await screen.findByRole('button', { name: 'Mark ready' }));
    fireEvent.click(screen.getByRole('button', { name: /Second experience/ }));
    await screen.findByRole('heading', { name: 'Second experience' });
    pendingReady.reject({ conflict: { completeness: 44, missing_dimensions: ['metrics'] } });

    await waitFor(() => expect(screen.queryByText('Could not mark ready')).not.toBeInTheDocument());
    expect(screen.queryByText('metrics')).not.toBeInTheDocument();
  });

  it('clears an A ready error when selecting B', async () => {
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
    api.markExperienceReady.mockRejectedValue(new Error('ready failed'));
    render(<ExperienceLibraryPage />);

    fireEvent.click(await screen.findByRole('button', { name: 'Mark ready' }));
    expect(await screen.findByText('ready failed')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Second experience/ }));
    await screen.findByRole('heading', { name: 'Second experience' });
    expect(screen.queryByText('ready failed')).not.toBeInTheDocument();
  });

  it('exposes the active and archived views as ARIA tabs', async () => {
    render(<ExperienceLibraryPage />);
    expect(await screen.findByRole('tab', { name: 'Active', selected: true })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Recycle bin', selected: false })).toBeInTheDocument();
  });

  it('uses roving tab focus and arrow keys to switch library views', async () => {
    render(<ExperienceLibraryPage />);
    const active = await screen.findByRole('tab', { name: 'Active', selected: true });
    const archived = screen.getByRole('tab', { name: 'Recycle bin', selected: false });

    expect(active).toHaveAttribute('tabindex', '0');
    expect(archived).toHaveAttribute('tabindex', '-1');
    active.focus();
    fireEvent.keyDown(active, { key: 'ArrowRight' });

    expect(await screen.findByRole('tab', { name: 'Recycle bin', selected: true })).toHaveFocus();
    expect(screen.getByRole('tab', { name: 'Active', selected: false })).toHaveAttribute(
      'tabindex',
      '-1'
    );

    fireEvent.keyDown(screen.getByRole('tab', { name: 'Recycle bin' }), { key: 'ArrowLeft' });
    expect(await screen.findByRole('tab', { name: 'Active', selected: true })).toHaveFocus();
  });

  it('does not focus an inactive tab when unsaved changes block an arrow-key view switch', async () => {
    render(<ExperienceLibraryPage />);
    const title = await screen.findByRole('textbox', { name: 'Title' });
    const active = await screen.findByRole('tab', { name: 'Active', selected: true });
    const archived = screen.getByRole('tab', { name: 'Recycle bin', selected: false });
    let archivedFocusCount = 0;
    archived.addEventListener('focus', () => {
      archivedFocusCount += 1;
    });
    fireEvent.change(title, {
      target: { value: 'Unsaved title' },
    });

    active.focus();
    fireEvent.keyDown(active, { key: 'ArrowRight' });

    expect(
      await screen.findByRole('dialog', { name: /Discard unsaved changes/ })
    ).toBeInTheDocument();
    expect(archivedFocusCount).toBe(0);
    expect(archived).not.toHaveFocus();
    expect(archived).toHaveAttribute('aria-selected', 'false');
  });
});
