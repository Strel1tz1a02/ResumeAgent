'use client';

import Send from 'lucide-react/dist/esm/icons/send';
import X from 'lucide-react/dist/esm/icons/x';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { useTranslations } from '@/lib/i18n';
import { useExperienceAiChat } from './use-experience-ai-chat';

function proposalValue(proposal: NonNullable<ReturnType<typeof useExperienceAiChat>['proposal']>) {
  return proposal.proposal.suggested_content;
}

export function ExperienceChatPanel() {
  const { t } = useTranslations();
  const chat = useExperienceAiChat();
  if (chat.phase === 'idle') return null;
  return (
    <aside className="sticky bottom-4 z-20 border border-black bg-background p-4 shadow-sw-lg">
      <div className="flex items-center justify-between gap-3 border-b border-black pb-3">
        <div>
          <p className="font-mono text-xs font-bold uppercase tracking-widest text-primary">
            {t('experiences.ai.title')}
          </p>
          <p className="font-mono text-[10px] uppercase text-ink-soft">{chat.target?.key}</p>
        </div>
        <Button size="sm" variant="outline" onClick={() => void chat.close()}>
          <X className="h-4 w-4" />
        </Button>
      </div>
      <div className="max-h-64 space-y-3 overflow-y-auto py-3">
        {chat.messages.map((message) => (
          <div
            key={message.id}
            className={
              message.role === 'user'
                ? 'ml-8 border border-black bg-primary p-3 text-sm text-white'
                : 'mr-8 border border-black bg-paper-tint p-3 text-sm'
            }
          >
            {message.content}
          </div>
        ))}
      </div>
      {chat.proposal && (
        <div className="mb-3 border-2 border-warning bg-orange-50 p-3">
          <p className="font-mono text-xs font-bold uppercase">{t('experiences.ai.approval')}</p>
          <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap text-xs">
            {JSON.stringify(proposalValue(chat.proposal), null, 2)}
          </pre>
          <div className="mt-3 flex gap-2">
            <Button size="sm" onClick={() => void chat.resolve('approve')}>
              {t('experiences.ai.approve')}
            </Button>
            <Button size="sm" variant="outline" onClick={() => void chat.resolve('reject')}>
              {t('experiences.ai.reject')}
            </Button>
          </div>
        </div>
      )}
      <p className="mb-2 font-mono text-[10px] uppercase text-warning">
        {t('experiences.ai.unsavedHint')}
      </p>
      <div className="flex gap-2">
        <Textarea
          value={chat.input}
          onChange={(event) => chat.setInput(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') event.stopPropagation();
          }}
          rows={2}
          aria-label={t('experiences.ai.message')}
        />
        <Button
          className="self-stretch"
          onClick={() => void chat.send()}
          disabled={chat.phase !== 'ready' || !chat.input.trim()}
        >
          <Send className="h-4 w-4" />
        </Button>
      </div>
      {chat.error && <p className="mt-2 text-xs text-destructive">{t('experiences.ai.error')}</p>}
    </aside>
  );
}
