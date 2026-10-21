import assert from 'node:assert/strict';
import { existsSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { _electron as electron } from 'playwright';

const agent = process.argv[2] || 'codex';
assert.ok(['codex', 'claude', 'opencode', 'pi'].includes(agent));
const executablePath = process.env.VOICEYM_PACKAGED_EXE
  ? resolve(process.env.VOICEYM_PACKAGED_EXE)
  : resolve(
      process.platform === 'win32'
        ? 'electron/release/win-unpacked/Voiceym.exe'
        : process.platform === 'darwin'
          ? 'electron/release/mac/Voiceym.app/Contents/MacOS/Voiceym'
          : 'electron/release/linux-unpacked/voiceym-electron',
    );
assert.ok(existsSync(executablePath), `Packaged executable does not exist: ${executablePath}`);
const profile = mkdtempSync(join(tmpdir(), 'voiceym-translation-agent-'));
const app = await electron.launch({
  executablePath,
  args: [`--user-data-dir=${profile}`],
  env: {
    ...process.env,
    VOICEYM_ALLOW_MULTIPLE_INSTANCES: '1',
    VOICEYM_SKIP_BACKEND: '1',
  },
});

try {
  const page = await app.firstWindow();
  await page.waitForFunction(() => Boolean(window.voiceym?.repair));
  const agents = await page.evaluate(() => window.voiceym.repair.list());
  const selected = agents.find((candidate) => candidate.id === agent);
  assert.equal(selected?.available, true, `${agent} CLI must be detected`);

  const result = await page.evaluate(
    ({ agent }) =>
      window.voiceym.repair.translate({
        agent,
        purpose: 'translate',
        sourceLanguage: 'English',
        targetLanguage: 'Spanish',
        dialect: 'es-MX',
        glossary: [{ source: 'Voiceym', target: 'Voiceym' }],
        segments: [
          { id: 'intro', sourceText: 'Welcome to Voiceym.', start: 0, end: 1.5 },
          { id: 'next', sourceText: 'Your project is ready.', start: 1.5, end: 3.5 },
        ],
      }),
    { agent },
  );
  assert.equal(result.agent, agent);
  assert.deepEqual(
    result.translations.map((row) => row.id),
    ['intro', 'next'],
  );
  assert.ok(result.translations.every((row) => row.text.trim().length > 0));
  assert.notEqual(result.translations[0].text.toLowerCase(), 'welcome to voiceym.');
  console.log(`PASS: packaged ${agent} returned complete timing-budgeted Dubbing translations`);
} finally {
  await app.close();
  rmSync(profile, { recursive: true, force: true });
}
