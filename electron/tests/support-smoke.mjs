import { chromium } from 'playwright';
import assert from 'node:assert/strict';
const browser = await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge', headless: true });
const page = await browser.newPage({ viewport: { width: 1100, height: 760 } });
try {
  await page.goto((process.env.VOICEYM_UI_URL || 'http://localhost:3912') + '/#/settings/appearance');
  await page.getByRole('link', { name: 'Support', exact: true }).click();
  await page.getByRole('heading', { name: 'Support', exact: true }).first().waitFor();
  const repo = 'https://github.com/mnikhilsrikar2512/voiceym';
  assert.equal(
    await page.getByRole('link', { name: 'Open GitHub Issues', exact: true }).getAttribute('href'),
    repo + '/issues',
  );
  assert.equal(
    await page.getByRole('link', { name: 'Report privately', exact: true }).getAttribute('href'),
    repo + '/security/advisories/new',
  );
  assert.equal(
    await page.getByRole('link', { name: 'github.com/mnikhilsrikar2512/voiceym', exact: true }).getAttribute('href'),
    repo,
  );
  // The donation page is gone: no amounts, goal bar or payment links remain.
  assert.equal(await page.getByRole('progressbar').count(), 0);
  assert.equal(await page.getByRole('link', { name: 'PayPal', exact: true }).count(), 0);
  console.log('Support navigation, issue, private security and source destinations passed; no external page opened.');
} finally {
  await browser.close();
}
