'use client';

import { useState, type FocusEvent, type PropsWithChildren } from 'react';
import Sparkles from 'lucide-react/dist/esm/icons/sparkles';
import { Button } from '@/components/ui/button';
import type { ExperienceChatTarget } from '@/lib/api/experience-ai-chat';
import type { ExperienceFieldState } from '@/lib/api/experiences';
import { useTranslations } from '@/lib/i18n';
import { useExperienceAiChat } from './use-experience-ai-chat';

export function FieldAiEntry({
  target,
  state,
  children,
  onSave,
  saveDisabled = false,
  dirty = false,
  showAiStart = true,
  className = '',
}: PropsWithChildren<{
  target: ExperienceChatTarget;
  state?: ExperienceFieldState;
  onSave?: () => void;
  saveDisabled?: boolean;
  dirty?: boolean;
  showAiStart?: boolean;
  className?: string;
}>) {
  const { t } = useTranslations();
  const chat = useExperienceAiChat();
  const [focused, setFocused] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const onBlur = (event: FocusEvent<HTMLDivElement>) => {
    if (!event.currentTarget.contains(event.relatedTarget)) {
      setFocused(false);
      setConfirming(false);
    }
  };
  const active = chat.target?.key === target.key && chat.target.ref_id === target.ref_id;
  return (
    <div
      className={`relative border-l-4 pl-2 ${state?.status === 'complete' ? 'border-success' : 'border-warning'} ${className}`}
      onFocusCapture={() => setFocused(true)}
      onBlurCapture={onBlur}
    >
      {children}
      {focused && (
        <div className="absolute -right-1 -top-3 z-10 flex gap-1">
          {onSave && dirty && (
            <Button
              type="button"
              size="sm"
              className="h-7 px-2 font-mono text-[10px] uppercase shadow-sw-xs"
              onMouseDown={(event) => event.preventDefault()}
              onClick={onSave}
              disabled={saveDisabled}
            >
              {t('experiences.editor.saveField')}
            </Button>
          )}
          {showAiStart && !active && (
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="h-7 bg-background px-2 font-mono text-[10px] uppercase shadow-sw-xs"
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => setConfirming(true)}
            >
              <Sparkles className="h-3 w-3" /> {t('experiences.ai.startField')}
            </Button>
          )}
        </div>
      )}
      {showAiStart && focused && confirming && !active && (
        <div className="absolute -right-1 top-6 z-20 w-44 border border-black bg-background p-2 shadow-sw-xs">
          <p className="font-mono text-[10px] leading-tight">{t('experiences.ai.startConfirm')}</p>
          <div className="mt-2 flex gap-1">
            <Button
              type="button"
              size="sm"
              className="h-7 px-2 text-[10px]"
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => {
                setConfirming(false);
                void chat.start(target);
              }}
            >
              {t('experiences.ai.startConfirmAction')}
            </Button>
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="h-7 px-2 text-[10px]"
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => setConfirming(false)}
            >
              {t('common.cancel')}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
