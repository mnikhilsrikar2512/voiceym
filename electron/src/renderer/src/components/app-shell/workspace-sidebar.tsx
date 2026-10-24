import { useEffect, useRef, useState } from 'react';
import { Link, useRouterState } from '@tanstack/react-router';
import { useTranslation } from 'react-i18next';
import { PanelLeftIcon, PanelLeftOpenIcon, SettingsIcon } from 'lucide-react';
import { brandIcon } from '@/lib/brand';
import { appVersion, isMac } from '@/components/bridge';
import { cn } from '@/lib/utils';
import { Button, buttonVariants } from '@/components/ui/button';
import { usePaneResize } from '@/hooks/use-pane-resize';
import { useWorkspace } from '@/lib/store/workspace';
import { VoicesSidebar } from '@/features/clone/voices-sidebar';
import { WorkspaceNavigation } from './workspace-menu';
import { StatusBar } from './status-bar';

export function versionTranslationKey(development: boolean) {
  return development ? ('app.version_dev' as const) : ('app.version' as const);
}

/** How long the pointer must rest on the rail before the sidebar opens, and
 *  how long it may wander off before it closes: no flicker on a pass-through. */
const OPEN_DELAY_MS = 120;
const CLOSE_DELAY_MS = 220;

/** Popovers opened from the sidebar render outside it; focus moving into one
 *  is still inside the sidebar. */
const isSidebarLayer = (node: Node | null, sidebar: Element) =>
  !!node &&
  (sidebar.contains(node) ||
    (node instanceof Element && !!node.closest('[data-slot=popover-content],[role=dialog],[role=menu]')));

/**
 * The main sidebar is a slim icon rail that opens over the page while the
 * pointer (or keyboard focus) is on it, and closes when it leaves. It never
 * takes width from the workspace.
 */
