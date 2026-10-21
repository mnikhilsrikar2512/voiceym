import { afterEach, expect, it, vi } from 'vitest';
import { onStorageKey } from '@/lib/storage-events';
import { installSyncedStorage } from './synced-storage';

const KEYS = ['voiceym.theme.v2', 'voiceym.clone.settings.v1', 'voiceym.locale'];
const RAW = ['voiceym.locale'];
let uninstall: (() => void) | undefined;
afterEach(() => {
  uninstall?.();
  uninstall = undefined;
  localStorage.clear();
});

function install() {
  const post = vi.fn(async (_key: string, _value: unknown) => {});
  const synced = installSyncedStorage({ storage: localStorage, keys: KEYS, rawKeys: RAW, post });
  uninstall = synced.uninstall;
  return { post, synced };
}

it('posts each change to a synced key once, in the format the allowlist declares', () => {
  const { post } = install();
  localStorage.setItem('voiceym.clone.settings.v1', JSON.stringify({ steps: 16 }));
  localStorage.setItem('voiceym.locale', 'true'); // a raw key stays a string even if it parses
  localStorage.removeItem('voiceym.theme.v2');
  expect(post.mock.calls).toEqual([
    ['voiceym.clone.settings.v1', { steps: 16 }],
    ['voiceym.locale', 'true'],
    ['voiceym.theme.v2', null],
  ]);
});

it('never posts a JSON key whose stored text cannot be parsed', () => {
  const { post } = install();
  localStorage.setItem('voiceym.theme.v2', 'not json');
  expect(post).not.toHaveBeenCalled();
});

it('ignores keys that never sync and still stores them', () => {
  const { post } = install();
  localStorage.setItem('voiceym.workspace-layout', 'x');
  expect(post).not.toHaveBeenCalled();
  expect(localStorage.getItem('voiceym.workspace-layout')).toBe('x');
});

it('applies a pulled value without echoing it and tells the app it changed', () => {
  const { post, synced } = install();
  const seen: (string | null)[] = [];
  const stops = KEYS.map((key) => onStorageKey(key, () => seen.push(key)));
  synced.apply('voiceym.locale', 'de');
  synced.apply('voiceym.clone.settings.v1', { steps: 24 });
  synced.apply('voiceym.theme.v2', null);
  stops.forEach((stop) => stop());
  expect(post).not.toHaveBeenCalled();
  expect(localStorage.getItem('voiceym.locale')).toBe('de');
  expect(localStorage.getItem('voiceym.clone.settings.v1')).toBe('{"steps":24}');
  expect(localStorage.getItem('voiceym.theme.v2')).toBeNull();
  expect(seen).toEqual([
    'voiceym.locale',
    'voiceym.clone.settings.v1',
    'voiceym.theme.v2',
  ]);
});

it('reads the current values of every synced key for the first mirror', () => {
  localStorage.setItem('voiceym.locale', 'de');
  localStorage.setItem('voiceym.theme.v2', '{"mode":"dark"}');
  const { synced } = install();
  expect(synced.snapshot()).toEqual([
    ['voiceym.theme.v2', { mode: 'dark' }],
    ['voiceym.locale', 'de'],
  ]);
});

it('uninstall restores the original storage methods', () => {
  const original = Storage.prototype.setItem;
  const { post } = install();
  uninstall!();
  uninstall = undefined;
  expect(Storage.prototype.setItem).toBe(original);
  localStorage.setItem('voiceym.locale', 'de');
  expect(post).not.toHaveBeenCalled();
});

it('a failing post never breaks the write', () => {
  const post = vi.fn(async () => {
    throw new Error('backend down');
  });
  const synced = installSyncedStorage({ storage: localStorage, keys: KEYS, rawKeys: RAW, post });
  uninstall = synced.uninstall;
  expect(() => localStorage.setItem('voiceym.locale', 'de')).not.toThrow();
  expect(localStorage.getItem('voiceym.locale')).toBe('de');
});

it('a pulled value for a partly-synced key merges in and keeps device-local fields (the draft)', () => {
  const post = vi.fn(async () => {});
  const synced = installSyncedStorage({
    storage: localStorage,
    keys: KEYS,
    rawKeys: RAW,
    partialKeys: ['voiceym.clone.settings.v1'],
    post,
  });
  uninstall = synced.uninstall;
  localStorage.setItem(
    'voiceym.clone.settings.v1',
    JSON.stringify({ steps: 16, text: 'my draft', refText: 'ref', selectedProfileId: 'ab12cd34' }),
  );
  synced.apply('voiceym.clone.settings.v1', { steps: 24, speed: 1.1 });
  expect(JSON.parse(localStorage.getItem('voiceym.clone.settings.v1')!)).toEqual({
    steps: 24,
    speed: 1.1,
    text: 'my draft',
    refText: 'ref',
    selectedProfileId: 'ab12cd34',
  });
});
