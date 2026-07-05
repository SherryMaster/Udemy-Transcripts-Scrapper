function ScraperUI() {
  this.state = {
    phase: 'idle',
    course: { title: '', sections: [] },
    overall: { completed: 0, total: 0, failed: 0, skipped: 0, active: 0, success: 0, elapsedMs: 0 },
  };
  this.els = {};
  this.ws = null;
  this.startedAt = null;
}

ScraperUI.prototype.mount = function (container) {
  container.innerHTML = `
    <aside class="sidebar">
      <div class="brand">
        <div class="logo">U</div>
        <div><div class="brand-name">Udemy Scraper</div><div class="brand-sub">Transcript downloader</div></div>
      </div>
      <div class="side-section">
        <div class="side-label">Configuration</div>
        <div class="field">
          <label class="field-label">Course URL</label>
          <input class="input" id="url" placeholder="https://www.udemy.com/course/your-course/learn" />
          <div class="input-hint" id="url-hint">Enter a valid Udemy course URL</div>
        </div>
        <div class="field">
          <label class="field-label">Save to</label>
          <div class="input-row">
            <input class="input" id="dir" value="${this._defaultDir()}" />
            <button class="btn-sm" id="browse">Browse</button>
          </div>
        </div>
        <div class="slider-row">
          <label class="field-label" style="margin:0">Batch size</label>
          <input type="range" class="slider" id="batch" min="1" max="15" value="5" />
          <div class="slider-val" id="batch-val">5</div>
        </div>
        <div style="font-size:10px;color:var(--text-muted);margin-bottom:10px">lectures per batch</div>
        <div class="slider-row">
          <label class="field-label" style="margin:0">Threads</label>
          <input type="range" class="slider" id="threads" min="1" max="6" value="3" style="max-width:80px" />
          <div class="slider-val" id="threads-val">3</div>
          <div class="slider-cap">parallel workers</div>
        </div>
        <button class="btn-primary" id="start">Start Scraping</button>
        <div class="btn-row">
          <button class="btn-ghost" id="resume" disabled>Resume</button>
          <button class="btn-stop" id="stop" disabled>Stop</button>
        </div>
      </div>
      <div class="side-section">
        <div class="side-label">Overall Progress</div>
        <div class="prog-card">
          <div class="prog-head">
            <span class="prog-title">Course</span>
            <span class="prog-count" id="prog-count">0 <span>/ 0</span></span>
          </div>
          <div class="prog-bar"><div class="prog-fill" id="prog-fill"></div></div>
          <div class="stats">
            <div class="stat s"><span class="dot"></span><span><b id="st-success">0</b> success</span></div>
            <div class="stat p"><span class="dot"></span><span><b id="st-active">0</b> active</span></div>
            <div class="stat k"><span class="dot"></span><span><b id="st-skipped">0</b> skipped</span></div>
            <div class="stat f"><span class="dot"></span><span><b id="st-failed">0</b> failed</span></div>
          </div>
          <div class="prog-status" id="prog-status">Ready</div>
          <div class="prog-meta"><span id="elapsed">Elapsed 0:00</span><span id="eta">ETA —</span></div>
        </div>
      </div>
    </aside>
    <main class="main">
      <div class="main-head">
        <div><div class="course-title" id="course-title">No course loaded</div>
        <div class="course-sub" id="course-sub"></div></div>
        <div class="pill idle" id="phase-pill">IDLE</div>
      </div>
      <div class="banner" id="banner"></div>
      <div class="sections" id="sections">
        <div class="empty"><div class="empty-mark">▸</div><p>Paste a course URL to begin</p></div>
      </div>
      <div class="log-panel" id="log-panel"><div class="log-box" id="log-box"></div></div>
      <div class="log-bar" id="log-bar">
        <div class="log-bar-l"><span class="log-chev" id="log-chev">▸</span><span class="log-title">Activity Log</span></div>
        <div class="log-preview" id="log-preview">—</div>
      </div>
    </main>
    <div class="tip" id="tip"></div>
    <div class="toast" id="toast">
      <div class="toast-title" id="toast-title"></div>
      <div class="toast-body" id="toast-body"></div>
      <button id="toast-btn">Retry failed</button>
    </div>
  `;
  this.els = {
    url: document.getElementById('url'), dir: document.getElementById('dir'),
    batch: document.getElementById('batch'), batchVal: document.getElementById('batch-val'),
    threads: document.getElementById('threads'), threadsVal: document.getElementById('threads-val'),
    start: document.getElementById('start'), resume: document.getElementById('resume'),
    stop: document.getElementById('stop'), browse: document.getElementById('browse'),
    progCount: document.getElementById('prog-count'), progFill: document.getElementById('prog-fill'),
    progStatus: document.getElementById('prog-status'), elapsed: document.getElementById('elapsed'),
    eta: document.getElementById('eta'), stSuccess: document.getElementById('st-success'),
    stActive: document.getElementById('st-active'), stSkipped: document.getElementById('st-skipped'),
    stFailed: document.getElementById('st-failed'), courseTitle: document.getElementById('course-title'),
    courseSub: document.getElementById('course-sub'), phasePill: document.getElementById('phase-pill'),
    banner: document.getElementById('banner'), sections: document.getElementById('sections'),
    logPanel: document.getElementById('log-panel'), logBox: document.getElementById('log-box'),
    logBar: document.getElementById('log-bar'), logChev: document.getElementById('log-chev'),
    logPreview: document.getElementById('log-preview'), tip: document.getElementById('tip'),
    toast: document.getElementById('toast'), toastTitle: document.getElementById('toast-title'),
    toastBody: document.getElementById('toast-body'), toastBtn: document.getElementById('toast-btn'),
    urlHint: document.getElementById('url-hint'),
  };
};

