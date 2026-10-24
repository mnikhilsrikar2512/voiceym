import { Link } from '@tanstack/react-router';
import {
  AudioLinesIcon,
  BlocksIcon,
  BookOpenIcon,
  FilmIcon,
  FolderIcon,
  HomeIcon,
  LayersIcon,
  LibraryIcon,
  MessagesSquareIcon,
  MicIcon,
  TypeIcon,
  UsersRoundIcon,
  WandSparklesIcon,
  WorkflowIcon,
  WrenchIcon,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { setWorkspace } from '@/lib/store/workspace';
import { cn } from '@/lib/utils';
import './workspace-menu.css';

type Destination = readonly [
  to:
    | '/'
    | '/talk'
    | '/clone'
    | '/stories'
    | '/audiobook'
    | '/projects'
    | '/tools'
    | '/batch'
    | '/gallery'
    | '/dub'
    | '/design'
    | '/transcriptions'
    | '/personas'
    | '/calls'
    | '/integrations',
  label: string,
  hint: string,
  icon: typeof AudioLinesIcon,
  activate?: () => void,
];

const openSaved = () => setWorkspace({ libraryOpen: true, libraryTab: 'voices' });

/** Every destination is visible and named for what it does: no collapsed
 *  groups to discover, and a one-line hint on each item. */
const sections: { label?: string; items: Destination[] }[] = [
  {
    items: [
      ['/', 'nav.home', 'navHint.home', HomeIcon],
      ['/talk', 'nav.talk', 'navHint.talk', MessagesSquareIcon],
    ],
  },
  {
    label: 'nav.section_create',
    items: [
      ['/clone', 'nav.speak', 'navHint.speak', TypeIcon, openSaved],
      ['/design', 'nav.design_voice', 'navHint.design', WandSparklesIcon],
      ['/dub', 'nav.dub_video', 'navHint.dub', FilmIcon],
      ['/transcriptions', 'nav.transcribe', 'navHint.transcribe', MicIcon],
      ['/audiobook', 'audiobook.title', 'navHint.audiobook', BookOpenIcon],
      ['/stories', 'nav.stories', 'navHint.stories', AudioLinesIcon],
      ['/batch', 'nav.batch_dub', 'navHint.batch', LayersIcon],
    ],
  },
  {
    label: 'nav.section_library',
    items: [
      ['/personas', 'nav.my_voices', 'navHint.my_voices', UsersRoundIcon, openSaved],
      ['/gallery', 'nav.voice_gallery', 'navHint.gallery', LibraryIcon],
      ['/projects', 'projects.title', 'navHint.projects', FolderIcon],
    ],
  },
  {
    label: 'nav.section_more',
    items: [
      ['/calls', 'workflows.title', 'navHint.workflows', WorkflowIcon],
      ['/tools', 'tools.title', 'navHint.tools', WrenchIcon],
      ['/integrations', 'integrationCatalog.title', 'navHint.integrations', BlocksIcon],
    ],
  },
];

const itemClass =
  'workspace-nav-item group relative flex min-w-0 items-center rounded-lg text-sm text-sidebar-foreground/80 outline-none transition-colors duration-150 hover:bg-sidebar-accent hover:text-sidebar-foreground focus-visible:ring-2 focus-visible:ring-ring';
const iconClass = 'workspace-nav-icon size-4 shrink-0 text-muted-foreground transition-colors duration-150';

function NavigationLink({
  destination: [to, label, hint, Icon, activate],
  compact = false,
}: {
  destination: Destination;
  compact?: boolean;
}) {
  const { t } = useTranslation();
  const link = (
    <Link
      to={to}
      aria-label={compact ? t(label) : undefined}
      aria-description={t(hint)}
      title={compact ? undefined : t(hint)}
      onClick={() => activate?.()}
      className={cn(itemClass, compact ? 'h-9 justify-center px-0' : 'h-8 gap-2.5 px-2.5')}
      activeOptions={{ exact: to === '/' }}
      activeProps={{
        className: 'bg-sidebar-accent font-semibold text-sidebar-foreground',
        'aria-current': 'page',
      }}
    >
      <Icon className={iconClass} aria-hidden="true" />
      {!compact && <span className="truncate">{t(label)}</span>}
    </Link>
  );
  if (!compact) return link;
  return (
    <Tooltip>
      <TooltipTrigger render={link} />
      <TooltipContent side="right">
        <span className="block font-medium">{t(label)}</span>
        <span className="block opacity-75">{t(hint)}</span>
      </TooltipContent>
    </Tooltip>
  );
}

export function WorkspaceNavigation({ compact = false }: { compact?: boolean }) {
  const { t } = useTranslation();
  return (
    <nav
      data-slot="workspace-navigation"
      aria-label={t('nav.workspaces')}
      className={cn(
        'min-h-0 overflow-y-auto overscroll-contain py-2 [scrollbar-width:thin]',
        compact ? 'px-1.5' : 'shrink-0 px-3',
      )}
    >
      {sections.map(({ label, items }, index) => (
        <div
          key={label ?? 'start'}
          role={label ? 'group' : undefined}
          aria-label={label ? t(label) : undefined}
          className={cn('space-y-0.5', index > 0 && (compact ? 'mt-1.5 border-t border-sidebar-border/60 pt-1.5' : 'mt-3'))}
        >
          {label && !compact && (
            <div className="px-2.5 pb-1 text-[11px] font-medium tracking-wide text-muted-foreground/80 uppercase">
              {t(label)}
            </div>
          )}
          {items.map((destination) => (
            <NavigationLink key={destination[0] + destination[1]} destination={destination} compact={compact} />
          ))}
        </div>
      ))}
    </nav>
  );
}