export function WorkspaceSidebar() {
  const { t } = useTranslation();
  const mac = isMac();
  const versionLabel = t(versionTranslationKey(import.meta.env.DEV), {
    version: appVersion(),
  });
  const { libraryTab } = useWorkspace();
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  const [open, setOpen] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const hovered = useRef(false);
  const overlayRef = useRef<HTMLDivElement>(null);
  const clearTimer = () => {
    if (timer.current !== null) clearTimeout(timer.current);
    timer.current = null;
  };
  const schedule = (next: boolean) => {
    clearTimer();
    timer.current = setTimeout(() => setOpen(next), next ? OPEN_DELAY_MS : CLOSE_DELAY_MS);
  };
  useEffect(() => clearTimer, []);
  // Choosing a destination closes the sidebar.
  useEffect(() => {
    clearTimer();
    setOpen(false);
  }, [pathname]);
  const sidebarResize = usePaneResize({
    storageKey: 'voiceym.library-width',
    side: 'left',
    minimum: mac ? 288 : 220,
    initial: mac ? 288 : 256,
    maximum: 360,
    reserve: 640,
    enabled: open,
    overlay: true,
  });
  return (
    <div
      data-slot="workspace-sidebar"
      data-open={open || undefined}
      className="relative z-40 h-dvh shrink-0"
      onMouseEnter={() => {
        hovered.current = true;
        schedule(true);
      }}
      onMouseLeave={() => {
        hovered.current = false;
        schedule(false);
      }}
      onBlur={(event) => {
        // Focus dropping to the page while the pointer rests here keeps it open.
        const next = event.relatedTarget as Node | null;
        if (next ? !isSidebarLayer(next, event.currentTarget) : !hovered.current) schedule(false);
      }}
      onKeyDown={(event) => {
        // Escape inside one of its popovers closes only that popover.
        if (event.key !== 'Escape' || !open || !event.currentTarget.contains(event.target as Node)) return;
        setOpen(false);
        (document.activeElement as HTMLElement | null)?.blur();
      }}
    >
      <aside
        aria-label={t('nav.workspaces')}
        data-slot="compact-main-sidebar"
        className={cn(
          'brand-sidebar relative isolate grid h-dvh min-h-0 shrink-0 grid-rows-[auto_minmax(0,1fr)_auto_auto] overflow-hidden bg-sidebar',
          mac ? 'w-16' : 'w-12 border-r border-border/50',
        )}
      >
        {mac && (
          <span
            aria-hidden="true"
            data-slot="compact-sidebar-divider"
            className="pointer-events-none absolute top-[72px] right-0 bottom-0 w-px bg-border/50"
          />
        )}
        <div
          className={cn(
            'workspace-titlebar flex shrink-0 justify-center',
            mac ? 'min-h-[72px] items-end pb-1' : 'h-12 items-center',
          )}
        >
          {/* Touch and keyboard users have no hover: the rail opens on a press too. */}
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={t('clone.toggle_sidebar')}
            aria-expanded={open}
            onClick={() => {
              clearTimer();
              setOpen(true);
              requestAnimationFrame(() =>
                overlayRef.current?.querySelector<HTMLElement>('nav a')?.focus(),
              );
            }}
            className="shrink-0 outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {mac ? (
              <PanelLeftOpenIcon className="size-5" aria-hidden="true" />
            ) : (
              <img src={brandIcon} alt="" className="size-6 shrink-0" />
            )}
          </Button>
        </div>
        {/* Covered by the open sidebar: keep its tooltips from showing through. */}
        <div className="contents" inert={open || undefined}>
          <WorkspaceNavigation compact />
        </div>
        {!mac && <StatusBar compact />}
        <div
          className={cn(
            'shrink-0 border-t border-border/50 py-2',
            mac ? 'grid grid-cols-2 items-center gap-1 px-1' : 'flex flex-col items-center gap-1',
          )}
        >
          <Link
            to="/settings"
            aria-label={t('nav.settings')}
            title={t('nav.settings')}
            className={cn(
              buttonVariants({
                variant: 'ghost',
                size: mac ? 'icon-xs' : 'icon-sm',
              }),
              'workspace-nav-item',
            )}
          >
            <SettingsIcon className="workspace-nav-icon" />
          </Link>
          {mac && <StatusBar compact inline />}
        </div>
      </aside>
      {open && (
        <aside
          ref={sidebarResize.host}
          style={{ width: sidebarResize.width }}
          aria-label={t('clone.saved_profiles')}
          data-slot="expanded-main-sidebar"
          className="brand-sidebar workspace-sidebar-overlay absolute inset-y-0 start-0 isolate grid h-full min-h-0 min-w-0 grid-cols-[minmax(0,1fr)] grid-rows-[auto_minmax(0,auto)_minmax(5rem,1fr)_auto] overflow-hidden border-e border-border/50 bg-sidebar shadow-[12px_0_32px_-12px_rgb(0_0_0/0.45)]"
        >
          <header
            className={cn(
              'workspace-titlebar flex shrink-0 items-center gap-2 px-4',
              mac && 'pl-24',
            )}
          >
            <Link
              to="/"
              aria-label={`${t('app.name')} ${versionLabel}`}
              className="flex min-w-0 flex-1 items-center gap-2 rounded outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <img src={brandIcon} alt="" className="size-6 shrink-0" />
              <span className="flex min-w-0 flex-col leading-tight">
                <span className="truncate font-heading text-[17px] font-bold tracking-tight">{t('app.name')}</span>
                <span className="text-[11px] leading-none text-muted-foreground tabular-nums">
                  {versionLabel}
                </span>
              </span>
            </Link>
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label={t('common.close')}
              onClick={() => {
                clearTimer();
                setOpen(false);
              }}
            >
              <PanelLeftIcon />
            </Button>
          </header>
          <div
            {...sidebarResize.separatorProps}
            aria-label={t('clone.saved_profiles')}
            className="absolute inset-y-0 right-0 z-20 w-2 cursor-col-resize touch-none outline-none hover:bg-primary/15 focus-visible:bg-primary/30"
          />
          <div ref={overlayRef} className="min-h-0 min-w-0 overflow-y-auto border-b border-border/50">
            <WorkspaceNavigation />
          </div>
          <VoicesSidebar key={libraryTab} initialTab={libraryTab} />
          <div className="flex min-w-0 shrink-0 flex-col border-t border-border/50">
            <StatusBar
              footerLeading={
                mac ? (
                  <Link
                    to="/settings"
                    aria-label={t('nav.settings')}
                    title={t('nav.settings')}
                    className={cn(
                      buttonVariants({ variant: 'ghost', size: 'icon-xs' }),
                      'workspace-nav-item',
                    )}
                  >
                    <SettingsIcon className="workspace-nav-icon" />
                  </Link>
                ) : undefined
              }
            />
            {!mac && (
              <div className="flex items-center justify-between gap-2 border-t border-border/50 px-3 py-2">
                <Link
                  to="/settings"
                  className={cn(
                    buttonVariants({ variant: 'ghost', size: 'sm' }),
                    'workspace-nav-item',
                  )}
                >
                  <SettingsIcon className="workspace-nav-icon" />
                  {t('nav.settings')}
                </Link>
              </div>
            )}
          </div>
        </aside>
      )}
    </div>
  );
}