ScraperUI.prototype._defaultDir = function () {
  return '~/Desktop/Udemy_Transcripts';
};

ScraperUI.prototype.bindControls = function () {
  const self = this;
  this.els.batch.addEventListener('input', () => { this.els.batchVal.textContent = this.els.batch.value; });
  this.els.threads.addEventListener('input', () => { this.els.threadsVal.textContent = this.els.threads.value; });

  this.els.start.addEventListener('click', () => this._start());
  this.els.resume.addEventListener('click', () => this._resume());
  this.els.stop.addEventListener('click', () => this._stop());
  this.els.toastBtn.addEventListener('click', () => { this._retryFailed(); this._hideToast(); });
  this.els.browse.addEventListener('click', () => this._browse());

  this.els.url.addEventListener('keydown', (e) => { if (e.key === 'Enter') this._start(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && this.state.phase === 'running') this._stop(); });

  this.els.url.addEventListener('input', () => {
    this.els.url.classList.remove('invalid');
    this.els.urlHint.classList.remove('show');
  });

  this.els.logBar.addEventListener('click', () => {
    const open = this.els.logPanel.classList.toggle('open');
    this.els.logChev.textContent = open ? '▾' : '▸';
  });

  this.els.sections.addEventListener('mouseover', (e) => this._onBoxHover(e));
  this.els.sections.addEventListener('mouseout', () => this.els.tip.classList.remove('show'));
  this.els.sections.addEventListener('click', (e) => {
    const head = e.target.closest('.sec-head');
    if (head) head.parentElement.querySelector('.grid').classList.toggle('collapsed');
  });

  window.addEventListener('beforeunload', (e) => {
    if (self.state.phase === 'running') { e.preventDefault(); e.returnValue = ''; }
  });
};

ScraperUI.prototype._browse = async function () {
  if (window.pywebview && window.pywebview.api) {
    const p = await window.pywebview.api.browse_directory();
    if (p) this.els.dir.value = p;
  } else {
    const p = prompt('Output directory:', this.els.dir.value);
    if (p) this.els.dir.value = p;
  }
};

ScraperUI.prototype._start = function () {
  const url = this.els.url.value.trim();
  if (!/https?:\/\/www\.udemy\.com\/course\//.test(url)) {
    this.els.url.classList.add('invalid');
    this.els.urlHint.classList.add('show');
    return;
  }
  this._post('/api/start', {
    url, outputDir: this.els.dir.value.trim(),
    batchSize: +this.els.batch.value, numThreads: +this.els.threads.value,
  });
};

ScraperUI.prototype._resume = function () {
  const url = this.els.url.value.trim();
  if (!/https?:\/\/www\.udemy\.com\/course\//.test(url)) {
    this.els.url.classList.add('invalid');
    this.els.urlHint.classList.add('show');
    return;
  }
  this._post('/api/resume', { url, outputDir: this.els.dir.value.trim() });
};

ScraperUI.prototype._stop = function () {
  this._post('/api/stop', {});
};

ScraperUI.prototype._retryFailed = function () {
  this._post('/api/retry-failed', {});
};

ScraperUI.prototype._post = async function (path, body) {
  try {
    await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  } catch (e) { /* server may be restarting */ }
};

ScraperUI.prototype.connectWS = function () {
  const url = (location.protocol === 'http:' ? 'ws://' + location.host : 'ws://localhost:8765') + '/ws';
  this.ws = new WebSocket(url);
  this.ws.onmessage = (m) => this.applyEvent(JSON.parse(m.data));
  this.ws.onclose = () => {
    if (this.state.phase === 'running') {
      this._setPhase('running', 'RECONNECTING…');
      setTimeout(() => this.connectWS(), 1000);
    }
  };
};

