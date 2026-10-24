import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';

const mock = vi.hoisted(() => ({
  api: vi.fn(),
  toast: { success: vi.fn(), warning: vi.fn(), error: vi.fn() },
}));

vi.mock('@/lib/api/client', () => ({
  apiJson: mock.api,
  describeError: (error: unknown) => (error instanceof Error ? error.message : String(error)),
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
vi.mock('@tanstack/react-router', () => ({
  Link: ({ children }: { children: ReactNode }) => <a href="#">{children}</a>,
}));
vi.mock('sonner', () => ({ toast: mock.toast }));

import { DiarisationSettings } from './model-settings';

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

it('confirms a diarisation engine selection after the backend accepts it', async () => {
  mock.api.mockImplementation((path: string, init?: RequestInit) => {
    if (path === '/engines/diarisation/select' && init?.method === 'POST')
      return Promise.resolve({ active: 'pyannote' });
    if (path === '/engines/diarisation')
      return Promise.resolve({
        active: 'none',
        options: [{ id: 'pyannote', label: 'pyannote 3.1', installed: true }],
      });
    return Promise.resolve({});
  });

  render(
    <QueryClientProvider client={new QueryClient()}>
      <DiarisationSettings />
    </QueryClientProvider>,
  );

  fireEvent.click(await screen.findByRole('button', { name: 'modelSettings.select' }));
  await waitFor(() => expect(mock.toast.success).toHaveBeenCalledWith('settings.engine_switched'));
});

it('shows an uninstalled runtime without a select button', async () => {
  mock.api.mockImplementation(() =>
    Promise.resolve({
      active: 'pyannote',
      options: [
        {
          id: 'pyannote',
          label: 'pyannote 3.1',
          installed: false,
          reason: 'Install the pyannote model bundle',
        },
      ],
    }),
  );
  render(
    <QueryClientProvider client={new QueryClient()}>
      <DiarisationSettings />
    </QueryClientProvider>,
  );
  await screen.findByText('Install the pyannote model bundle');
  expect(screen.queryByRole('button', { name: 'modelSettings.select' })).toBeNull();
});
