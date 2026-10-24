import { render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';

vi.mock('@tanstack/react-router', () => ({
  Link: ({ to, children, ...props }: any) => (
    <a href={to} {...props}>
      {children}
    </a>
  ),
}));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('@/lib/store/workspace', () => ({ setWorkspace: vi.fn() }));
vi.mock('@/components/app-shell/workspace-header', () => ({
  WorkspaceHeader: ({ children }: any) => <header>{children}</header>,
}));
import { HomePage, homeTasks } from './home-page';

it('asks what to do and links every task to its page with a plain description', () => {
  render(<HomePage />);
  expect(screen.getByRole('heading', { name: 'home.title' })).toBeVisible();
  for (const task of homeTasks) {
    const link = screen.getByRole('link', { name: new RegExp(task.title) });
    expect(link).toHaveAttribute('href', task.to);
    expect(link).toHaveTextContent(task.desc);
  }
  expect(screen.getByRole('link', { name: /home.talk_title/ })).toHaveAttribute('href', '/talk');
  expect(screen.getByRole('link', { name: /nav.voice_gallery/ })).toHaveAttribute('href', '/gallery');
});