ScraperUI.prototype.applyEvent = function (ev) {
  switch (ev.type) {
    case 'course_discovered': this._onDiscovered(ev); break;
    case 'lecture_status': this._onLecture(ev); break;
    case 'progress': this._onProgress(ev); break;
    case 'log': this._onLog(ev); break;
    case 'done': this._onDone(ev); break;
    case 'error': this._onError(ev); break;
  }
};

ScraperUI.prototype._onDiscovered = function (ev) {
  this.state.course = { title: ev.courseTitle, sections: ev.sections.map(s => ({
    index: s.index, title: s.title,
    lectures: s.lectures.map(l => ({ index: l.index, id: l.id, title: l.title, status: 'pending', message: '', size: null })),
  })) };
  this.els.courseTitle.textContent = ev.courseTitle;
  const total = ev.sections.reduce((n, s) => n + s.lectures.length, 0);
  this.els.courseSub.textContent = `${ev.sections.length} sections · ${total} lectures`;
  this._renderSections();
  this._setPhase('running', 'RUNNING');
  this.startedAt = Date.now();
};

ScraperUI.prototype._onLecture = function (ev) {
  const sec = this.state.course.sections[ev.sectionIdx];
  if (!sec) return;
  const lec = sec.lectures[ev.lectureIdx];
  if (!lec) return;
  lec.status = ev.status;
  lec.message = ev.message || '';
  if (ev.size !== undefined && ev.size !== null) lec.size = ev.size;
  const box = this.els.sections.querySelector(`[data-sec="${ev.sectionIdx}"][data-lec="${ev.lectureIdx}"]`);
  if (box) {
    box.className = 'box b-' + ({ 'in-progress': 'working', success: 'success', skipped: 'skip', failed: 'fail', pending: 'pending' }[ev.status] || 'pending');
  }
  this._updateSectionMeta(ev.sectionIdx);
  this.els.progStatus.innerHTML = `Scraping: <b>${lec.title}</b>`;
};

ScraperUI.prototype._onProgress = function (ev) {
  this.state.overall = ev;
  this._updateOverall();
};

ScraperUI.prototype._onLog = function (ev) {
  const line = document.createElement('div');
  line.className = 'log-line ' + ({ success: 'ok', error: 'fail', warn: 'warn', info: '' }[ev.level] || '');
  const ts = new Date().toLocaleTimeString('en-GB');
  line.textContent = `[${ts}] ${ev.message}`;
  this.els.logBox.appendChild(line);
  this.els.logBox.scrollTop = this.els.logBox.scrollHeight;
  this.els.logPreview.textContent = line.textContent;
  if (this.els.logBox.children.length > 500) this.els.logBox.removeChild(this.els.logBox.firstChild);
};

ScraperUI.prototype._onDone = function (ev) {
  this.state.overall = { ...this.state.overall, ...ev };
  this._updateOverall();
  this._setPhase('done', 'DONE');
  this._showToast('Done', `${ev.completed} saved · ${ev.failed} failed · ${ev.skipped} skipped`, ev.failed > 0);
  this.els.progStatus.textContent = `Done — ${ev.completed} completed, ${ev.failed} failed`;
  this.els.progFill.style.width = '100%';
  this._resetButtons(true);
};

ScraperUI.prototype._onError = function (ev) {
  this._setPhase('error', 'ERROR');
  this.els.banner.textContent = ev.message + ' — open Chrome, log into Udemy, then retry.';
  this.els.banner.classList.add('show');
  this._resetButtons(true);
};

ScraperUI.prototype._renderSections = function () {
  this.els.sections.innerHTML = '';
  this.state.course.sections.forEach((sec, si) => {
    const card = document.createElement('div');
    card.className = 'sec-card';
    card.innerHTML = `
      <div class="sec-head">
        <div class="sec-title"><span class="idx">${String(sec.index).padStart(2, '0')}</span>${this._esc(sec.title)}</div>
        <div class="sec-meta">
          <div class="sec-count"><b id="cnt-${si}">0</b>/${sec.lectures.length}</div>
          <div class="sec-mini"><div class="sec-mini-fill" id="mini-${si}" style="width:0"></div></div>
        </div>
      </div>
      <div class="grid" id="grid-${si}"></div>`;
    const grid = card.querySelector(`#grid-${si}`);
    sec.lectures.forEach((lec, li) => {
      const box = document.createElement('div');
      box.className = 'box b-pending';
      box.dataset.sec = si; box.dataset.lec = li;
      box.dataset.title = lec.title;
      grid.appendChild(box);
    });
    this.els.sections.appendChild(card);
  });
};

