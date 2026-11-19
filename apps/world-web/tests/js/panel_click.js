/* Drive the shipped `index.html` in jsdom: click a Mission Control row (and a floor
   dot) and read the AGENT PANEL back. This is the browser half of the WORLD-11 test —
   it runs the real page, not a copy of its logic, and it is not a build step: the page
   still ships with no framework, no bundle and no `<script src>`.

   usage: node panel_click.js <index.html> <fixture.json>
   stdout: one JSON object (see `panel()` below for the fields it reports). */

'use strict';

const fs = require('fs');
const { JSDOM, VirtualConsole } = require('jsdom');

const htmlPath = process.argv[2];
const fixturePath = process.argv[3];
const fixture = JSON.parse(fs.readFileSync(fixturePath, 'utf8'));
const html = fs.readFileSync(htmlPath, 'utf8');

const consoleErrors = [];
const consoleWarnings = [];

/* A WebSocket the test controls: opens once, then pushes whatever frame we hand it. */
const sockets = [];
class FakeWebSocket {
  constructor(url) {
    this.url = url;
    this.readyState = 0;
    sockets.push(this);
    setTimeout(() => {
      this.readyState = 1;
      if (this.onopen) this.onopen({});
    }, 0);
  }
  send() {}
  close() {
    this.readyState = 3;
    if (this.onclose) this.onclose({});
  }
  push(frame) {
    if (this.onmessage) this.onmessage({ data: JSON.stringify(frame) });
  }
}

function panel(window) {
  const doc = window.document;
  const text = (id) => {
    const el = doc.getElementById(id);
    return el ? el.textContent : null;
  };
  const thought = doc.getElementById('detail-thought');
  const said = doc.getElementById('detail-last-said');
  return {
    who: text('detail-who'),
    name: text('detail-name'),
    role: text('detail-role'),
    state: text('detail-state'),
    room: text('detail-room'),
    elapsed: text('detail-elapsed'),
    ticket: text('detail-ticket'),
    title: text('detail-title'),
    thought: text('detail-thought'),
    thoughtSource: thought ? thought.dataset.source || '' : '',
    lastSaid: text('detail-last-said'),
    lastSaidTick: said ? said.dataset.tick || '' : '',
    lastSaidAgent: said ? said.dataset.agent || '' : '',
    panelAgent: doc.getElementById('detail').dataset.agent || '',
    hint: text('detail-hint')
  };
}

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function main() {
  const virtualConsole = new VirtualConsole();
  virtualConsole.on('jsdomError', (err) => consoleErrors.push(String(err && err.message)));
  virtualConsole.on('error', (msg) => consoleErrors.push(String(msg)));
  virtualConsole.on('warn', (msg) => consoleWarnings.push(String(msg)));

  const dom = new JSDOM(html, {
    runScripts: 'dangerously',
    url: 'http://127.0.0.1:8099/',
    pretendToBeVisual: true,
    virtualConsole,
    beforeParse(window) {
      // the page's only two transports, stubbed at the edge: no engine, no network
      window.WebSocket = FakeWebSocket;
      window.fetch = (url) =>
        Promise.resolve({
          ok: true,
          status: 200,
          url: String(url),
          json: () => Promise.resolve(JSON.parse(JSON.stringify(fixture.snapshot)))
        });
    }
  });

  const { window } = dom;
  const doc = window.document;
  await wait(30); // DOMContentLoaded + the snapshot promise

  const out = {rows: doc.querySelectorAll('#mc-body tr').length,
               dots: doc.querySelectorAll('#layer-agents .agent').length,
               sockets: sockets.length};

  out.initial = panel(window); // nothing clicked yet: an explicit empty state

  const rowOf = (id) => doc.querySelector('#mc-body tr[data-agent="' + id + '"]');
  const dotOf = (id) => doc.querySelector('#layer-agents .agent[data-agent="' + id + '"]');

  // 1. click the Mission Control row of the agent the fixture describes
  rowOf(fixture.click).dispatchEvent(new window.MouseEvent('click', {bubbles: true}));
  out.clicked = panel(window);

  // 2. click a different row: the panel must follow the click, not the first one
  rowOf(fixture.other).dispatchEvent(new window.MouseEvent('click', {bubbles: true}));
  out.other = panel(window);

  // 3. click the floor dot of the first agent again
  dotOf(fixture.click).dispatchEvent(new window.MouseEvent('click', {bubbles: true}));
  out.dot = panel(window);

  // 4. a live delta: new thought + a newer message must reach the open panel
  if (sockets.length) sockets[0].push(fixture.delta);
  await wait(10);
  out.delta = panel(window);

  // 5. clicking the selected row again closes the panel (explicit empty state)
  rowOf(fixture.click).dispatchEvent(new window.MouseEvent('click', {bubbles: true}));
  out.deselected = panel(window);

  out.errors = consoleErrors;
  out.warnings = consoleWarnings;
  out.transcriptRows = doc.querySelectorAll('#transcript li').length;
  out.selectedRows = doc.querySelectorAll('#mc-body tr.is-selected').length;
  out.selectedDots = doc.querySelectorAll('#layer-agents .agent.is-selected').length;
  console.log(JSON.stringify(out));
  window.close();
}

main().catch((err) => {
  console.log(JSON.stringify({fatal: String(err && err.stack || err)}));
  process.exitCode = 1;
});
