import { chromium } from 'playwright';
import assert from 'node:assert/strict';
const browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge', headless: true });
const page = await browser.newPage({ viewport: { width: 1100, height: 760 } });
try {
  await page.goto((process.env.VOICEYM_UI_URL || 'http://localhost:3912') + '/#/settings/appearance');
  await page.getByRole('link', { name: 'Support', exact: true }).click();
  await page.getByRole('heading', { name: 'Support', exact: true }).first().waitFor();
  assert.equal(
    await page.getByRole('link', { name: 'Open GitHub Issues', exact: true }).getAttribute('href'),
    'https://github.com/mnikhilsrikar2512/voiceym/issues',
  );
  assert.equal(
    await page.getByRole('link', { name: 'Report privately', exact: true }).getAttribute('href'),
    'https://github.com/mnikhilsrikar2512/voiceym/security/advisories/new',
  );
  assert.equal(
    await page.getByRole('link', { name: 'github.com/mnikhilsrikar2512/voiceym', exact: true }).getAttribute('href'),
    'https://github.com/mnikhilsrikar2512/voiceym',
  );
  console.log('Help navigation, issue, private security and source destinations passed; no external page opened.');
} finally {
  await browser.close();
}