ScraperUI.prototype._updateSectionMeta = function (si) {
  const sec = this.state.course.sections[si];
  const done = sec.lectures.filter(l => l.status === 'success' || l.status === 'skipped').length;
  const cnt = document.getElementById(`cnt-${si}`);
  const mini = document.getElementById(`mini-${si}`);
  if (cnt) cnt.textContent = done;
  if (mini) mini.style.width = (sec.lectures.length ? (done / sec.lectures.length) * 100 : 0) + '%';
};

ScraperUI.prototype._updateOverall = function () {
  const o = this.state.overall;
  this.els.progCount.innerHTML = `${o.completed} <span>/ ${o.total}</span>`;
  const pct = o.total ? (o.completed / o.total) * 100 : 0;
  this.els.progFill.style.width = pct + '%';
  this.els.stSuccess.textContent = o.success ?? 0;
  this.els.stActive.textContent = o.active ?? 0;
  this.els.stSkipped.textContent = o.skipped ?? 0;
  this.els.stFailed.textContent = o.failed ?? 0;
  const secs = Math.floor((o.elapsedMs || 0) / 1000);
  this.els.elapsed.textContent = `Elapsed ${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, '0')}`;
  if (o.completed > 0 && o.total > 0 && o.completed < o.total) {
    const rate = (o.elapsedMs || 1) / o.completed;
    const remain = Math.max(0, (o.total - o.completed) * rate / 1000);
    const rm = Math.floor(remain / 60);
    this.els.eta.textContent = `ETA ~${rm}m`;
  } else if (o.completed >= o.total && o.total > 0) {
    this.els.eta.textContent = 'ETA —';
  }
};

ScraperUI.prototype._setPhase = function (phase, label) {
  this.state.phase = phase;
  const pill = this.els.phasePill;
  pill.className = 'pill ' + phase;
  pill.innerHTML = (phase === 'running') ? `<span class="live-dot"></span> ${label || 'RUNNING'}` : (label || phase.toUpperCase());
  if (phase === 'running') { this._lockControls(true); }
};

ScraperUI.prototype._lockControls = function (locked) {
  this.els.start.disabled = locked;
  this.els.resume.disabled = locked;
  this.els.stop.disabled = !locked;
  this.els.url.disabled = locked; this.els.dir.disabled = locked;
  this.els.batch.disabled = locked; this.els.threads.disabled = locked;
};

ScraperUI.prototype._resetButtons = function (idle) {
  this.els.start.disabled = false;
  this.els.stop.disabled = true;
  this.els.url.disabled = false; this.els.dir.disabled = false;
  this.els.batch.disabled = false; this.els.threads.disabled = false;
};

ScraperUI.prototype._onBoxHover = function (e) {
  const box = e.target.closest('.box');
  if (!box) { this.els.tip.classList.remove('show'); return; }
  const si = +box.dataset.sec, li = +box.dataset.lec;
  const sec = this.state.course.sections[si];
  const lec = sec ? sec.lectures[li] : null;
  if (!lec) return;
  const statusLabel = { pending: 'Pending', 'in-progress': 'In progress', success: 'Saved', skipped: 'Skipped', failed: 'Failed' }[lec.status] || lec.status;
  this.els.tip.innerHTML = `
    <div class="tip-title">${this._esc(lec.title)}</div>
    <div class="tip-row"><span class="k">Status</span><span class="v">${statusLabel}</span></div>
    <div class="tip-row"><span class="k">Size</span><span class="v">${lec.size ? lec.size.toLocaleString() + ' chars' : '—'}</span></div>
    <div class="tip-row"><span class="k">Lecture</span><span class="v">#${li + 1} of ${sec.lectures.length}</span></div>`;
  this.els.tip.classList.add('show');
  const r = box.getBoundingClientRect();
  this.els.tip.style.left = Math.min(r.left, window.innerWidth - 240) + 'px';
  this.els.tip.style.top = (r.top - this.els.tip.offsetHeight - 10) + 'px';
};

ScraperUI.prototype._showToast = function (title, body, showRetry) {
  this.els.toastTitle.textContent = title;
  this.els.toastBody.textContent = body;
  this.els.toastBtn.style.display = showRetry ? '' : 'none';
  this.els.toast.classList.add('show');
};

ScraperUI.prototype._hideToast = function () { this.els.toast.classList.remove('show'); };

ScraperUI.prototype._esc = function (s) {
  const d = document.createElement('div'); d.textContent = s; return d.innerHTML;
};

window.ScraperUI = ScraperUI;
