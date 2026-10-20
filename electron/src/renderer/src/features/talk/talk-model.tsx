import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { apiJson } from '@/lib/api/client';

const PROVIDERS = '/api/settings/llm-providers';
const OLLAMA = 'ollama';

type Catalogue = { active: string | null; providers: { id: string; model: string }[] };
type ModelList = { ok: boolean; models: string[] };

/** Embedding models can't hold a conversation, so they stay out of the list. */
export function chatModels(models: string[]): string[] {
  return models.filter((name) => !/embed/i.test(name));
}

/**
 * Picks which local Ollama model answers in Talk. Lists whatever the user has
 * pulled, so adding a model is just `ollama pull <name>`; nothing leaves the
 * machine and no key is needed.
 */
export function TalkModelPicker() {
  const { t } = useTranslation();
  const client = useQueryClient();
  const catalogue = useQuery({
    queryKey: ['llm-providers'],
    queryFn: ({ signal }) => apiJson<Catalogue>(PROVIDERS, { signal }),
  });
  const installed = useQuery({
    queryKey: ['llm-provider-models', OLLAMA],
    queryFn: ({ signal }) => apiJson<ModelList>(`${PROVIDERS}/${OLLAMA}/models`, { signal }),
    staleTime: 15_000,
  });
  const models = chatModels(installed.data?.ok ? installed.data.models : []);
  const ollama = catalogue.data?.providers.find((provider) => provider.id === OLLAMA);
  const current = catalogue.data?.active === OLLAMA ? ollama?.model : undefined;

  const choose = async (model: string) => {
    await apiJson(`${PROVIDERS}/${OLLAMA}`, {
      method: 'PUT',
      body: JSON.stringify({ model, make_active: true }),
      headers: { 'Content-Type': 'application/json' },
    });
    await client.invalidateQueries({ queryKey: ['llm-providers'] });
  };

  return (
    <div className="talk-field">
      <span id="talk-model-label">{t('talk.model')}</span>
      {models.length ? (
        <Select value={current ?? null} onValueChange={(value) => value && void choose(String(value))}>
          <SelectTrigger aria-labelledby="talk-model-label" className="w-full">
            <SelectValue placeholder={t('talk.model')}>{current ?? t('talk.model')}</SelectValue>
          </SelectTrigger>
          <SelectContent>
            {models.map((name) => (
              <SelectItem key={name} value={name}>
                {name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      ) : (
        <p className="talk-setup-note">{t('talk.model_missing')}</p>
      )}
      <p className="talk-setup-note">{t('talk.model_hint')}</p>
    </div>
  );
}
