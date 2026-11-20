#!/usr/bin/env node
// Session helper for the editor's own browser (Chrome Canary, permanent profile, remote debugging on BROWSER_CDP).
//   browser_session.mjs check envato    -> {"logged_in": true|false}
//   browser_session.mjs login envato    -> signs in with {"email","password"} read from STDIN (the Python side
//                                          decrypts them from the vault and pipes them in). Never printed or logged.
//   browser_session.mjs close           -> closes the browser cleanly
// A 2-step code (email/app) cannot be automated: then it reports {"logged_in": false, "reason": "2fa"} and the
// person runs `python -m reel_editor browser login envato` once.
import { chromium } from 'playwright-core';

const CDP = process.env.BROWSER_CDP || process.env.ENVATO_CDP || 'http://127.0.0.1:9222';
const [cmd, site = 'envato'] = process.argv.slice(2);
const out = (o) => { console.log(JSON.stringify(o)); };
const die = (m) => { console.error(m); process.exit(1); };
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

const SITES = {
  envato: {
    home: 'https://app.envato.com/',
    login: 'https://account.envato.com/sign_in?to=elements',
    isLoginUrl: (u) => /account\.envato\.com\/(sign_in|login)|\/sign_in|\/login/i.test(u),
  },
};

async function connect() {
  try { return await chromium.connectOverCDP(CDP, { timeout: 20000 }); }
  catch { die('Cannot reach the editor browser at ' + CDP + ' (run: python -m reel_editor browser start)'); }
}

async function isLoggedIn(page, s) {
  await page.goto(s.home, { waitUntil: 'domcontentloaded', timeout: 45000 });
  await sleep(3000);
  if (s.isLoginUrl(page.url())) return false;
  // logged-out home pages show a "Sign in" link; logged-in ones show the account menu
  const signIn = await page.locator('a:has-text("Sign in"), button:has-text("Sign in"), a:has-text("Log in")').count();
  return signIn === 0;
}

async function readStdin() {
  if (process.stdin.isTTY) return {};
  const chunks = []; for await (const c of process.stdin) chunks.push(c);
  try { return JSON.parse(Buffer.concat(chunks).toString() || '{}'); } catch { return {}; }
}

async function login(page, s) {
  const { email, password: pass } = await readStdin();
  if (!email || !pass) return { logged_in: false, reason: 'no saved login' };
  await page.goto(s.login, { waitUntil: 'domcontentloaded', timeout: 45000 });
  await sleep(2500);
  const user = page.locator('input[type=email], input[name=username], input[name=email], input[autocomplete=username]').first();
  if (!(await user.count())) return { logged_in: false, reason: 'sign-in form not found' };
  await user.fill(email);
  let pw = page.locator('input[type=password]').first();
  if (!(await pw.isVisible().catch(() => false))) {          // two-step form: email first, then password
    await page.locator('button[type=submit], input[type=submit]').first().click().catch(() => {});
    await sleep(2500);
    pw = page.locator('input[type=password]').first();
  }
  if (!(await pw.count())) return { logged_in: false, reason: 'password field not found' };
  await pw.fill(pass);
  const remember = page.locator('input[type=checkbox][name*=remember i]').first();
  if (await remember.count()) await remember.check().catch(() => {});
  await page.locator('button[type=submit], input[type=submit]').first().click();
  await sleep(6000);
  const body = (await page.locator('body').innerText().catch(() => '')).toLowerCase();
  if (/verification code|two-step|2-step|enter the code|authenticat/.test(body)) return { logged_in: false, reason: '2fa' };
  if (/captcha|verify you are human/.test(body)) return { logged_in: false, reason: 'captcha' };
  return { logged_in: await isLoggedIn(page, s) };
}

const s = SITES[site];
if (cmd !== 'close' && !s) die('unknown site ' + site);
const browser = await connect();
try {
  if (cmd === 'close') {
    const cdp = await browser.newBrowserCDPSession();
    await cdp.send('Browser.close').catch(() => {});
    out({ closed: true });
  } else {
    const ctx = browser.contexts()[0] || await browser.newContext();
    const page = await ctx.newPage();
    try {
      if (cmd === 'check') out({ logged_in: await isLoggedIn(page, s) });
      else if (cmd === 'login') out(await login(page, s));
      else die('commands: check | login | close');
    } finally { await page.close().catch(() => {}); }
  }
} catch (e) { die('error: ' + (e.message || e).toString().split('\n')[0]); }
finally { await browser.close().catch(() => {}); }
