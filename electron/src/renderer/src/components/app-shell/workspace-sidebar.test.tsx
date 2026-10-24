import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

// The main navigation sits directly under the sidebar header, above the voice
// library, so its position never depends on how tall the library is.

const route = vi.hoisted(() => ({ pathname: '/clone' }));
vi.mock('@tanstack/react-router', () => ({
  useRouterState: ({ select }: any) => select({ location: route }),
  Link: ({ to, children, ...props }: any) => (
    <a href={to} {...props}>
      {children}
    </a>
  ),
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, values?: { version?: string }) =>
      key === 'app.version_dev'
        ? `v${values?.version} · Dev`
        : key === 'app.version'
          ? `v${values?.version}`
          : key,
  }),
}));
vi.mock('@/lib/store/workspace', () => ({
  useWorkspace: () => ({ libraryOpen: true, libraryTab: 'voices' }),
  setWorkspace: vi.fn(),
}));
vi.mock('@/components/bridge', () => ({ appVersion: () => '0.5.7', isMac: () => false }));
vi.mock('@/lib/brand', () => ({ brandIcon: 'icon.png', brandArtwork: 'art.png' }));
vi.mock('@/hooks/use-pane-resize', () => ({
  usePaneResize: () => ({ host: { current: null }, width: 280, separatorProps: {} }),
}));
vi.mock('@/hooks/use-backend-status', () => ({ useBackendStatus: () => ({ stage: 'ready' }) }));
vi.mock('@/features/clone/voices-sidebar', () => ({
  VoicesSidebar: () => <div data-testid="library" />,
}));
vi.mock('./workspace-menu', () => ({
  WorkspaceNavigation: () => <nav data-testid="navigation" />,
}));
vi.mock('./status-bar', () => ({ StatusBar: () => <div data-testid="status" /> }));
vi.mock('./system-notifications', () => ({ SystemNotifications: () => null }));

import { versionTranslationKey, WorkspaceSidebar } from './workspace-sidebar';

beforeEach(() => {
  vi.useFakeTimers();
  route.pathname = '/clone';
});
afterEach(() => {
  vi.useRealTimers();
});

const host = () => document.querySelector('[data-slot="workspace-sidebar"]') as HTMLElement;
const expanded = () => screen.queryByRole('complementary', { name: 'clone.saved_profiles' });

function openByHover() {
  fireEvent.mouseEnter(host());
  act(() => vi.advanceTimersByTime(200));
}

it('shows only the icon rail until the pointer rests on it', () => {
  render(<WorkspaceSidebar />);
  expect(screen.getByRole('complementary', { name: 'nav.workspaces' })).toBeInTheDocument();
  expect(expanded()).toBeNull();
  openByHover();
  expect(expanded()).toBeInTheDocument();
});

it('closes when the pointer leaves, but not on a quick pass back', () => {
  render(<WorkspaceSidebar />);
  openByHover();
  fireEvent.mouseLeave(host());
  act(() => vi.advanceTimersByTime(100));
  fireEvent.mouseEnter(host());
  act(() => vi.advanceTimersByTime(300));
  expect(expanded()).toBeInTheDocument();
  fireEvent.mouseLeave(host());
  act(() => vi.advanceTimersByTime(300));
  expect(expanded()).toBeNull();
});

it('opens from the rail button for touch and keyboard, and closes on Escape', () => {
  render(<WorkspaceSidebar />);
  fireEvent.click(screen.getByRole('button', { name: 'clone.toggle_sidebar' }));
  expect(expanded()).toBeInTheDocument();
  fireEvent.keyDown(host(), { key: 'Escape' });
  expect(expanded()).toBeNull();
});

it('places the navigation above the voice library in the open sidebar', () => {
  render(<WorkspaceSidebar />);
  openByHover();
  const navigation = screen.getAllByTestId('navigation').at(-1)!;
  const library = screen.getByTestId('library');
  const statuses = screen.getAllByTestId('status');
  expect(
    navigation.compareDocumentPosition(library) & Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
  expect(
    library.compareDocumentPosition(statuses.at(-1)!) & Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
});

it('shows the app version beneath the brand name in the open sidebar', () => {
  render(<WorkspaceSidebar />);
  openByHover();
  const brand = screen.getByRole('link', { name: 'app.name v0.5.7 · Dev' });
  expect(brand).toHaveTextContent('app.name');
  expect(brand).toHaveTextContent('v0.5.7 · Dev');
});

it('uses the plain version label outside development builds', () => {
  expect(versionTranslationKey(true)).toBe('app.version_dev');
  expect(versionTranslationKey(false)).toBe('app.version');
});

