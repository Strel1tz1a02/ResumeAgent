'use client';

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import ArrowLeft from 'lucide-react/dist/esm/icons/arrow-left';
import CheckCircle2 from 'lucide-react/dist/esm/icons/check-circle-2';
import Loader2 from 'lucide-react/dist/esm/icons/loader-2';
import Sparkles from 'lucide-react/dist/esm/icons/sparkles';
import TriangleAlert from 'lucide-react/dist/esm/icons/triangle-alert';

import { Button } from '@/components/ui/button';
import { Card, CardDescription, CardTitle } from '@/components/ui/card';
import { listJDImports, type JDImport } from '@/lib/api/jd-imports';
import {
  confirmResumeGeneration,
  previewResumeGeneration,
  type ResumeGenerationMode,
  type ResumeGenerationPreview,
} from '@/lib/api/resume-generations';
import { useTranslations } from '@/lib/i18n';

export function ResumeGenerationWorkspace() {
  const { t } = useTranslations();
  const router = useRouter();
  const [jds, setJds] = useState<JDImport[]>([]);
  const [jdId, setJdId] = useState('');
  const [mode, setMode] = useState<ResumeGenerationMode>('auto');
  const [pageCount, setPageCount] = useState<1 | 2>(1);
  const [preview, setPreview] = useState<ResumeGenerationPreview | null>(null);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    listJDImports()
      .then((jdList) => {
        if (cancelled) return;
        setJds(jdList.items);
        const firstConfirmed = jdList.items.find((item) => item.status === 'confirmed');
        const firstWithRequirements = jdList.items.find((item) => item.requirements.length > 0);
        const selected = firstConfirmed ?? firstWithRequirements;
        if (selected) setJdId(String(selected.id));
      })
      .catch((reason: unknown) => {
        if (!cancelled) setError(reason instanceof Error ? reason.message : String(reason));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const jdById = useMemo(() => new Map(jds.map((item) => [item.id, item])), [jds]);

  async function generate() {
    if (!jdId) return;
    setGenerating(true);
    setError(null);
    setPreview(null);
    try {
      setPreview(
        await previewResumeGeneration({
          jd_information_id: Number(jdId),
          mode,
          constraints: { page_count: pageCount },
        })
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setGenerating(false);
    }
  }

  async function confirm() {
    if (!preview) return;
    setConfirming(true);
    setError(null);
    try {
      const selectedJD = jdById.get(Number(jdId));
      const result = await confirmResumeGeneration(
        preview.run_id,
        selectedJD
          ? `${selectedJD.company ? `${selectedJD.company} - ` : ''}${selectedJD.job_name}`
          : undefined
      );
      router.push(`/resumes/${result.resume_id}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setConfirming(false);
    }
  }

  return (
    <div className="min-h-screen bg-background px-4 py-8 md:px-8">
      <div className="mx-auto max-w-7xl space-y-6">
        <header className="border border-black bg-background p-6 shadow-sw-default md:p-8">
          <Link
            href="/dashboard"
            className="mb-5 inline-flex items-center gap-2 font-mono text-xs font-bold uppercase text-blue-700"
          >
            <ArrowLeft className="h-4 w-4" /> {t('resumeGeneration.back')}
          </Link>
          <p className="font-mono text-xs font-bold uppercase tracking-wider text-blue-700">
            {'// '}
            {t('resumeGeneration.kicker')}
          </p>
          <h1 className="mt-2 font-serif text-4xl uppercase md:text-6xl">
            {t('resumeGeneration.title')}
          </h1>
          <p className="mt-3 max-w-3xl font-mono text-sm text-steel-grey">
            {t('resumeGeneration.description')}
          </p>
        </header>

        <div className="grid gap-6 lg:grid-cols-[360px_1fr]">
          <Card className="h-fit space-y-5 rounded-none border-black p-5">
            <div>
              <CardTitle>{t('resumeGeneration.configure')}</CardTitle>
              <CardDescription>{t('resumeGeneration.configureDescription')}</CardDescription>
            </div>

            {loading ? (
              <p className="flex items-center gap-2 font-mono text-xs">
                <Loader2 className="h-4 w-4 animate-spin" /> {t('common.loading')}
              </p>
            ) : (
              <>
                <label className="block space-y-2 font-mono text-xs font-bold uppercase">
                  <span>{t('resumeGeneration.jd')}</span>
                  <select
                    value={jdId}
                    onChange={(event) => setJdId(event.target.value)}
                    className="h-11 w-full border border-black bg-background px-3 font-sans text-sm"
                  >
                    <option value="">{t('resumeGeneration.selectJD')}</option>
                    {jds.map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.company ? `${item.company} · ` : ''}
                        {item.job_name || `JD #${item.id}`} ({item.requirements.length})
                      </option>
                    ))}
                  </select>
                </label>

                <label className="block space-y-2 font-mono text-xs font-bold uppercase">
                  <span>{t('resumeGeneration.mode')}</span>
                  <select
                    value={mode}
                    onChange={(event) => setMode(event.target.value as ResumeGenerationMode)}
                    className="h-11 w-full border border-black bg-background px-3 font-sans text-sm"
                  >
                    <option value="auto">{t('resumeGeneration.modeAuto')}</option>
                    <option value="llm">{t('resumeGeneration.modeLLM')}</option>
                    <option value="deterministic">{t('resumeGeneration.modeDeterministic')}</option>
                  </select>
                </label>

                <label className="block space-y-2 font-mono text-xs font-bold uppercase">
                  <span>{t('resumeGeneration.pages')}</span>
                  <select
                    value={pageCount}
                    onChange={(event) => setPageCount(Number(event.target.value) as 1 | 2)}
                    className="h-11 w-full border border-black bg-background px-3 font-sans text-sm"
                  >
                    <option value={1}>1</option>
                    <option value={2}>2</option>
                  </select>
                </label>

                <Button
                  onClick={generate}
                  disabled={!jdId || generating}
                  className="w-full rounded-none border-2 border-black"
                >
                  {generating ? (
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  ) : (
                    <Sparkles className="mr-2 h-4 w-4" />
                  )}
                  {generating ? t('resumeGeneration.generating') : t('resumeGeneration.generate')}
                </Button>
              </>
            )}

            {error && (
              <div className="border border-red-700 bg-red-50 p-3 font-mono text-xs text-red-800">
                {error}
              </div>
            )}
          </Card>

          <main className="space-y-6">
            {!preview && !generating && (
              <Card className="rounded-none border-dashed border-black p-10 text-center">
                <CardTitle>{t('resumeGeneration.emptyTitle')}</CardTitle>
                <CardDescription className="mt-2">
                  {t('resumeGeneration.emptyDescription')}
                </CardDescription>
              </Card>
            )}

            {preview && (
              <>
                <section className="grid gap-4 md:grid-cols-3">
                  <Metric
                    label={t('resumeGeneration.coverage')}
                    value={`${Math.round(preview.plan.coverage_ratio * 100)}%`}
                  />
                  <Metric
                    label={t('resumeGeneration.selectedExperiences')}
                    value={String(preview.plan.selected_experiences.length)}
                  />
                  <Metric
                    label={t('resumeGeneration.searchRounds')}
                    value={String(preview.plan.search_rounds)}
                  />
                </section>

                {(preview.validation.warnings.length > 0 ||
                  preview.validation.errors.length > 0) && (
                  <section className="border border-black bg-amber-50 p-4">
                    <h2 className="flex items-center gap-2 font-mono text-sm font-bold uppercase">
                      <TriangleAlert className="h-4 w-4" /> {t('resumeGeneration.review')}
                    </h2>
                    <ul className="mt-3 list-disc space-y-1 pl-5 text-sm">
                      {[...preview.validation.errors, ...preview.validation.warnings].map(
                        (item) => (
                          <li key={item}>{item}</li>
                        )
                      )}
                    </ul>
                  </section>
                )}

                <section className="border border-black bg-background p-5 shadow-sw-default">
                  <h2 className="font-serif text-2xl uppercase">{t('resumeGeneration.plan')}</h2>
                  <div className="mt-4 grid gap-4 md:grid-cols-2">
                    {preview.plan.selected_experiences.map((item) => (
                      <div key={item.experience_id} className="border border-black p-4">
                        <p className="font-mono text-xs font-bold uppercase text-blue-700">
                          {item.section} · Experience #{item.experience_id}
                        </p>
                        <p className="mt-2 text-sm">{item.reason}</p>
                        <p className="mt-2 font-mono text-xs text-steel-grey">
                          Evidence: {item.evidence_ids.join(', ')}
                        </p>
                      </div>
                    ))}
                  </div>
                  {preview.plan.promoted_skills.length > 0 && (
                    <div className="mt-5">
                      <h3 className="font-mono text-xs font-bold uppercase">
                        {t('resumeGeneration.promotedSkills')}
                      </h3>
                      <div className="mt-2 flex flex-wrap gap-2">
                        {preview.plan.promoted_skills.map((item) => (
                          <span key={item.skill} className="border border-black px-3 py-1 text-sm">
                            {item.skill} · E{item.evidence_ids.join(',')}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}
                  {preview.plan.uncovered_requirements.length > 0 && (
                    <div className="mt-5">
                      <h3 className="font-mono text-xs font-bold uppercase text-red-700">
                        {t('resumeGeneration.gaps')}
                      </h3>
                      <p className="mt-2 text-sm">
                        {preview.plan.uncovered_requirements.join(', ')}
                      </p>
                    </div>
                  )}
                </section>

                <section className="border border-black bg-white p-6 shadow-sw-default">
                  <h2 className="font-serif text-2xl uppercase">{t('resumeGeneration.preview')}</h2>
                  {preview.resume_data.summary && (
                    <p className="mt-4 border-b border-black pb-4 text-sm">
                      {preview.resume_data.summary}
                    </p>
                  )}
                  {[
                    ...(preview.resume_data.workExperience ?? []),
                    ...(preview.resume_data.personalProjects ?? []),
                  ].map((item) => {
                    const title = 'name' in item ? item.name : 'title' in item ? item.title : '';
                    return (
                      <article key={item.id} className="mt-5">
                        <h3 className="font-bold">{title}</h3>
                        <p className="font-mono text-xs text-steel-grey">{item.years}</p>
                        <ul className="mt-2 list-disc space-y-1 pl-5 text-sm">
                          {(item.description ?? []).map((bullet) => (
                            <li key={bullet}>{bullet}</li>
                          ))}
                        </ul>
                      </article>
                    );
                  })}
                  <div className="mt-6 flex justify-end">
                    <Button
                      onClick={confirm}
                      disabled={!preview.validation.valid || confirming}
                      className="rounded-none border-2 border-black"
                    >
                      {confirming ? (
                        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                      ) : (
                        <CheckCircle2 className="mr-2 h-4 w-4" />
                      )}
                      {confirming
                        ? t('resumeGeneration.confirming')
                        : t('resumeGeneration.confirm')}
                    </Button>
                  </div>
                </section>
              </>
            )}
          </main>
        </div>
      </div>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="border border-black bg-background p-4 shadow-sw-sm">
      <p className="font-mono text-xs font-bold uppercase text-steel-grey">{label}</p>
      <p className="mt-2 font-serif text-3xl">{value}</p>
    </div>
  );
}
