import { cleanup, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createInstance } from 'i18next';
import { I18nextProvider } from 'react-i18next';
import { afterEach, expect, it, vi } from 'vitest';
import en from '@/i18n/locales/en.json';
import ja from '@/i18n/locales/ja.json';
import { DiagnosticsSettings } from './diagnostics-settings';

vi.mock('@/components/bridge', () => ({ getBridge: () => undefined }));
vi.mock('@/lib/api/client', () => ({
  apiJson: () => new Promise(() => {}),
  describeError: String,
}));
afterEach(cleanup);

it.each(['en', 'ja'])(
  'shows required credits in About before models or diagnostics load (%s)',
  async (language) => {
    const i18n = createInstance();
    await i18n.init({
      lng: language,
      resources: { en: { translation: en }, ja: { translation: ja } },
      interpolation: { escapeValue: false },
    });
    render(
      <I18nextProvider i18n={i18n}>
        <QueryClientProvider client={new QueryClient()}>
          <DiagnosticsSettings />
        </QueryClientProvider>
      </I18nextProvider>,
    );
    expect(screen.getByRole('heading', { name: i18n.t('about.model_credits') })).toBeVisible();
    const notice = screen.getByText(
      'Built with Higgs Materials licensed from Boson AI USA, Inc., Copyright Boson AI USA, Inc., All Rights Reserved and Meta Llama 3 licensed under the Meta Llama 3 Community License, Copyright Meta Platforms, Inc., All Right Reserved',
    );
    expect(notice).toBeVisible();
    expect(notice).toHaveAttribute('lang', 'en');
    expect(
      screen.getByRole('link', { name: 'Boson Higgs Audio 2 Community License' }),
    ).toHaveAttribute(
      'href',
      'https://huggingface.co/k2-fsa/OmniVoice/blob/c5fdb5ccb189668d56333f77ba2629f4cd7535f4/audio_tokenizer/LICENSE',
    );
    expect(screen.getByRole('link', { name: 'Meta Llama 3 Community License' })).toHaveAttribute(
      'href',
      'https://github.com/meta-llama/llama3/blob/main/LICENSE',
    );
    for (const model of [
      'Parakeet TDT 0.6B v2',
      'Parakeet TDT 0.6B v3',
      'WeSpeaker VoxCeleb ResNet34-LM',
      'OmniVoice',
      'NLLB-200 distilled 600M',
    ]) {
      expect(screen.getByRole('link', { name: model })).toBeVisible();
    }
    expect(screen.getByText(i18n.t('about.model_credits_hint'))).toBeVisible();
  },
);
