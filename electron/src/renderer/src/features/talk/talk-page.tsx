import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { useTranslation } from 'react-i18next';
import { MicIcon, MicOffIcon, PhoneOffIcon, SendHorizontalIcon, Settings2Icon } from 'lucide-react';
import { WorkspaceHeader } from '@/components/app-shell/workspace-header';
import { ProfileAvatar } from '@/components/profile-avatar';
import { Button } from '@/components/ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { LanguagePicker } from '@/features/clone/language-picker';
import { useProfiles } from '@/hooks/use-profiles';
import { getAgentModels } from '@/lib/api/agent';
import { cn } from '@/lib/utils';
import { AgentClient, type AgentSettings, type AgentSnapshot } from './agent-client';
import { VoiceBars } from './voice-bars';
import './talk-page.css';

const SETTINGS_KEY = 'voiceym.talk.settings';
/** The model picker's value for "use the app's language model". */
const APP_MODEL = 'app';
/** Languages Talk answers with a multilingual model (the backend switches hearing and voice too). */
const INDIAN_LANGUAGES = new Set([
  'Hindi',
  'Bengali',
  'Tamil',
  'Telugu',
  'Marathi',
  'Gujarati',
  'Kannada',
  'Malayalam',
  'Panjabi',
  'Urdu',
  'Odia',
  'Assamese',
  'Nepali',
]);

function loadSettings(): AgentSettings {
  const fallback: AgentSettings = {
    instructions: '',
    voice: '',
    language: 'Auto',
    // Empty until the user picks one: Talk then uses the lightest installed Ollama model.
    model: '',
    bargeIn: true,
    greet: false,
  };
  try {
    const saved = JSON.parse(localStorage.getItem(SETTINGS_KEY) ?? 'null');
    return saved && typeof saved === 'object' ? { ...fallback, ...saved } : fallback;
  } catch {
    return fallback;
  }
}

function saveSettings(settings: AgentSettings): void {
  try {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
  } catch {
    // Settings simply aren't remembered.
  }
}

/** One client for the whole app, so leaving the page doesn't drop the call mid-sentence. */
const client = new AgentClient();

function useAgent(): AgentSnapshot {
  return useSyncExternalStore(
    (notify) => client.subscribe(notify),
    () => client.state,
  );
}

