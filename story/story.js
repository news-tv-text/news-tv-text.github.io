/* ЛАМПЫ ЧЕРЕЗ ОДНУ — телетекст-квест.
   Страницы набираются тремя цифрами, как на настоящем декодере.
   Числа, попадающиеся в рассказе (116, 207, 209, 212, 313, 214), — реальные номера. */

'use strict';

const STORE = 'lampy.v1';
const $ = (id) => document.getElementById(id);

const el = {
  crt: $('crt'), screen: $('screen'), page: $('page'), pageno: $('pageno'),
  digits: $('digits'), ticker: $('ticker'), clock: $('clock'), lamps: $('lamps'),
  anchor: $('anchor'), chan: $('chan'), subtitle: $('subtitle'),
  pad: $('pad'), sndbtn: $('sndbtn'), static: $('static')
};

let C = null;                 // content.json
let chapterByPage = new Map();
let entry = '';               // набираемый номер
let current = null;           // текущая страница (строка)
let typing = null;            // активный таймер печати
let tickCount = 0;

const state = {
  read: new Set(), visited: new Set(), finished: false, sound: false
};

/* ---------- сохранение ---------- */

function load() {
  try {
    const raw = JSON.parse(localStorage.getItem(STORE) || '{}');
    state.read = new Set(raw.read || []);
    state.visited = new Set(raw.visited || []);
    state.finished = !!raw.finished;
  } catch (_) { /* первый визит */ }
}

function save() {
  try {
    localStorage.setItem(STORE, JSON.stringify({
      read: [...state.read], visited: [...state.visited], finished: state.finished
    }));
  } catch (_) { /* приватный режим — просто не сохраняем */ }
}

/* ---------- звук: гул трансформатора, щелчки, разряд ---------- */

const Audio_ = {
  ctx: null, hum: null,
  init() {
    if (this.ctx) return;
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return;
    this.ctx = new AC();
    const g = this.ctx.createGain();
    g.gain.value = 0.035;
    const lp = this.ctx.createBiquadFilter();
    lp.type = 'lowpass'; lp.frequency.value = 220;
    // 50 Гц и её третья гармоника — стойка в аппаратной
    [50, 150].forEach((f, i) => {
      const o = this.ctx.createOscillator();
      o.type = i ? 'triangle' : 'sine';
      o.frequency.value = f;
      o.connect(lp);
      o.start();
    });
    lp.connect(g); g.connect(this.ctx.destination);
    this.hum = g;
  },
  noise(dur, vol, freq) {
    if (!state.sound || !this.ctx) return;
    const n = Math.floor(this.ctx.sampleRate * dur);
    const buf = this.ctx.createBuffer(1, n, this.ctx.sampleRate);
    const d = buf.getChannelData(0);
    for (let i = 0; i < n; i++) d[i] = (Math.random() * 2 - 1) * (1 - i / n);
    const src = this.ctx.createBufferSource(); src.buffer = buf;
    const bp = this.ctx.createBiquadFilter();
    bp.type = 'bandpass'; bp.frequency.value = freq; bp.Q.value = 0.8;
    const g = this.ctx.createGain(); g.gain.value = vol;
    src.connect(bp); bp.connect(g); g.connect(this.ctx.destination);
    src.start();
  },
  click() { this.noise(0.03, 0.16, 2400); },
  burst() { this.noise(0.28, 0.1, 1200); },
  toggle() {
    state.sound = !state.sound;
    if (state.sound) { this.init(); this.ctx && this.ctx.resume(); }
    if (this.hum) this.hum.gain.value = state.sound ? 0.035 : 0;
    el.sndbtn.textContent = state.sound ? 'ЗВУК ВКЛ' : 'ЗВУК ВЫКЛ';
  }
};

/* ---------- помехи ---------- */

const calm = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

function staticBurst(ms = 260) {
  if (calm) return;
  const cv = el.static, ctx = cv.getContext('2d');
  const w = cv.width = Math.floor(window.innerWidth / 3);
  const h = cv.height = Math.floor(window.innerHeight / 3);
  const img = ctx.createImageData(w, h);
  const stop = performance.now() + ms;
  cv.style.opacity = '0.12';
  (function frame(now) {
    const d = img.data;
    for (let i = 0; i < d.length; i += 4) {
      const v = Math.random() * 255;
      d[i] = d[i + 1] = d[i + 2] = v;
      d[i + 3] = Math.random() < 0.45 ? 70 : 0;
    }
    ctx.putImageData(img, 0, 0);
    if (now < stop) requestAnimationFrame(frame);
    else cv.style.opacity = '0';
  })(performance.now());
  Audio_.burst();
}

