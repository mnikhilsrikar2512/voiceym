import { render, screen, within } from '@testing-library/react';
import { expect, it, vi } from 'vitest';

vi.mock('@tanstack/react-router', () => ({
  Link: ({ to, activeProps: _activeProps, activeOptions: _activeOptions, children, ...props }: any) => (
    <a href={to} {...props}>
      {children}
    </a>
  ),
}));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('@/lib/store/workspace', () => ({ setWorkspace: vi.fn() }));
import { WorkspaceNavigation } from './workspace-menu';

it('shows every destination by name, grouped under visible section labels', () => {
  render(<WorkspaceNavigation />);
  expect(screen.getByRole('link', { name: 'nav.home' })).toHaveAttribute('href', '/');
  expect(screen.getByRole('link', { name: 'nav.talk' })).toHaveAttribute('href', '/talk');
  const create = screen.getByRole('group', { name: 'nav.section_create' });
  expect(within(create).getByText('nav.section_create')).toBeVisible();
  expect(within(create).getByRole('link', { name: 'nav.dub_video' })).toHaveAttribute('href', '/dub');
  const library = screen.getByRole('group', { name: 'nav.section_library' });
  expect(within(library).getByRole('link', { name: 'nav.voice_gallery' })).toHaveAttribute(
    'href',
    '/gallery',
  );
  expect(screen.getByRole('link', { name: 'workflows.title' })).toHaveAttribute('href', '/calls');
  // Nothing is hidden behind a collapsed group.
  expect(screen.queryByRole('button')).toBeNull();
});

it('describes what each destination does', () => {
  render(<WorkspaceNavigation />);
  const speak = screen.getByRole('link', { name: 'nav.speak' });
  expect(speak).toHaveAttribute('aria-description', 'navHint.speak');
  expect(speak).toHaveAttribute('title', 'navHint.speak');
});

it('keeps every destination one click away on the compact rail', () => {
  render(<WorkspaceNavigation compact />);
  expect(screen.getByRole('link', { name: 'nav.speak' })).toHaveAttribute('href', '/clone');
  expect(screen.getByRole('link', { name: 'nav.voice_gallery' })).toHaveAttribute(
    'href',
    '/gallery',
  );
  expect(screen.getByRole('link', { name: 'integrationCatalog.title' })).toHaveAttribute(
    'href',
    '/integrations',
  );
  // Section labels give way to dividers on the rail.
  expect(screen.queryByText('nav.section_create')).toBeNull();
});