export function TalkPage() {
  const { t } = useTranslation();
  const agent = useAgent();
  const { data: profiles = [] } = useProfiles();
  const { data: ollama } = useQuery({
    queryKey: ['agent-models'],
    queryFn: getAgentModels,
    staleTime: 30_000,
  });
  const [settings, setSettings] = useState(loadSettings);
  const [draft, setDraft] = useState('');
  const [setupOpen, setSetupOpen] = useState(true);
  const transcriptRef = useRef<HTMLOListElement>(null);
  const live = agent.mode !== 'idle';

  const update = useCallback((patch: Partial<AgentSettings>) => {
    setSettings((current) => {
      const next = { ...current, ...patch };
      saveSettings(next);
      return next;
    });
  }, []);

  const installed = ollama?.models ?? [];
  const chosenModel =
    settings.model === APP_MODEL || installed.some((model) => model.name === settings.model)
      ? settings.model
      : (INDIAN_LANGUAGES.has(settings.language) && ollama?.multilingual_default) ||
        ollama?.default ||
        APP_MODEL;
  const wire = useMemo<AgentSettings>(
    () => ({
      ...settings,
      language: settings.language === 'Auto' ? '' : settings.language,
      model: chosenModel === APP_MODEL ? '' : chosenModel,
    }),
    [settings, chosenModel],
  );
  const modelItems = [
    { value: APP_MODEL, label: t('talk.model_app') },
    ...installed.map((model) => ({
      value: model.name,
      label: `${model.name} · ${(model.size / 1e9).toFixed(1)} GB`,
    })),
  ];

  const toggleCall = useCallback(() => {
    if (client.active) void client.stop();
    else {
      client.reset();
      void client.start(wire);
    }
  }, [wire]);

  const send = useCallback(async () => {
    const text = draft.trim();
    if (!text) return;
    setDraft('');
    if (!client.active) {
      client.reset();
      await client.start(wire, { withMic: false });
    }
    client.say(text);
  }, [draft, wire]);

  // Space starts or ends the conversation when you aren't typing somewhere.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.code !== 'Space' || event.repeat) return;
      const target = event.target as HTMLElement | null;
      if (target?.closest('input, textarea, select, button, [contenteditable="true"], [role="combobox"]'))
        return;
      event.preventDefault();
      toggleCall();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [toggleCall]);

  useEffect(() => {
    const list = transcriptRef.current;
    list?.lastElementChild?.scrollIntoView({ block: 'nearest' });
  }, [agent.lines]);

  const voice = profiles.find((profile) => profile.id === settings.voice);
  const showIssue = agent.issue && (agent.mode === 'idle' || agent.mode === 'listening');
  const status = showIssue ? `issue_${agent.issue}` : agent.mode;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <WorkspaceHeader>
        <h1 className="talk-title">{t('talk.title')}</h1>
        <Button
          variant="ghost"
          size="sm"
          className="ml-2 gap-2 text-muted-foreground"
          aria-pressed={setupOpen}
          onClick={() => setSetupOpen((open) => !open)}
        >
          <Settings2Icon />
          {t('talk.setup')}
        </Button>
      </WorkspaceHeader>
      <div className={cn('talk-layout', setupOpen && 'talk-layout-with-setup')}>
        <section className="talk-stage" aria-label={t('talk.conversation')}>
          <div className="talk-presence">
            <VoiceBars mode={agent.mode} level={() => client.level()} />
            <div className="talk-state" role="status" aria-live="polite">
              <p className="talk-state-title">{t(`talk.state.${status}.title`)}</p>
              <p className="talk-state-hint">{t(`talk.state.${status}.hint`)}</p>
            </div>
          </div>

          <ol ref={transcriptRef} className="talk-transcript" aria-label={t('talk.transcript')}>
            {agent.lines.length === 0 && <li className="talk-empty">{t('talk.empty')}</li>}
            {agent.lines.map((line) => (
              <li key={line.id} className={cn('talk-line', `talk-line-${line.role}`)}>
                <span className="talk-line-who">
                  {line.role === 'user' ? t('talk.you') : (voice?.name ?? t('talk.agent'))}
                </span>
                <span className={cn('talk-line-text', line.pending && 'talk-line-pending')}>
                  {line.pending ? t('talk.hearing') : line.text}
                  {line.interrupted && <em className="talk-line-cut"> {t('talk.interrupted')}</em>}
                </span>
              </li>
            ))}
          </ol>

          <form
            className="talk-dock"
            onSubmit={(event) => {
              event.preventDefault();
              void send();
            }}
          >
            <label htmlFor="talk-input" className="sr-only">
              {t('talk.type')}
            </label>
            <input
              id="talk-input"
              className="talk-input"
              value={draft}
              placeholder={t('talk.type')}
              autoComplete="off"
              onChange={(event) => setDraft(event.target.value)}
            />
            {draft.trim() ? (
              <button type="submit" className="talk-dock-button" aria-label={t('talk.send')}>
                <SendHorizontalIcon />
              </button>
            ) : (
              live && (
                <button
                  type="button"
                  className="talk-dock-button"
                  aria-label={agent.micOn ? t('talk.mute') : t('talk.unmute')}
                  onClick={() => void client.toggleMic()}
                >
                  {agent.micOn ? <MicIcon /> : <MicOffIcon />}
                </button>
              )
            )}
            <button
              type="button"
              className={cn('talk-mic', live && 'talk-mic-live')}
              aria-label={live ? t('talk.end') : t('talk.start')}
              onClick={toggleCall}
            >
              {live ? <PhoneOffIcon /> : <MicIcon />}
            </button>
          </form>
        </section>

        {setupOpen && (
          <aside className="talk-setup" aria-label={t('talk.setup')}>
            <h2 className="talk-setup-title">{t('talk.setup')}</h2>
            <p className="talk-setup-note">{t('talk.setup_note')}</p>

            <div className="talk-field">
              <label htmlFor="talk-instructions">{t('talk.instructions')}</label>
              <Textarea
                id="talk-instructions"
                rows={6}
                value={settings.instructions}
                placeholder={t('talk.instructions_placeholder')}
                onChange={(event) => update({ instructions: event.target.value })}
              />
            </div>

            <div className="talk-field">
              <span id="talk-voice-label">{t('talk.voice')}</span>
              <ul className="talk-voices" aria-labelledby="talk-voice-label">
                <li>
                  <button
                    type="button"
                    aria-pressed={!settings.voice}
                    className="talk-voice"
                    onClick={() => update({ voice: '' })}
                  >
                    <ProfileAvatar name="♪" />
                    <span>{t('talk.voice_default')}</span>
                  </button>
                </li>
                {profiles.map((profile) => (
                  <li key={profile.id}>
                    <button
                      type="button"
                      aria-pressed={settings.voice === profile.id}
                      className="talk-voice"
                      onClick={() => update({ voice: profile.id })}
                    >
                      <ProfileAvatar name={profile.name} imageUrl={profile.image_url} />
                      <span>{profile.name}</span>
                    </button>
                  </li>
                ))}
              </ul>
              <Link to="/clone" className="talk-link">
                {t('talk.add_voice')}
              </Link>
            </div>

            <div className="talk-field">
              <span id="talk-model-label">{t('talk.model')}</span>
              <Select
                items={modelItems}
                value={chosenModel}
                onValueChange={(model) => update({ model: String(model) })}
              >
                <SelectTrigger aria-labelledby="talk-model-label" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {modelItems.map((item) => (
                    <SelectItem key={item.value} value={item.value}>
                      {item.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="talk-setup-note">
                {ollama?.available === false ? t('talk.model_unavailable') : t('talk.model_hint')}
              </p>
            </div>

            <div className="talk-field">
              <span>{t('talk.language')}</span>
              <LanguagePicker
                value={settings.language}
                onValueChange={(language) => update({ language })}
                recentsScope="talk-language"
              />
            </div>

            <label className="talk-toggle">
              <span>
                <span className="talk-toggle-label">{t('talk.barge_in')}</span>
                <span className="talk-toggle-hint">{t('talk.barge_in_hint')}</span>
              </span>
              <Switch checked={settings.bargeIn} onCheckedChange={(bargeIn) => update({ bargeIn })} />
            </label>
            <label className="talk-toggle">
              <span>
                <span className="talk-toggle-label">{t('talk.greet')}</span>
                <span className="talk-toggle-hint">{t('talk.greet_hint')}</span>
              </span>
              <Switch checked={settings.greet} onCheckedChange={(greet) => update({ greet })} />
            </label>
            {live && <p className="talk-setup-note">{t('talk.applies_next')}</p>}
          </aside>
        )}
      </div>
    </div>
  );
}
