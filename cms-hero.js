(() => {
  const config = document.getElementById('cms-montage');
  const host = document.getElementById('heroMontage');
  const toggle = document.getElementById('montageToggle');
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  if (!config || !host || !toggle || reduced.matches || navigator.connection?.saveData) return;
  const {clips, shuffle} = JSON.parse(config.textContent);
  if (!clips.length) return;
  let queue = [], current, pending, timer;
  let paused = false, visible = true, lastKey;
  const key = clip => clip.provider + ':' + clip.id;
  const allowed = () => !paused && visible && !document.hidden && !reduced.matches;
  try { lastKey = sessionStorage.getItem('joo-cms-last-background'); } catch {}
  function next() {
    if (!queue.length) {
      queue = [...clips];
      if (shuffle) {
        for (let i = queue.length - 1; i > 0; i--) {
          const j = Math.floor(Math.random() * (i + 1));
          [queue[i], queue[j]] = [queue[j], queue[i]];
        }
        if (queue.length > 1 && key(queue[0]) === lastKey) [queue[0], queue[1]] = [queue[1], queue[0]];
      }
    }
    const clip = queue.shift();
    lastKey = key(clip);
    try { sessionStorage.setItem('joo-cms-last-background', lastKey); } catch {}
    return clip;
  }
  function loadYouTube() {
    if (window.YT?.Player) return Promise.resolve();
    if (loadYouTube.promise) return loadYouTube.promise;
    loadYouTube.promise = new Promise((resolve, reject) => {
      const original = window.onYouTubeIframeAPIReady;
      const tag = document.createElement('script');
      const timeout = setTimeout(() => fail(), 12000);
      const fail = () => {
        clearTimeout(timeout);
        tag.remove();
        window.onYouTubeIframeAPIReady = original;
        loadYouTube.promise = null;
        reject(new Error('YouTube player could not load'));
      };
      window.onYouTubeIframeAPIReady = () => {
        clearTimeout(timeout);
        try { original?.(); } finally { resolve(); }
      };
      tag.src = 'https://www.youtube.com/iframe_api';
      tag.onerror = fail;
      document.head.append(tag);
    });
    return loadYouTube.promise;
  }
  function loadVimeo() {
    if (window.Vimeo?.Player) return Promise.resolve();
    if (loadVimeo.promise) return loadVimeo.promise;
    loadVimeo.promise = new Promise((resolve, reject) => {
      const tag = document.createElement('script');
      const timeout = setTimeout(() => fail(), 12000);
      const fail = () => {
        clearTimeout(timeout);
        tag.remove();
        loadVimeo.promise = null;
        reject(new Error('Vimeo player could not load'));
      };
      tag.src = 'https://player.vimeo.com/api/player.js';
      tag.onload = () => { clearTimeout(timeout); resolve(); };
      tag.onerror = fail;
      document.head.append(tag);
    });
    return loadVimeo.promise;
  }
  function call(item, method) {
    try { Promise.resolve(item?.[method]?.()).catch(() => {}); } catch {}
  }
  function dispose(item) {
    if (!item || item.dead) return;
    item.dead = true;
    clearTimeout(item.timeout);
    call(item, 'destroy');
    item.element.remove();
  }
  function cover(item) {
    if (!item) return;
    const {width, height} = host.getBoundingClientRect();
    const frameWidth = Math.max(width, height * 16 / 9);
    item.frame.style.width = frameWidth + 'px';
    item.frame.style.height = frameWidth * 9 / 16 + 'px';
  }
  function schedule(delay = current?.clip.seconds != null ? current.clip.seconds * 1000 : 6500 + Math.random() * 2500) {
    clearTimeout(timer);
    if (allowed()) timer = setTimeout(advance, delay);
  }
  function ready(item) {
    if (pending !== item || item.dead) return;
    if (!allowed()) { call(item, 'pause'); return; }
    clearTimeout(item.timeout);
    const previous = current;
    current = item;
    pending = null;
    item.element.classList.add('is-active');
    if (!host.dataset.startedMs) host.dataset.startedMs = String(Math.round(performance.now()));
    if (previous) {
      previous.element.classList.remove('is-active');
      setTimeout(() => dispose(previous), 1100);
    }
    toggle.hidden = false;
    schedule();
  }
  async function advance() {
    if (pending || !allowed()) return;
    const clip = next();
    const element = document.createElement('div');
    element.className = 'montage-frame';
    const frame = document.createElement('iframe');
    frame.title = 'Decorative portfolio montage';
    frame.tabIndex = -1;
    frame.setAttribute('aria-hidden', 'true');
    frame.allow = 'autoplay; encrypted-media; picture-in-picture';
    frame.referrerPolicy = 'strict-origin-when-cross-origin';
    const item = {clip, element, frame};
    pending = item;
    const fail = () => {
      if (pending !== item) return;
      pending = null;
      dispose(item);
      schedule(1200);
    };
    item.timeout = setTimeout(fail, 25000);
    try {
      if (clip.provider === 'youtube') {
        await loadYouTube();
        if (item.dead) return;
        const params = new URLSearchParams({enablejsapi: '1', autoplay: '0', mute: '1', controls: '0',
          playsinline: '1', rel: '0', disablekb: '1', loop: '1', playlist: clip.id,
          start: String(clip.start), origin: location.origin});
        frame.src = 'https://www.youtube.com/embed/' + clip.id + '?' + params;
        element.append(frame);
        host.append(element);
        cover(item);
        const player = new YT.Player(frame, {events: {
          onReady(event) {
            if (item.dead) return;
            event.target.mute();
            if (allowed()) event.target.playVideo();
          },
          onStateChange(event) {
            if (event.data === YT.PlayerState.PLAYING) ready(item);
          },
          onError: fail, onAutoplayBlocked: fail,
        }});
        item.pause = () => player.pauseVideo();
        item.play = () => player.playVideo();
        item.destroy = () => player.destroy();
      } else {
        await loadVimeo();
        if (item.dead) return;
        const params = new URLSearchParams({dnt: '1', muted: '1', controls: '0',
          playsinline: '1', loop: '1', autopause: '0'});
        if (clip.access) params.set('h', clip.access);
        frame.src = 'https://player.vimeo.com/video/' + clip.id + '?' + params;
        element.append(frame);
        host.append(element);
        cover(item);
        const player = new Vimeo.Player(frame);
        item.pause = () => player.pause();
        item.play = () => player.play();
        item.destroy = () => player.destroy();
        player.on('playing', () => ready(item));
        player.on('error', fail);
        await player.ready();
        await player.setMuted(true);
        if (clip.start) await player.setCurrentTime(clip.start);
        if (!item.dead && allowed()) await player.play();
      }
    } catch { fail(); }
  }
  function sync() {
    clearTimeout(timer);
    if (!allowed()) {
      call(current, 'pause');
      call(pending, 'pause');
      return;
    }
    call(current, 'play');
    call(pending, 'play');
    if (current) schedule();
    else if (!pending) advance();
  }
  toggle.addEventListener('click', () => {
    paused = !paused;
    toggle.setAttribute('aria-pressed', String(paused));
    toggle.textContent = paused ? 'Play background' : 'Pause background';
    sync();
  });
  document.addEventListener('visibilitychange', sync);
  reduced.addEventListener('change', sync);
  new IntersectionObserver(([entry]) => { visible = entry.isIntersecting; sync(); }).observe(host);
  new ResizeObserver(() => { cover(current); cover(pending); }).observe(host);
})();