function glitch() {
  if (calm) return;
  document.body.classList.add('glitch');
  setTimeout(() => document.body.classList.remove('glitch'), 700);
}

/* ---------- печать текста ---------- */

function stopTyping() {
  if (typing) { clearInterval(typing); typing = null; }
}

function decorate(node, text) {
  // «в отражении» — в отражении и печатается
  if (!text.includes('в отражении')) return;
  node.innerHTML = node.textContent.replace(
    /в отражении/g, '<span class="mirror">в отражении</span>'
  );
}

function typeBlocks(blocks, done) {
  stopTyping();
  let bi = 0, ci = 0, node = null;
  const speed = 9;                       // символов за такт

  typing = setInterval(() => {
    if (bi >= blocks.length) {
      stopTyping();
      if (node) node.querySelector('.cursor')?.remove();
      done && done();
      return;
    }
    const b = blocks[bi];
    if (!node) {
      node = document.createElement('p');
      node.className = 'blk ' + (b.kind || 'para') + (b.cls ? ' ' + b.cls : '');
      node.innerHTML = '<span class="cursor"></span>';
      el.page.appendChild(node);
    }
    ci = Math.min(b.text.length, ci + speed);
    node.textContent = b.text.slice(0, ci);
    if (ci < b.text.length) {
      node.insertAdjacentHTML('beforeend', '<span class="cursor"></span>');
      el.page.scrollTop = el.page.scrollHeight;
    } else {
      decorate(node, b.text);
      node = null; ci = 0; bi++;
      el.page.scrollTop = el.page.scrollHeight;
    }
  }, 26);
}

function skipTyping() {
  if (!typing) return false;
  stopTyping();
  const pending = el.page._pending;
  el.page.querySelectorAll('.blk').forEach((n) => n.querySelector('.cursor')?.remove());
  if (pending) {
    el.page.innerHTML = '';
    renderHead(pending.head);
    pending.blocks.forEach((b) => {
      const p = document.createElement('p');
      p.className = 'blk ' + (b.kind || 'para') + (b.cls ? ' ' + b.cls : '');
      p.textContent = b.text;
      decorate(p, b.text);
      el.page.appendChild(p);
    });
    pending.after && pending.after();
  }
  return true;
}

/* ---------- отрисовка страниц ---------- */

function renderHead({ title, num }) {
  const h = document.createElement('div');
  h.className = 'ptitle'; h.textContent = title;
  const n = document.createElement('div');
  n.className = 'pnum'; n.textContent = num;
  el.page.append(h, n);
}

function show(head, blocks, after) {
  stopTyping();
  el.page.innerHTML = '';
  el.page._pending = { head, blocks, after };
  renderHead(head);
  typeBlocks(blocks, after);
}

function setPageNo(code) {
  el.pageno.textContent = code;
  el.pageno.classList.remove('flip');
  void el.pageno.offsetWidth;
  el.pageno.classList.add('flip');
}

function timeFromTitle(t) {
  const m = t && t.match(/(\d{2}):(\d{2})/);
  return m ? m[0] : '--:--';
}

function renderChapter(ch) {
  document.body.classList.toggle('night', !!ch.night);
  el.clock.textContent = timeFromTitle(ch.title);
  el.anchor.textContent = ch.anchor ? '⌁ ' + ch.anchor : '';
  show(
    { title: ch.title, num: 'СТР. ' + ch.page + '   ГЛАВА ' + ch.n + ' ИЗ ' + C.chapters.length },
    ch.blocks.map((b) => ({ kind: b.kind, text: b.text })),
    () => {
      state.read.add(ch.page); save();
      if (ch.hint) {
        const p = document.createElement('p');
        p.className = 'blk sys';
        p.textContent = '⌁ ' + ch.hint;
        el.page.appendChild(p);
      }
    }
  );
}

function renderIndex() {
  el.anchor.textContent = '⌁ мутное оргстекло стенда';
  stopTyping();
  el.page.innerHTML = '';
  el.page._pending = null;
  renderHead({ title: 'ОГЛАВЛЕНИЕ', num: 'СТР. 100' });

  const wrap = document.createElement('div');
  wrap.className = 'idx';
  C.chapters.forEach((ch) => {
    const b = document.createElement('button');
    b.className = state.read.has(ch.page) ? 'done' : '';
    b.innerHTML = '<span class="no">' + ch.page + '</span>' + ch.title;
    b.onclick = () => goto(ch.page);
    wrap.appendChild(b);
  });
  el.page.appendChild(wrap);

  const tail = document.createElement('div');
  tail.style.marginTop = '18px';
  tail.className = 'blk sys';
  tail.textContent = C.help.join(' ');
  el.page.appendChild(tail);
}

