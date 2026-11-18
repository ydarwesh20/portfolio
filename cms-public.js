(() => {
  if (document.currentScript.dataset.mode === 'preview') {
    window.addEventListener('message', event => {
      if (event.origin !== location.origin || event.source !== window.parent || event.data?.type !== 'joo-focus') return;
      const target = [...document.querySelectorAll('[data-cms-select]')].find(el => el.dataset.cmsSelect === event.data.id);
      if (!target) return;
      for (let el = target.parentElement; el; el = el.parentElement) {
        if (el.tagName === 'DETAILS') el.open = true;
      }
      document.querySelector('.cms-selected')?.classList.remove('cms-selected');
      target.classList.add('cms-selected');
      target.scrollIntoView({block: 'center', behavior: 'smooth'});
    });
    document.addEventListener('click', event => {
      if (event.target.closest('.grade-swipe, .portfolio-choice')) return;
      const target = event.target.closest('[data-cms-select]');
      if (event.target.closest('a')) event.preventDefault();
      if (target) parent.postMessage({type: 'joo-select', id: target.dataset.cmsSelect}, location.origin);
    });
    return;
  }
  if (navigator.globalPrivacyControl || navigator.doNotTrack === '1') return;
  const path = location.pathname;
  const send = event => fetch('/api/cms?action=event', {
    method: 'POST', credentials: 'omit', keepalive: true,
    headers: {'Content-Type': 'application/json', 'X-Joo-CMS': '1'},
    body: JSON.stringify({path, event}),
  }).catch(() => {});
  send('view');
  document.addEventListener('click', event => {
    const link = event.target.closest('a');
    if (!link) return;
    const url = new URL(link.href);
    if (link.classList.contains('film-link')) send('video');
    else if (url.protocol === 'mailto:') send('email');
    else if (url.hostname === 'wa.me' || url.hostname === 'api.whatsapp.com') send('whatsapp');
    else if (url.hostname === 'www.instagram.com' || url.hostname === 'instagram.com') send('instagram');
  });
})();
