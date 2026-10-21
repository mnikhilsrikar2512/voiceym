import { afterEach, expect, it, vi } from 'vitest';

const seen = vi.hoisted(() => ({ env: undefined as NodeJS.ProcessEnv | undefined }));
vi.mock('node:child_process', async (load) => ({
  ...(await load<typeof import('node:child_process')>()),
  execFile: vi.fn(
    (
      _file: string,
      _args: string[],
      options: { env: NodeJS.ProcessEnv },
      cb: (e: null, out: string, err: string) => void,
    ) => {
      seen.env = options.env;
      cb(null, '', '');
      return { stdin: { end() {} } };
    },
  ),
}));

import { probe } from './repair-agents';

afterEach(() => vi.unstubAllEnvs());

it('CLI detection probes never inherit backend-only capabilities', async () => {
  vi.stubEnv('VOICEYM_LLM_AGENT_TOKEN', 'capability');
  vi.stubEnv('VOICEYM_ACCOUNT_BRIDGE_TOKEN', 'capability');
  await probe('codex', ['--version']);
  expect(seen.env?.VOICEYM_LLM_AGENT_TOKEN).toBeUndefined();
  expect(seen.env?.VOICEYM_ACCOUNT_BRIDGE_TOKEN).toBeUndefined();
});
