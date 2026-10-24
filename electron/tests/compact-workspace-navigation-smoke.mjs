import { chromium } from 'playwright';
import assert from 'node:assert/strict';
const browser = await chromium.launch({
  ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}),
  headless: true,
});
try {
  const page = await browser.newPage({ viewport: { width: 1200, height: 800 } });
  await page.addInitScript(() => localStorage.setItem('voiceym.setup.complete.v1', '1'));
  await page.route(
    (url) => url.pathname.startsWith('/api/'),
    (route) => {
      const path = new URL(route.request().url()).pathname;
      const fixtures = {
        '/api/health': { status: 'ok' },
        '/api/setup/status': { ready: true },
        '/api/models/install/status': { jobs: [] },
        '/api/profiles': [],
        '/api/history': [],
        '/api/projects': [],
        '/api/export/history': [],
        '/api/longform/jobs': { jobs: [] },
      };
      return path in fixtures
        ? route.fulfill({ json: fixtures[path] })
        : route.fulfill({ status: 404, json: { detail: 'Not mocked' } });
    },
  );
  const routes = [
    ['Text to speech', '/clone'],
    ['Design a voice', '/design'],
    ['My voices', '/personas'],
    ['Voice gallery', '/gallery'],
    ['Stories', '/stories'],
    ['Audiobook', '/audiobook'],
    ['Dub a video', '/dub'],
    ['Batch Dubbing', '/batch'],
  ];
  // Every rail icon goes straight to its page: no groups to open first.
  for (const [item, path] of routes) {
    await page.goto((process.env.VOICEYM_UI_URL || 'http://localhost:3912') + '/#/design');
    const rail = page.locator('[data-slot=compact-main-sidebar]');
    await rail.waitFor();
    const link = rail.getByRole('link', { name: item, exact: true });
    await link.waitFor();
    if (process.env.VOICEYM_SCREENSHOT && item === 'Text to speech')
      await page.screenshot({ path: process.env.VOICEYM_SCREENSHOT, animations: 'disabled' });
    await link.click();
    await page.waitForURL('**/#' + path, { timeout: 3000 });
  }
  await page.goto((process.env.VOICEYM_UI_URL || 'http://localhost:3912') + '/#/design');
  const rail = page.locator('[data-slot=compact-main-sidebar]');
  await rail.waitFor();
  const overlay = page.locator('[data-slot=expanded-main-sidebar]');
  // Resting on the rail opens the sidebar over the page; leaving closes it.
  await page.mouse.move(1100, 400);
  await rail.getByRole('link', { name: 'Talk', exact: true }).hover();
  await overlay.waitFor();
  const named = overlay.getByRole('link', { name: 'Text to speech', exact: true });
  await named.hover();
  await page.waitForTimeout(300);
  assert.equal(await named.isVisible(), true, 'sidebar should stay open while moving into it');
  await page.mouse.move(1100, 400);
  await overlay.waitFor({ state: 'detached' });
  // Keyboard and touch: the rail's top button opens it and focuses the first page.
  const toggle = rail.getByRole('button', { name: 'Toggle Sidebar', exact: true });
  await toggle.focus();
  await page.keyboard.press('Enter');
  await overlay.waitFor();
  await page.waitForFunction(() =>
    document.querySelector('[data-slot=expanded-main-sidebar] nav')?.contains(document.activeElement),
  );
  await page.keyboard.press('Escape');
  await overlay.waitFor({ state: 'detached' });
  // Choosing a page closes it.
  await toggle.click();
  await overlay.waitFor();
  await overlay.getByRole('link', { name: 'Stories', exact: true }).click();
  await page.waitForURL('**/#/stories', { timeout: 3000 });
  await page.mouse.move(1100, 400);
  await overlay.waitFor({ state: 'detached' });
  console.log('PASS: rail links, hover sidebar, and keyboard access');
  console.log(`PASS: ${routes.length} compact navigation destinations`);
} finally {
  await browser.close();
}
