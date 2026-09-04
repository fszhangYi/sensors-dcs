"""Embody-style /login HTML shell for sensors-dcs (vanilla, no React)."""

from __future__ import annotations

LOGIN_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>sensors-dcs · Login</title>
  <link rel="icon" href="/assets/favicon.svg" type="image/svg+xml" />
  <link rel="icon" href="/assets/favicon.ico" sizes="any" />
  <link rel="apple-touch-icon" href="/assets/favicon.png" />
  <link rel="stylesheet" href="/assets/fonts/ibm-plex-sans.css" />
  <style>
    :root {
      --bg: #0b1018;
      --panel: rgba(18, 26, 38, 0.9);
      --border: rgba(58, 77, 102, 0.75);
      --text: #e7ecf3;
      --muted: #8b9bb4;
      --accent: #3dd6c6;
      --spark: #f0b429;
      --input-bg: rgba(8, 12, 20, 0.85);
      --brand-title: linear-gradient(120deg, #e7ecf3 20%, #3dd6c6 55%, #f0b429 95%);
      --motion-ease: cubic-bezier(0.22, 1, 0.36, 1);
    }
    * { box-sizing: border-box; }
    body { margin: 0; background: var(--bg); color: var(--text);
      font-family: 'IBM Plex Sans', 'Segoe UI', 'PingFang SC', 'Noto Sans SC', sans-serif; }
    .auth-boot {
      min-height: 100vh; display: grid; place-content: center; gap: 14px;
      justify-items: center; color: var(--muted);
    }
    .auth-boot-mark {
      width: 42px; height: 42px; border-radius: 12px;
      border: 1px solid rgba(61, 214, 198, 0.45);
      background: radial-gradient(circle at 50% 70%, rgba(61, 214, 198, 0.35), transparent 55%), #121a26;
      box-shadow: 0 0 24px rgba(61, 214, 198, 0.2);
      animation: auth-pulse 1.4s ease-in-out infinite;
    }
    @keyframes auth-pulse {
      0%, 100% { transform: scale(1); opacity: 0.85; }
      50% { transform: scale(1.06); opacity: 1; }
    }
    .login-page {
      --login-card-w: 360px;
      position: relative; min-height: 100vh; display: flex; flex-direction: column;
      align-items: center; padding: 28px 16px 40px; overflow: hidden; background: var(--bg);
    }
    .login-bg { position: absolute; inset: 0; z-index: 0; pointer-events: none; overflow: hidden; }
    .login-grid {
      position: absolute; inset: -20%;
      background-image:
        linear-gradient(rgba(61, 214, 198, 0.05) 1px, transparent 1px),
        linear-gradient(90deg, rgba(61, 214, 198, 0.05) 1px, transparent 1px);
      background-size: 48px 48px;
      mask-image: radial-gradient(ellipse 70% 60% at 50% 40%, #000 20%, transparent 75%);
      animation: login-grid-drift 28s linear infinite; opacity: 0.7;
    }
    @keyframes login-grid-drift { to { transform: translate3d(48px, 48px, 0); } }
    .login-orb { position: absolute; border-radius: 50%; filter: blur(40px); opacity: 0.55; }
    .login-orb-a {
      width: 420px; height: 420px; left: -8%; top: -12%;
      background: radial-gradient(circle, rgba(61, 214, 198, 0.28), transparent 68%);
      animation: login-orb-float 12s ease-in-out infinite;
    }
    .login-orb-b {
      width: 380px; height: 380px; right: -6%; bottom: 8%;
      background: radial-gradient(circle, rgba(240, 180, 41, 0.16), transparent 70%);
      animation: login-orb-float 15s ease-in-out infinite reverse;
    }
    @keyframes login-orb-float {
      0%, 100% { transform: translate3d(0, 0, 0); }
      50% { transform: translate3d(18px, 22px, 0); }
    }
    .login-scan {
      position: absolute; left: 0; right: 0; height: 120px; top: -120px;
      background: linear-gradient(180deg, transparent, rgba(61, 214, 198, 0.06), transparent);
      animation: login-scan 7.5s ease-in-out infinite;
    }
    @keyframes login-scan {
      0% { top: -120px; opacity: 0; }
      12% { opacity: 1; }
      88% { opacity: 1; }
      100% { top: 110%; opacity: 0; }
    }
    .login-brand, .login-main, .login-footer { position: relative; z-index: 1; }
    .login-brand {
      display: flex; flex-direction: column; align-items: center; gap: 12px;
      margin-top: 28px; margin-bottom: 28px;
      animation: login-rise 520ms var(--motion-ease) both;
    }
    .login-logo {
      width: 48px; height: 48px; border-radius: 12px; display: block;
      box-shadow: 0 0 0 1px rgba(61, 214, 198, 0.25), 0 12px 32px rgba(0,0,0,0.45);
    }
    .login-brand-name {
      font-size: 1.35rem; font-weight: 650; letter-spacing: 0.02em;
      background: var(--brand-title);
      -webkit-background-clip: text; background-clip: text; color: transparent;
    }
    .login-lang {
      display: inline-flex; gap: 2px; padding: 3px; border-radius: 999px;
      border: 1px solid rgba(61, 214, 198, 0.28);
      background: rgba(12, 18, 28, 0.65); backdrop-filter: blur(8px);
    }
    .login-lang-btn {
      appearance: none; border: 0; cursor: pointer; padding: 5px 12px; border-radius: 999px;
      font: inherit; font-size: 0.72rem; font-weight: 550; color: var(--muted); background: transparent;
    }
    .login-lang-btn.active {
      color: var(--bg); background: linear-gradient(120deg, #3dd6c6, #7ae0d4);
    }
    .login-main {
      width: min(100%, var(--login-card-w)); display: flex; flex-direction: column; gap: 16px;
      animation: login-rise 640ms 60ms var(--motion-ease) both;
    }
    @keyframes login-rise {
      from { opacity: 0; transform: translateY(14px); }
      to { opacity: 1; transform: translateY(0); }
    }
    .login-card {
      position: relative; padding: 22px 22px 20px; border-radius: 12px;
      border: 1px solid rgba(58, 77, 102, 0.85); background: var(--panel);
      box-shadow: 0 18px 48px rgba(0,0,0,0.45), inset 0 1px 0 rgba(255,255,255,0.04);
      overflow: hidden; backdrop-filter: blur(12px);
    }
    .login-card-shine {
      position: absolute; inset: 0 auto auto 0; width: 140%; height: 2px;
      background: linear-gradient(90deg, transparent, rgba(61,214,198,0.55), rgba(240,180,41,0.45), transparent);
      animation: login-shine 3.8s ease-in-out infinite;
    }
    @keyframes login-shine {
      0%, 100% { transform: translateX(-30%); opacity: 0.45; }
      50% { transform: translateX(10%); opacity: 1; }
    }
    .login-card h1 { margin: 4px 0 6px; font-size: 1.05rem; font-weight: 600; text-align: center; }
    .login-sub { margin: 0 0 18px; text-align: center; font-size: 0.72rem; color: var(--muted); line-height: 1.45; }
    .login-form { display: flex; flex-direction: column; gap: 12px; }
    .login-field { display: flex; flex-direction: column; gap: 6px; font-size: 0.78rem; color: var(--muted); }
    .login-field input {
      appearance: none; border: 1px solid var(--border); background: var(--input-bg);
      color: var(--text); border-radius: 8px; padding: 0.55rem 0.7rem; font: inherit;
    }
    .login-field-row { display: flex; align-items: center; gap: 0.5rem; }
    .login-field-row input { flex: 1; }
    .login-ghost {
      appearance: none; border: 1px solid var(--border); background: transparent; color: var(--muted);
      border-radius: 999px; padding: 0.35rem 0.7rem; font: inherit; font-size: 0.72rem; cursor: pointer;
    }
    .login-error {
      margin: 0; min-height: 1.2em; color: #f87171; font-size: 0.78rem; text-align: center;
    }
    .login-submit {
      appearance: none; border: 0; cursor: pointer; margin-top: 4px; padding: 0.65rem 1rem;
      border-radius: 999px; font: inherit; font-weight: 600; color: #0b1018;
      background: linear-gradient(120deg, #3dd6c6, #7ae0d4 55%, #f0b429);
    }
    .login-submit:disabled { opacity: 0.55; cursor: not-allowed; }
    .login-aside {
      padding: 0 4px; color: var(--muted); font-size: 0.72rem; line-height: 1.5; text-align: center;
    }
    .login-aside-muted { opacity: 0.85; }
    .login-footer {
      margin-top: auto; padding-top: 28px; display: flex; align-items: center; gap: 8px;
      color: var(--muted); font-size: 0.7rem;
      animation: login-rise 700ms 120ms var(--motion-ease) both;
    }
    .login-footer-dot {
      width: 6px; height: 6px; border-radius: 50%;
      background: linear-gradient(120deg, #3dd6c6, #f0b429);
      box-shadow: 0 0 10px rgba(61, 214, 198, 0.45);
    }
    .hidden { display: none !important; }
    @media (prefers-reduced-motion: reduce) {
      .login-grid, .login-orb, .login-scan, .login-card-shine, .auth-boot-mark,
      .login-brand, .login-main, .login-footer { animation: none !important; }
    }
  </style>
</head>
<body>
  <div class="auth-boot" id="authBoot" role="status" aria-live="polite">
    <div class="auth-boot-mark" aria-hidden="true"></div>
    <p data-i18n="login.checkingSession">校验会话…</p>
  </div>

  <div class="login-page hidden" id="loginPage">
    <div class="login-bg" aria-hidden="true">
      <div class="login-grid"></div>
      <div class="login-orb login-orb-a"></div>
      <div class="login-orb login-orb-b"></div>
      <div class="login-scan"></div>
    </div>

    <header class="login-brand">
      <img class="login-logo" src="/assets/favicon.svg" alt="" width="48" height="48" />
      <span class="login-brand-name">sensors-dcs</span>
      <div class="login-lang" role="group" data-i18n-title="lang.title" title="界面语言 / Language">
        <button type="button" class="login-lang-btn active" data-locale="zh" data-i18n="lang.zh">中文</button>
        <button type="button" class="login-lang-btn" data-locale="en" data-i18n="lang.en">EN</button>
      </div>
    </header>

    <main class="login-main">
      <section class="login-card" aria-labelledby="login-title">
        <div class="login-card-shine" aria-hidden="true"></div>
        <h1 id="login-title" data-i18n="login.title">登录到 sensors-dcs</h1>
        <p class="login-sub" data-i18n="login.sub">使用本机账号继续采集</p>
        <form class="login-form" id="loginForm" autocomplete="on">
          <label class="login-field">
            <span data-i18n="login.username">用户名</span>
            <input id="username" name="username" autocomplete="username" value="sensors" required />
          </label>
          <label class="login-field">
            <span data-i18n="login.password">密码</span>
            <div class="login-field-row">
              <input id="password" name="password" type="password" autocomplete="current-password" required />
              <button type="button" class="login-ghost" id="btnTogglePw" data-i18n="login.show">显示</button>
            </div>
          </label>
          <p class="login-error" id="loginError" role="alert"></p>
          <button type="submit" class="login-submit" id="btnSubmit" data-i18n="login.submit">登录</button>
        </form>
      </section>
      <aside class="login-aside">
        <div data-i18n="login.aside">默认用户 sensors；首次启动会在终端打印随机密码。</div>
        <div class="login-aside-muted" data-i18n="login.asideMuted">会话 Cookie · HttpOnly · 同机有效</div>
      </aside>
    </main>

    <footer class="login-footer">
      <span class="login-footer-dot" aria-hidden="true"></span>
      <span data-i18n="login.footerTag">本地采集控制台</span>
    </footer>
  </div>

  <script>
    const DCS_I18N = __DCS_I18N_JSON__;
    const LS_LOCALE = 'sensors-dcs.locale';
    let currentLocale = 'zh';
    try {
      const stored = localStorage.getItem(LS_LOCALE);
      if (stored === 'zh' || stored === 'en') currentLocale = stored;
    } catch (e) {}
    function formatMessage(raw, vars) {
      if (!vars) return raw;
      return String(raw).replace(/[{](\w+)[}]/g, (_, k) =>
        (vars[k] == null ? '{' + k + '}' : String(vars[k])));
    }
    function t(path, vars) {
      const table = DCS_I18N[currentLocale] || DCS_I18N.zh || {};
      const fallback = DCS_I18N.zh || {};
      const raw = (table[path] != null ? table[path] : fallback[path]);
      return formatMessage(raw != null ? raw : path, vars);
    }
    function applyDomI18n() {
      document.querySelectorAll('[data-i18n]').forEach((el) => {
        const key = el.getAttribute('data-i18n');
        if (key) el.textContent = t(key);
      });
      document.querySelectorAll('[data-i18n-title]').forEach((el) => {
        const key = el.getAttribute('data-i18n-title');
        if (key) el.setAttribute('title', t(key));
      });
      document.title = t('login.docTitle');
      const btn = document.getElementById('btnTogglePw');
      const pw = document.getElementById('password');
      if (btn && pw) btn.textContent = t(pw.type === 'password' ? 'login.show' : 'login.hide');
    }
    function setLocale(loc) {
      if (loc !== 'zh' && loc !== 'en') return;
      currentLocale = loc;
      try { localStorage.setItem(LS_LOCALE, loc); } catch (e) {}
      document.documentElement.lang = loc === 'zh' ? 'zh-CN' : 'en';
      document.querySelectorAll('.login-lang-btn').forEach((btn) => {
        btn.classList.toggle('active', btn.getAttribute('data-locale') === loc);
      });
      applyDomI18n();
    }
    document.querySelectorAll('.login-lang-btn').forEach((btn) => {
      btn.addEventListener('click', () => setLocale(btn.getAttribute('data-locale')));
    });

    function sanitizeFrom(raw) {
      const value = (raw || '/').trim() || '/';
      if (!value.startsWith('/') || value.startsWith('//') || value.startsWith('/login')) return '/';
      return value;
    }
    function readFrom() {
      const q = new URLSearchParams(location.search).get('from');
      return sanitizeFrom(q || '/');
    }

    const boot = document.getElementById('authBoot');
    const page = document.getElementById('loginPage');
    const form = document.getElementById('loginForm');
    const errEl = document.getElementById('loginError');
    const btnSubmit = document.getElementById('btnSubmit');
    const btnTogglePw = document.getElementById('btnTogglePw');
    const pwInput = document.getElementById('password');
    const userInput = document.getElementById('username');

    btnTogglePw.addEventListener('click', () => {
      const show = pwInput.type === 'password';
      pwInput.type = show ? 'text' : 'password';
      btnTogglePw.textContent = t(show ? 'login.hide' : 'login.show');
    });

    async function probe() {
      setLocale(currentLocale);
      try {
        const r = await fetch('/api/auth/me', { credentials: 'same-origin' });
        const j = await r.json();
        if (!j.authRequired || j.authenticated) {
          location.replace(readFrom());
          return;
        }
        if (j.usernameHint) userInput.value = j.usernameHint;
      } catch (e) {
        /* show form anyway */
      }
      boot.classList.add('hidden');
      page.classList.remove('hidden');
      applyDomI18n();
    }

    form.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      errEl.textContent = '';
      btnSubmit.disabled = true;
      btnSubmit.textContent = t('login.submitting');
      try {
        const r = await fetch('/api/auth/login', {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            username: userInput.value.trim(),
            password: pwInput.value,
          }),
        });
        const j = await r.json().catch(() => ({}));
        if (!r.ok || !j.ok) {
          const raw = j.error || ('HTTP ' + r.status);
          errEl.textContent = (!raw || /^HTTP \d+$/.test(raw) || raw === 'invalid_credentials' || raw === 'empty_credentials')
            ? t('login.fail')
            : raw;
          return;
        }
        location.replace(readFrom());
      } catch (e) {
        errEl.textContent = e instanceof Error ? e.message : t('login.networkError');
      } finally {
        btnSubmit.disabled = false;
        btnSubmit.textContent = t('login.submit');
      }
    });

    probe();
  </script>
</body>
</html>
"""
