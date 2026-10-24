import { Link } from '@tanstack/react-router';
import { useTranslation } from 'react-i18next';
import {
  ArrowRightIcon,
  AudioLinesIcon,
  BlocksIcon,
  BookOpenIcon,
  FilmIcon,
  FingerprintIcon,
  FolderIcon,
  LibraryIcon,
  MessagesSquareIcon,
  MicIcon,
  TypeIcon,
  UsersRoundIcon,
  WandSparklesIcon,
  WorkflowIcon,
  WrenchIcon,
} from 'lucide-react';
import { WorkspaceHeader } from '@/components/app-shell/workspace-header';
import { setWorkspace } from '@/lib/store/workspace';
import './home-page.css';

type Route =
  | '/talk'
  | '/clone'
  | '/design'
  | '/dub'
  | '/transcriptions'
  | '/audiobook'
  | '/stories'
  | '/personas'
  | '/gallery'
  | '/projects'
  | '/calls'
  | '/tools'
  | '/integrations';

type Task = { to: Route; icon: typeof MicIcon; title: string; desc: string; onOpen?: () => void };

const openSavedVoices = () => setWorkspace({ libraryOpen: true, libraryTab: 'voices' });

/** The main things people come to do, in plain words, most common first. */
export const homeTasks: Task[] = [
  { to: '/talk', icon: MessagesSquareIcon, title: 'home.talk_title', desc: 'home.talk_desc' },
  { to: '/clone', icon: TypeIcon, title: 'home.speak_title', desc: 'home.speak_desc' },
  {
    to: '/clone',
    icon: FingerprintIcon,
    title: 'home.clone_title',
    desc: 'home.clone_desc',
    onOpen: openSavedVoices,
  },
  { to: '/design', icon: WandSparklesIcon, title: 'home.design_title', desc: 'home.design_desc' },
  { to: '/dub', icon: FilmIcon, title: 'home.dub_title', desc: 'home.dub_desc' },
  {
    to: '/transcriptions',
    icon: MicIcon,
    title: 'home.transcribe_title',
    desc: 'home.transcribe_desc',
  },
  { to: '/audiobook', icon: BookOpenIcon, title: 'home.audiobook_title', desc: 'home.audiobook_desc' },
  { to: '/stories', icon: AudioLinesIcon, title: 'home.stories_title', desc: 'home.stories_desc' },
];

/** Places people return to once they have made something. */
export const homeMore: Task[] = [
  { to: '/personas', icon: UsersRoundIcon, title: 'nav.my_voices', desc: 'navHint.my_voices', onOpen: openSavedVoices },
  { to: '/gallery', icon: LibraryIcon, title: 'nav.voice_gallery', desc: 'navHint.gallery' },
  { to: '/projects', icon: FolderIcon, title: 'projects.title', desc: 'navHint.projects' },
  { to: '/calls', icon: WorkflowIcon, title: 'workflows.title', desc: 'navHint.workflows' },
  { to: '/tools', icon: WrenchIcon, title: 'tools.title', desc: 'navHint.tools' },
  { to: '/integrations', icon: BlocksIcon, title: 'integrationCatalog.title', desc: 'navHint.integrations' },
];

export function HomePage() {
  const { t } = useTranslation();
  return (
    <div className="flex h-full min-h-0 flex-col">
      <WorkspaceHeader>
        <h1 className="text-sm font-semibold">{t('nav.home')}</h1>
      </WorkspaceHeader>
      <main className="min-h-0 flex-1 overflow-y-auto">
        <div className="home-page mx-auto w-full max-w-5xl px-6 py-10 sm:px-10">
          <header className="mb-8">
            <h2 className="font-heading text-2xl font-semibold tracking-tight sm:text-3xl">
              {t('home.title')}
            </h2>
            <p className="mt-2 text-sm text-muted-foreground">{t('home.subtitle')}</p>
          </header>

          <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4" aria-label={t('home.tasks')}>
            {homeTasks.map(({ to, icon: Icon, title, desc, onOpen }) => (
              <li key={title}>
                <Link to={to} onClick={onOpen} className="home-task group">
                  <span className="home-task-icon" aria-hidden="true">
                    <Icon className="size-5" />
                  </span>
                  <span className="mt-4 flex items-center gap-1.5 text-[15px] font-semibold">
                    {t(title)}
                    <ArrowRightIcon className="home-task-arrow size-4" aria-hidden="true" />
                  </span>
                  <span className="mt-1 text-[13px] leading-5 text-muted-foreground">{t(desc)}</span>
                </Link>
              </li>
            ))}
          </ul>

          <h3 className="mt-10 mb-3 text-xs font-medium tracking-wide text-muted-foreground uppercase">
            {t('home.more')}
          </h3>
          <ul className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {homeMore.map(({ to, icon: Icon, title, desc, onOpen }) => (
              <li key={to}>
                <Link to={to} onClick={onOpen} className="home-link">
                  <Icon className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
                  <span className="min-w-0">
                    <span className="block truncate text-sm font-medium">{t(title)}</span>
                    <span className="block truncate text-xs text-muted-foreground">{t(desc)}</span>
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </div>
      </main>
    </div>
  );
}