function renderInventory() {
  el.anchor.textContent = '⌁ бирка химчистки';
  const found = [];
  Object.entries(C.pages).forEach(([code, p]) => {
    if (p.found && state.visited.has(code)) found.push(code + ' — ' + p.found);
  });
  const readCount = state.read.size;

  stopTyping();
  el.page.innerHTML = '';
  el.page._pending = null;
  renderHead({ title: 'БЮРО НАХОДОК', num: 'СТР. 300   ПРИЁМ И ВЫДАЧА 00:40' });

  const ul = document.createElement('ul');
  ul.className = 'inv';
  const items = found.length ? found : ['ничего не сдано'];
  items.forEach((t) => {
    const li = document.createElement('li');
    li.textContent = t;
    if (!found.length) li.className = 'empty';
    ul.appendChild(li);
  });
  el.page.appendChild(ul);

  const st = document.createElement('p');
  st.className = 'blk sys';
  st.style.marginTop = '16px';
  st.textContent = 'ПРОЧИТАНО ГЛАВ: ' + readCount + ' ИЗ ' + C.chapters.length +
    '.  НАЙДЕНО НОМЕРОВ: ' + found.length + ' ИЗ ' + C.keys_required.length + '.';
  el.page.appendChild(st);

  if (state.finished) {
    const f = document.createElement('p');
    f.className = 'blk sys bad';
    f.textContent = 'СТРАНИЦА 214 ОТКРЫТА. НЕ ВОСТРЕБОВАНО: 1.';
    el.page.appendChild(f);
  }
}

function renderStatic(code, p) {
  el.anchor.textContent = '';
  const blocks = p.paras.map((t) => ({ kind: 'para', text: t }));
  show({ title: p.title, num: 'СТР. ' + code }, blocks);
}

function renderFinal(code, p) {
  document.body.classList.add('night');
  state.finished = true; save();
  staticBurst(700); glitch();
  const blocks = p.paras.map((t) => ({ kind: 'para', text: t }));
  blocks.push({ kind: 'para', cls: 'sys bad', text: 'КОНЕЦ ЭФИРА. ДЕКОДЕР ПРОДОЛЖАЕТ ПРИЁМ.' });
  show({ title: p.title, num: 'СТР. 214   БЮРО НАХОДОК' }, blocks);
}

function renderLocked(code) {
  const missingKeys = C.keys_required.filter((k) => !state.visited.has(k));
  const unread = C.chapters.length - state.read.size;
  const lines = [{ kind: 'para', cls: 'sys bad', text: 'СТРАНИЦА ' + code + ' НЕ НАЙДЕНА.' }];

  if (code === C.final_page) {
    lines.push({ kind: 'para', text: 'Декодер её знает, но не отдаёт. Чего-то не хватает.' });
    if (unread > 0) lines.push({ kind: 'para', cls: 'sys', text: 'НЕ ПРОЧИТАНО ГЛАВ: ' + unread + '.' });
    if (missingKeys.length) {
      lines.push({ kind: 'para', cls: 'sys', text: 'НЕ СДАНО В БЮРО НАХОДОК: ' + missingKeys.length + '.' });
      lines.push({ kind: 'para', text: 'Числа лежат в тексте. Их не прячут — их просто произносят вслух.' });
    }
  } else {
    lines.push({ kind: 'para', text: 'Такого номера в сетке нет. Проверьте по оглавлению — страница 100.' });
  }
  show({ title: 'ОШИБКА ПРИЁМА', num: 'СТР. ' + code }, lines);
}

/* ---------- навигация ---------- */

function goto(code) {
  code = String(code);
  Audio_.click();
  staticBurst(110);
  setPageNo(code);
  current = code;
  el.page.scrollTop = 0;

  if (history.replaceState) history.replaceState(null, '', '#' + code);

  if (code === '100') { state.visited.add(code); save(); return renderIndex(); }
  if (code === '300') { state.visited.add(code); save(); return renderInventory(); }

  const ch = chapterByPage.get(code);
  if (ch) { state.visited.add(code); save(); return renderChapter(ch); }

  const p = C.pages[code];
  if (!p) return renderLocked(code);

  if (p.kind === 'final') {
    const ok = state.read.size >= C.chapters.length &&
               C.keys_required.every((k) => state.visited.has(k));
    if (!ok) return renderLocked(code);
    state.visited.add(code); save();
    return renderFinal(code, p);
  }

  state.visited.add(code); save();
  if (p.found) { glitch(); }
  renderStatic(code, p);
}

