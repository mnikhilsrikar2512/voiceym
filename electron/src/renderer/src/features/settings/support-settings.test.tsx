import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
const mock = vi.hoisted(() => ({ open: vi.fn().mockResolvedValue(undefined) }));
vi.mock('@/components/bridge', () => ({
  getBridge: () => ({ files: { openExternal: mock.open } }),
}));
vi.mock('react-i18next', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-i18next')>()),
  useTranslation: () => ({ t: (key: string) => key }),
}));
import { SupportSettings } from './support-settings';
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});
it('offers only issues, security and source links', () => {
  render(<SupportSettings />);
  expect(mock.open).not.toHaveBeenCalled();
  const links = screen.getAllByRole('link');
  expect(links.map((link) => link.getAttribute('href'))).toEqual([
    'https://github.com/mnikhilsrikar2512/voiceym/issues',
    'https://github.com/mnikhilsrikar2512/voiceym/security/advisories/new',
    'https://github.com/mnikhilsrikar2512/voiceym',
  ]);
  expect(screen.getByRole('button', { name: 'reportBug.label' })).toBeVisible();
  expect(screen.getByRole('link', { name: /github\.com\/mnikhilsrikar2512\/voiceym/ })).toBeVisible();
  fireEvent.click(screen.getByRole('link', { name: /contact\.security_cta/ }));
  expect(mock.open).toHaveBeenCalledOnce();
  expect(mock.open).toHaveBeenCalledWith(
    'https://github.com/mnikhilsrikar2512/voiceym/security/advisories/new',
  );
});
