#!/usr/bin/env node
// OPTIONAL Envato stock-video adapter. It drives YOUR OWN logged-in Chrome session (remote debugging / CDP,
// the editor's own Chrome Canary, BROWSER_CDP, default http://127.0.0.1:9333) and uses YOUR paid Envato subscription.
// Check Envato's licence terms for how downloaded items may be used (each download is licensed to a project).
// Needs: npm install playwright-core (in this tools/ folder) and the editor browser logged in once:
//   python -m reel_editor browser login envato
//   envato_video.mjs search "<terms>" [--orientation vertical|horizontal|any] [--limit 20] [--json]
//   envato_video.mjs download <id> --out <dir> [--res 1080|4k|best]    (default 1080p, falls back to best available)
//   envato_video.mjs thumb <id> --out <file.jpg>
// Opens its own tab and closes it. Never touches Generate / AI buttons. Never prints cookies/tokens.
import { chromium } from 'playwright-core';
import { mkdirSync, existsSync, readdirSync, statSync, renameSync, writeFileSync, rmSync, openSync, readSync, closeSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { join, extname, basename, resolve } from 'node:path';

const CDP = process.env.BROWSER_CDP || process.env.ENVATO_CDP || 'http://127.0.0.1:9333';
const [cmd, ...rest] = process.argv.slice(2);
const flags = {}; const pos = [];
for (let i = 0; i < rest.length; i++) {
  if (rest[i].startsWith('--')) { const k = rest[i].slice(2); const nxt = rest[i + 1]; if (nxt === undefined || nxt.startsWith('--')) flags[k] = true; else { flags[k] = nxt; i++; } }
  else pos.push(rest[i]);
}
const die = (m) => { console.error(m); process.exit(1); };
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

async function withPage(fn) {
  let browser;
  try { browser = await chromium.connectOverCDP(CDP); } catch { die('BROWSER_DOWN: cannot reach the editor browser at ' + CDP + ' (run: python -m reel_editor browser start)'); }
  const ctx = browser.contexts()[0] || await browser.newContext();
  const page = await ctx.newPage();
  try { return await fn(page, ctx, browser); }
  finally { await page.close().catch(() => {}); await browser.close().catch(() => {}); }
}

function parseDur(s) { const m = /(\d+):(\d+)(?::(\d+))?/.exec(s || ''); if (!m) return null; return m[3] ? (+m[1]) * 3600 + (+m[2]) * 60 + (+m[3]) : (+m[1]) * 60 + (+m[2]); }
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

async function checkLogin(page) {
  const u = page.url();
  if (/login|signin|account\.envato/i.test(u)) die('NOT_LOGGED_IN: Envato is logged out in the editor browser (run: python -m reel_editor browser login envato)');
}

async function search(terms, orientation, limit) {
  const q = new URLSearchParams({ itemType: 'stock-video', term: terms });
  let url = 'https://app.envato.com/search?' + q.toString().replace(/%20/g, '+');
  const o = (orientation || 'any').toLowerCase();
  if (o === 'vertical') url += '&filter.orientation=Vertical';
  else if (o === 'horizontal') url += '&filter.orientation=Horizontal';
  return withPage(async (page) => {
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 45000 });
    await checkLogin(page);
    await page.waitForSelector('[data-cy=item-card]', { timeout: 30000 }).catch(() => {});
    const grab = () => page.evaluate(() => [...document.querySelectorAll('[data-cy=item-card]')].map(c => {
      const a = c.querySelector('a[data-analytics-item_id]'); if (!a) return null;
      const img = c.querySelector('img'); const vid = c.querySelector('video');
      let thumb = null;
      if (img) { const ss = (img.getAttribute('srcset') || '').split(',').map(s => s.trim().split(' ')[0]).filter(Boolean); thumb = ss[ss.length - 1] || img.src; }
      const first = (c.innerText || '').split('\n')[0];
      return { id: a.getAttribute('data-analytics-item_id'), title: a.getAttribute('data-analytics-item_title'), author: a.getAttribute('data-analytics-item_author'), dur: first, thumbUrl: thumb, previewUrl: vid ? (vid.getAttribute('src') || vid.querySelector('source')?.src || null) : null };
    }).filter(Boolean));
    let items = await grab();
    for (let i = 0; i < 8 && items.length < limit; i++) {
      await page.mouse.wheel(0, 4000); await sleep(1200);
      const n = await grab(); if (n.length <= items.length) break; items = n;
    }
    const seen = new Set();
    return items.filter(x => !seen.has(x.id) && seen.add(x.id)).slice(0, limit).map(x => ({
      id: x.id, title: x.title, duration_sec: parseDur(x.dur), author: x.author, thumbUrl: x.thumbUrl, previewUrl: x.previewUrl,
      orientation: o === 'any' ? null : o,
    }));
  });
}