function step(delta) {
  const ch = chapterByPage.get(current);
  let idx = ch ? ch.n - 1 : -1;
  if (idx < 0) idx = delta > 0 ? -1 : C.chapters.length;
  const next = C.chapters[idx + delta];
  if (next) goto(next.page);
}

/* ---------- ввод ---------- */

function pushDigit(d) {
  entry = (entry + d).slice(-3);
  el.digits.textContent = (entry + '___').slice(0, 3);
  Audio_.click();
  if (entry.length === 3) {
    const code = entry;
    entry = '';
    setTimeout(() => { el.digits.textContent = '___'; goto(code); }, 160);
  }
}

document.addEventListener('keydown', (e) => {
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  if (/^\d$/.test(e.key)) { pushDigit(e.key); return; }
  switch (e.key) {
    case 'Backspace': entry = entry.slice(0, -1); el.digits.textContent = (entry + '___').slice(0, 3); break;
    case 'ArrowRight': case ' ': case 'Enter': e.preventDefault(); if (!skipTyping()) step(1); break;
    case 'ArrowLeft': step(-1); break;
    case 'Escape': goto('100'); break;
    case 's': case 'S': case 'ы': case 'Ы': Audio_.toggle(); break;
  }
});

el.page.addEventListener('click', skipTyping);

document.querySelectorAll('.key, .padkey').forEach((btn) => {
  btn.addEventListener('click', () => {
    const d = btn.dataset.d;
    if (d) return pushDigit(d);
    switch (btn.dataset.act) {
      case 'next': if (!skipTyping()) step(1); break;
      case 'prev': step(-1); break;
      case 'index': goto('100'); break;
      case 'found': goto('300'); break;
      case 'sound': Audio_.toggle(); break;
      case 'clear': entry = ''; el.digits.textContent = '___'; break;
      case 'pad': el.pad.hidden = !el.pad.hidden; break;
    }
  });
});

/* ---------- бегущая строка ---------- */

function nextTicker() {
  tickCount++;
  const progress = state.read.size / C.chapters.length;
  // Чем дальше читатель, тем чаще в блок попадает то, чего никто не набирал
  const chance = 0.12 + progress * 0.45;
  const intrude = tickCount > 2 && Math.random() < chance;

  const pool = intrude ? C.ticker.intrusions : C.ticker.normal;
  const text = pool[Math.floor(Math.random() * pool.length)];

  el.ticker.textContent = '••• ' + text + '   ';
  el.ticker.classList.toggle('intrusion', intrude);
  el.ticker.style.animation = 'none';
  void el.ticker.offsetWidth;
  el.ticker.style.animation = '';

  if (intrude) { glitch(); staticBurst(180); lampFlicker(); }
}

function lampFlicker() {
  el.lamps.textContent = 'ЛАМПЫ 16';
  el.lamps.classList.add('wrong');
  setTimeout(() => {
    el.lamps.textContent = 'ЛАМПЫ 14';
    el.lamps.classList.remove('wrong');
  }, 2600);
}

/* ---------- запуск ---------- */

async function boot() {
  load();
  // В однофайловой сборке (превью) контент уже лежит в странице
  C = window.__CONTENT__ || await (await fetch('content.json', { cache: 'no-cache' })).json();
  C.chapters.forEach((ch) => chapterByPage.set(ch.page, ch));

  el.chan.textContent = C.channel;
  el.subtitle.textContent = C.subtitle;
  document.title = C.channel + ' // ' + C.title;

  // Прямая ссылка вида .../story/#101 открывает страницу сразу
  const deep = (location.hash || '').replace('#', '');
  if (/^\d{3}$/.test(deep)) {
    el.ticker.addEventListener('animationiteration', nextTicker);
    nextTicker();
    return goto(deep);
  }

  el.page.classList.add('boot');
  setPageNo('---');
  typeBlocks(
    C.boot.map((t) => ({ kind: 'para', text: t })).concat([
      { kind: 'para', text: '' },
      { kind: 'para', text: C.title },
      { kind: 'para', text: state.read.size ? 'Приём восстановлен. Прочитано глав: ' + state.read.size + '.' : 'Наберите ' + C.first_page + '.' }
    ]),
    () => { el.page.classList.remove('boot'); }
  );

  el.ticker.addEventListener('animationiteration', nextTicker);
  nextTicker();
  setTimeout(nextTicker, 400);
}

boot().catch(() => {
  el.page.innerHTML = '<p class="blk sys bad">НЕТ СИГНАЛА. content.json не загрузился — ' +
    'страницу нужно открывать по http, а не как файл.</p>';
});
