'use client';

import { useEffect, useState } from 'react';
import Loader2 from 'lucide-react/dist/esm/icons/loader-2';

import { useTranslations } from '@/lib/i18n';

/**
 * 在同步生成请求期间展示轻量阶段提示。
 * 只展示真实等待时长和不确定进度，不冒充后端实时阶段事件。
 */
export function GenerationProgress() {
  const { t } = useTranslations();
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const steps = [
    t('resumeGeneration.progressAnalyze'),
    t('resumeGeneration.progressRetrieve'),
    t('resumeGeneration.progressPlan'),
    t('resumeGeneration.progressDraft'),
    t('resumeGeneration.progressValidate'),
  ];

  useEffect(() => {
    const interval = window.setInterval(() => {
      setElapsedSeconds((current) => current + 1);
    }, 1000);
    return () => window.clearInterval(interval);
  }, []);

  return (
    <section className="border border-black bg-background p-5 shadow-sw-default" aria-busy="true">
      <div className="flex items-center justify-between gap-4">
        <div>
          <p className="font-mono text-xs font-bold uppercase text-blue-700">
            {t('resumeGeneration.progressTitle')}
          </p>
          <p className="mt-2 flex items-center gap-2 text-sm font-semibold" role="status">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            {t('resumeGeneration.progressDescription')}
          </p>
        </div>
        <span className="font-mono text-xs font-bold text-steel-grey" aria-hidden="true">
          {t('resumeGeneration.progressElapsed', { seconds: elapsedSeconds })}
        </span>
      </div>

      <div
        className="mt-4 h-2 overflow-hidden border border-black bg-white"
        role="progressbar"
        aria-label={t('resumeGeneration.progressTitle')}
      >
        <span className="block h-full w-full animate-pulse bg-blue-600" />
      </div>

      <ol className="mt-4 grid gap-2 text-xs sm:grid-cols-2 lg:grid-cols-5">
        {steps.map((step, index) => (
          <li key={step} className="border border-black/30 px-3 py-2 text-steel-grey">
            <span className="mr-2 font-mono">{index + 1}.</span>
            {step}
          </li>
        ))}
      </ol>
    </section>
  );
}