async function openItem(page, id) {
  await page.goto('https://app.envato.com/search/stock-video/' + id, { waitUntil: 'domcontentloaded', timeout: 45000 });
  await checkLogin(page);
  await page.waitForSelector('[data-cy=idp-download-button]', { timeout: 30000 }).catch(() => die('Item page has no download button (bad id or not a stock-video?)'));
}

async function thumb(id, out) {
  return withPage(async (page, ctx) => {
    await openItem(page, id);
    const url = await page.evaluate(() => document.querySelector('meta[property="og:image"]')?.content || document.querySelector('video')?.poster || null);
    let src = url;
    if (!src) { // fall back to the search card image
      src = await page.evaluate(() => [...document.querySelectorAll('img')].map(i => i.src).find(s => /envatousercontent/.test(s)) || null);
    }
    if (!src) die('No poster image found');
    const r = await ctx.request.get(src);
    if (!r.ok()) die('Poster fetch failed: ' + r.status());
    mkdirSync(resolve(out, '..'), { recursive: true });
    writeFileSync(out, await r.body());
    return out;
  });
}

async function download(id, outDir, res) {
  outDir = resolve(outDir); mkdirSync(outDir, { recursive: true });
  const stage = join(outDir, '.envato-' + id.slice(0, 8) + '-' + Date.now());
  mkdirSync(stage);
  try {
    await withPage(async (page, ctx, browser) => {
      await openItem(page, id);
      // choose resolution
      const want = (res || '1080').toString().toLowerCase();
      await page.waitForSelector('[data-cy=idp-download-button]', { timeout: 30000 }).catch(() => {});
      const hasFormats = await page.waitForSelector('[data-cy=idp-format-dropdown-trigger]', { timeout: 4000 }).then(() => true).catch(() => false);
      if (want !== 'best' && !hasFormats) console.error('single format item');
      if (want !== 'best' && hasFormats) {
        await page.click('[data-cy=idp-format-dropdown-trigger]');
        await page.waitForSelector('[data-cy=idp-format-option]', { timeout: 8000 });
        const labels = await page.$$eval('[data-cy=idp-format-option]', els => els.map(e => e.innerText.trim()));
        const pick = (re) => labels.findIndex(l => re.test(l));
        let idx = want === '4k' ? pick(/4K/i) : pick(/1080/);
        if (idx < 0) idx = pick(/4K|2160/i) >= 0 ? pick(/4K|2160/i) : 0;
        console.error('formats: ' + labels.join(' | ') + ' -> chose: ' + labels[idx]);
        await page.locator('[data-cy=idp-format-option]').nth(idx).click();
        await sleep(500);
      }
      const cdp = await ctx.newCDPSession(page);
      await cdp.send('Browser.setDownloadBehavior', { behavior: 'allowAndName', downloadPath: stage, eventsEnabled: true }).catch(() => {});
      const dlP = page.waitForEvent('download', { timeout: 120000 }).catch(() => null);
      // CDP-level completion tracker (works even if Playwright misses the event)
      let guid = null, done = false, fname = null;
      cdp.on('Browser.downloadWillBegin', e => { guid = e.guid; fname = e.suggestedFilename; });
      cdp.on('Browser.downloadProgress', e => { if (e.state === 'completed') done = true; if (e.state === 'canceled') done = 'canceled'; });
      await page.locator('[data-cy=idp-download-button]').first().scrollIntoViewIfNeeded().catch(() => {});
      await page.locator('[data-cy=idp-download-button]').first().click({ timeout: 45000 });
      // license modal handling: appears as role=dialog with a download/confirm button
      const t0 = Date.now(); let modalHandled = false;
      while (Date.now() - t0 < 120000 && !done) {
        await sleep(800);
        if (!modalHandled) {
          const dlg = page.locator('[role=dialog]:has-text("licen"), [role=dialog]:has-text("project"), [role=dialog]:has-text("Project")').first();
          if (await dlg.count() && await dlg.isVisible().catch(() => false)) {
            modalHandled = true;
            await page.screenshot({ path: join(stage, 'modal.png') }).catch(() => {});
            const text = await dlg.innerText();
            console.error('license dialog: ' + text.replace(/\s+/g, ' ').slice(0, 300));
            // never touch AI/generate controls; only a "create project" / license-confirm path
            const nameInput = dlg.locator('input[type=text], input:not([type])').first();
            if (await nameInput.count()) { const v = await nameInput.inputValue(); if (!v) await nameInput.fill('Reel Editor'); }
            const btn = dlg.locator('button').filter({ hasText: /^(Download|Create project|Create|License|Confirm|Continue|Download and license)/i }).filter({ hasNotText: /generate|AI/i }).last();
            if (await btn.count()) await btn.click(); else console.error('no confirm button found in dialog');
          }
        }
        if (fname && !guid) break;
      }
      const dl = await Promise.race([dlP, sleep(5000).then(() => null)]);
      if (dl) { await dl.saveAs(join(stage, dl.suggestedFilename())).catch(() => {}); }
      // wait for completion
      const t1 = Date.now();
      while (Date.now() - t1 < 180000) {
        const files = readdirSync(stage).filter(f => !f.endsWith('.png') && !f.endsWith('.crdownload'));
        if (files.length && (done === true || dl)) break;
        if (done === 'canceled') die('Download canceled');
        await sleep(1000);
      }
    });
    // collect result
    let files = readdirSync(stage).filter(f => f !== 'modal.png' && !f.endsWith('.crdownload'));
    if (!files.length) die('No file downloaded (see ' + stage + ')');
    let f = files[0]; let p = join(stage, f);
    // allowAndName gives a guid name; detect zip by magic
    const head = Buffer.alloc(12); { const fd = openSync(p, 'r'); readSync(fd, head, 0, 12, 0); closeSync(fd); }
    const isZip = head[0] === 0x50 && head[1] === 0x4b;
    const magic = head.subarray(4, 8).toString('latin1') === 'ftyp' ? (head.subarray(8, 12).toString('latin1') === 'qt  ' ? 'QuickTime' : 'MP4') : '';
    if (isZip) {
      const ex = join(stage, 'x'); mkdirSync(ex);
      try { execFileSync('unzip', ['-o', '-q', p, '-d', ex]); }
      catch { execFileSync('tar', ['-xf', p, '-C', ex]); }   // Windows 10+ tar (bsdtar) reads zip files
      const walk = (d) => readdirSync(d, { withFileTypes: true }).flatMap(e => e.isDirectory() ? walk(join(d, e.name)) : [join(d, e.name)]);
      const vids = walk(ex).filter(x => /\.(mp4|mov|m4v|webm)$/i.test(x) && !/__MACOSX/.test(x)).sort((a, b) => statSync(b).size - statSync(a).size);
      if (!vids.length) die('Zip contained no video');
      p = vids[0];
    }
    const ext = extname(p) || (/QuickTime/i.test(magic) ? '.mov' : '.mp4');
    const final = join(outDir, 'envato-' + id + ext);
    renameSync(p, final);
    console.log(final);
  } finally {
    rmSync(stage, { recursive: true, force: true });
  }
}

try {
  if (cmd === 'search') {
    if (!pos[0]) die('usage: search "<terms>" [--orientation vertical|horizontal|any] [--limit 20] --json');
    const res = await search(pos.join(' '), flags.orientation, parseInt(flags.limit || '20', 10));
    if (flags.json) console.log(JSON.stringify(res, null, 1));
    else res.forEach(r => console.log(`${r.id}  ${r.duration_sec}s  ${r.title}  (${r.author})`));
  } else if (cmd === 'download') {
    if (!UUID.test(pos[0] || '') || !flags.out) die('usage: download <id> --out <dir> [--res 1080|4k|best]');
    await download(pos[0], flags.out, flags.res);
  } else if (cmd === 'thumb') {
    if (!UUID.test(pos[0] || '') || !flags.out) die('usage: thumb <id> --out <file.jpg>');
    console.log(await thumb(pos[0], resolve(flags.out)));
  } else die('commands: search | download | thumb');
} catch (e) { die('error: ' + (e.message || e).toString().split('\n')[0]); }
