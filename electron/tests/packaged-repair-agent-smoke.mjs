import { _electron as electron } from 'playwright';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import assert from 'node:assert/strict';

const executablePath =
  process.env.VOICEYM_PACKAGED_EXE ||
  resolve(
    process.platform === 'win32'
      ? 'electron/release/win-unpacked/Voiceym.exe'
      : process.platform === 'darwin'
        ? 'electron/release/mac/Voiceym.app/Contents/MacOS/Voiceym'
        : 'electron/release/linux-unpacked/voiceym-electron',
  );
const profile = mkdtempSync(join(tmpdir(), 'voiceym-packaged-repair-'));
const app = await electron.launch({
  executablePath,
  args: ['--user-data-dir=' + profile],
  env: {
    ...process.env,
    VOICEYM_ALLOW_MULTIPLE_INSTANCES: '1',
    VOICEYM_SKIP_BACKEND: '1',
  },
});

try {
  const page = await app.firstWindow();
  await page.waitForFunction(() => Boolean(window.voiceym?.repair));
  const state = await page.evaluate(() => window.voiceym.repair.getState());
  assert.equal(state.workspaceAvailable, false);
  assert.equal(state.workspacePath, undefined);

  const agents = await page.evaluate(() => window.voiceym.repair.list());
  assert.ok(
    agents.some((agent) => agent.available),
    'At least one local agent is required',
  );
  await page.evaluate(() => {
    window.location.hash = '/settings/updates';
  });
  const launcher = page.getByRole('button', { name: 'Ask Voiceym Agent' });
  await launcher.waitFor();
  await page.evaluate(() => {
    window.dispatchEvent(
      new CustomEvent('voiceym:repair-agent-open', {
        detail: { report: 'ACTION_REQUEST: inspect the current app setup', autoFix: false },
      }),
    );
  });
  const dock = page.getByRole('region', { name: 'Ask Voiceym Agent' });
  await dock.waitFor();
  assert.equal(await dock.getByRole('button', { name: 'Send', exact: true }).isEnabled(), true);
  assert.equal(await dock.getByRole('button', { name: 'Plan', exact: true }).isEnabled(), true);
  assert.equal(await dock.getByRole('button', { name: 'Choose folder…' }).count(), 0);

  const ordinaryError = await page.evaluate(async () => {
    const available = (await window.voiceym.repair.list()).find((agent) => agent.available);
    try {
      await window.voiceym.repair.start({
        agent: available.id,
        mode: 'diagnose',
        report: 'Diagnose this renderer failure',
        context: '{}',
      });
      return '';
    } catch (error) {
      return String(error);
    }
  });
  assert.match(ordinaryError, /source checkout is required/i);

  const unavailable = agents.find((agent) => !agent.available);
  if (unavailable) {
    const actionError = await page.evaluate(async (agent) => {
      try {
        await window.voiceym.repair.start({
          agent,
          mode: 'fix',
          report: 'ACTION_REQUEST: inspect the current app setup',
          context: '{}',
        });
        return '';
      } catch (error) {
        return String(error);
      }
    }, unavailable.id);
    assert.match(actionError, /agent is not installed/i);
    assert.doesNotMatch(actionError, /source checkout is required/i);
  }
  console.log(
    'PASS: packaged app actions run without source while code repair remains source-gated',
  );
} finally {
  await app.close();
  rmSync(profile, { recursive: true, force: true });
}
