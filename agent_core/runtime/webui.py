"""Built-in minimal web chat UI.

Served at GET / by the FastAPI server so the harness is usable out of the box:
clone -> `python -m agent_core` -> open http://127.0.0.1:8848 -> chat.

Zero external dependencies (no CDN, no build step). The page talks to the existing
HTTP API: POST /goal to submit, then poll GET /tasks/{id} until the task finishes,
showing live progress from the task's event log.
"""
from __future__ import annotations

CHAT_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>agent-core</title>
<style>
  :root{
    --bg:#0f1216; --panel:#161b22; --border:#262c36; --text:#e6edf3;
    --muted:#8b949e; --user:#1f6feb; --bot:#21262d; --err:#f85149; --accent:#2ea043;
  }
  *{box-sizing:border-box}
  html,body{height:100%;margin:0}
  body{background:var(--bg);color:var(--text);font:15px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;display:flex;flex-direction:column}
  header{padding:12px 16px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:10px}
  header h1{font-size:15px;margin:0;font-weight:600}
  header .dot{width:8px;height:8px;border-radius:50%;background:var(--accent)}
  header .sub{color:var(--muted);font-size:12px;margin-left:auto}
  header a{color:var(--muted);text-decoration:none;font-size:12px;margin-left:12px}
  header a:hover{color:var(--text)}
  #log{flex:1;overflow-y:auto;padding:18px 16px;display:flex;flex-direction:column;gap:12px;max-width:820px;width:100%;margin:0 auto}
  .row{display:flex}
  .row.user{justify-content:flex-end}
  .bubble{max-width:78%;padding:10px 13px;border-radius:14px;white-space:pre-wrap;word-wrap:break-word;overflow-wrap:anywhere}
  .row.user .bubble{background:var(--user);color:#fff;border-bottom-right-radius:4px}
  .row.bot .bubble{background:var(--bot);border:1px solid var(--border);border-bottom-left-radius:4px}
  .status{color:var(--muted);font-style:italic}
  .err{color:var(--err)}
  .note{color:var(--muted);font-size:12px;margin-top:6px}
  .result{white-space:pre-wrap}
  .empty{color:var(--muted);text-align:center;margin:auto;font-size:14px}
  footer{border-top:1px solid var(--border);padding:12px 16px}
  .composer{max-width:820px;margin:0 auto;display:flex;gap:8px;align-items:flex-end}
  textarea{flex:1;resize:none;background:var(--panel);color:var(--text);border:1px solid var(--border);border-radius:10px;padding:10px 12px;font:inherit;max-height:160px}
  textarea:focus{outline:none;border-color:var(--user)}
  button{background:var(--user);color:#fff;border:0;border-radius:10px;padding:0 16px;height:40px;font:inherit;font-weight:600;cursor:pointer}
  button:disabled{opacity:.5;cursor:default}
  header .hbtn{background:transparent;border:1px solid var(--border);color:var(--muted);height:24px;width:30px;padding:0;border-radius:6px;font-size:15px;font-weight:500;cursor:pointer}
  header .hbtn:hover{color:var(--text)}
  body.collapsed #log{display:none}
  body.collapsed #routines{display:none}
  @media (max-width:480px){
    header{gap:6px;padding:8px 10px}
    header .sub{display:none}
    header h1{font-size:14px}
    #log{padding:12px 10px;gap:10px}
    .bubble{max-width:92%}
    footer{padding:10px}
  }
  #routines input, #routines select{background:var(--panel);color:var(--text);border:1px solid var(--border);border-radius:8px;padding:6px 8px;font:inherit;font-size:13px}
  button.mini{height:32px;padding:0 12px;font-size:13px;font-weight:500;border-radius:8px}
  .btn-sec{background:var(--bot);border:1px solid var(--border);color:var(--text)}
  .rmeta{color:var(--muted);font-size:12px}
  .ritem{display:flex;align-items:center;gap:8px;background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:6px 10px;font-size:13px}
  .ritem.off{opacity:.5}
  .ritem .rn{font-weight:500}
  .ritem button{height:26px;padding:0 8px;font-size:12px;font-weight:500;border-radius:6px}
</style>
</head>
<body>
<header>
  <span class="dot"></span><h1>agent-core</h1>
  <span class="sub">throw it a goal — it runs autonomously</span>
  <a href="#" id="routinesBtn">routines</a>
  <a href="/docs" target="_blank">API</a>
  <a href="/tasks" target="_blank">tasks</a>
  <button id="collapseBtn" class="hbtn" title="Collapse / expand">–</button>
</header>
<div id="routines" style="display:none;border-bottom:1px solid var(--border);padding:12px 16px;max-width:820px;width:100%;margin:0 auto">
  <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px">
    <b>Autonomy</b>
    <label style="font-size:12px;color:var(--muted);display:flex;align-items:center;gap:6px">
      <input type="checkbox" id="autoSwitch"> run enabled routines on schedule
    </label>
    <span id="autoState" style="font-size:12px;color:var(--muted);margin-left:auto"></span>
  </div>
  <div id="rlist" style="display:flex;flex-direction:column;gap:6px"></div>
  <div style="display:flex;gap:6px;margin-top:10px;flex-wrap:wrap;align-items:center">
    <select id="rpreset"></select>
    <button id="addPreset" class="mini btn-sec">Add preset</button>
    <span class="rmeta">or</span>
    <input type="text" id="rname" placeholder="name" style="width:120px">
    <input type="text" id="rprompt" placeholder="prompt (goal)" style="flex:1;min-width:160px">
    <input type="number" id="rint" value="24" title="interval hours" style="width:56px">
    <button id="addCustom" class="mini btn-sec">Add</button>
  </div>
</div>
<div id="log"><div class="empty">Send a goal to start. e.g. "create hello.txt in workspace with hi"</div></div>
<footer>
  <div class="composer">
    <textarea id="msg" rows="1" placeholder="Describe a goal…  (Enter to send, Shift+Enter for newline)"></textarea>
    <button id="send">Send</button>
  </div>
</footer>
<script>
  const log = document.getElementById('log');
  const input = document.getElementById('msg');
  const sendBtn = document.getElementById('send');
  const esc = s => (s||'').replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
  function el(cls, html){ const d=document.createElement('div'); d.className=cls; d.innerHTML=html; return d; }
  function scroll(){ log.scrollTop = log.scrollHeight; }
  function autosize(){ input.style.height='auto'; input.style.height=Math.min(input.scrollHeight,160)+'px'; }

  async function send(){
    const text = input.value.trim();
    if(!text) return;
    const empty = log.querySelector('.empty'); if(empty) empty.remove();
    input.value=''; autosize();
    log.appendChild(el('row user', '<div class="bubble">'+esc(text)+'</div>'));
    const asst = el('row bot', '<div class="bubble"><span class="status">queued…</span></div>');
    log.appendChild(asst); scroll();
    const bubble = asst.querySelector('.bubble');
    sendBtn.disabled = true;
    try{
      const r = await fetch('/goal', {method:'POST', headers:{'content-type':'application/json'}, body: JSON.stringify({goal:text})});
      if(!r.ok){ bubble.innerHTML = '<span class="err">error: '+esc(await r.text())+'</span>'; return; }
      const data = await r.json();
      await poll(data.task_id, bubble);
    }catch(e){ bubble.innerHTML = '<span class="err">network error: '+esc(''+e)+'</span>'; }
    finally{ sendBtn.disabled = false; input.focus(); }
  }

  async function poll(id, bubble){
    while(true){
      await new Promise(r=>setTimeout(r,1200));
      let t;
      try{ const res = await fetch('/tasks/'+id); if(!res.ok){continue;} t = await res.json(); }
      catch(e){ continue; }
      const ev = (t.events && t.events.length) ? t.events[t.events.length-1].message : '';
      if(t.status==='queued' || t.status==='running'){
        bubble.innerHTML = '<span class="status">'+esc(t.status)+(ev? ' · '+esc(ev.slice(0,140)) : '')+'…</span>';
        scroll(); continue;
      }
      if(t.status==='done' || t.status==='incomplete'){
        bubble.innerHTML = '<div class="result">'+esc(t.result || '(no result)')+'</div>' +
          (t.status==='incomplete' ? '<div class="note">incomplete — hit the turn limit. POST /tasks/'+esc(id)+'/resume to continue.</div>' : '');
      } else if(t.status==='error'){
        bubble.innerHTML = '<span class="err">error: '+esc(t.error || 'unknown')+'</span>';
      } else {
        bubble.innerHTML = '<span class="note">'+esc(t.status)+'</span>';
      }
      scroll(); return;
    }
  }

  input.addEventListener('input', autosize);
  input.addEventListener('keydown', e => { if(e.key==='Enter' && !e.shiftKey){ e.preventDefault(); send(); }});
  sendBtn.addEventListener('click', send);
  input.focus();

  // ---- collapse / expand (folds to a slim bar; resizes the native window too) ----
  const collapseBtn = document.getElementById('collapseBtn');
  let collapsed = false;
  collapseBtn.addEventListener('click', () => {
    collapsed = !collapsed;
    document.body.classList.toggle('collapsed', collapsed);
    collapseBtn.textContent = collapsed ? '▢' : '–';
    collapseBtn.title = collapsed ? 'Expand' : 'Collapse';
    try {
      if (window.pywebview && window.pywebview.api && window.pywebview.api.resize) {
        window.pywebview.api.resize(460, collapsed ? 140 : 640);
      }
    } catch (e) {}
  });

  // ---- routines panel (opt-in autonomy) ----
  const rPanel = document.getElementById('routines');
  const autoSwitch = document.getElementById('autoSwitch');
  document.getElementById('routinesBtn').addEventListener('click', e => {
    e.preventDefault();
    rPanel.style.display = rPanel.style.display==='none' ? 'block' : 'none';
    if(rPanel.style.display!=='none') loadRoutines();
  });
  autoSwitch.addEventListener('change', async () => {
    await fetch('/scheduler', {method:'POST', headers:{'content-type':'application/json'}, body: JSON.stringify({enabled:autoSwitch.checked})});
    loadRoutines();
  });
  async function loadRoutines(){
    let d; try{ d = await (await fetch('/routines')).json(); }catch(e){ return; }
    autoSwitch.checked = !!d.autonomy_enabled;
    document.getElementById('autoState').textContent = d.autonomy_enabled ? 'ON — enabled routines will fire on schedule' : 'OFF — nothing runs automatically';
    document.getElementById('rlist').innerHTML = (d.routines||[]).map(r => {
      const nr = r.next_run ? new Date(r.next_run).toLocaleString() : '—';
      return '<div class="ritem'+(r.enabled?'':' off')+'">'
        + '<span class="rn">'+esc(r.name)+'</span>'
        + '<span class="rmeta">every '+r.interval_hours+'h · next '+esc(nr)+' · runs '+r.runs+'</span>'
        + '<span style="margin-left:auto"></span>'
        + '<button class="btn-sec" onclick="runRoutine(\''+r.id+'\')">Run now</button>'
        + '<button class="btn-sec" onclick="toggleRoutine(\''+r.id+'\')">'+(r.enabled?'Disable':'Enable')+'</button>'
        + '<button class="btn-sec" onclick="delRoutine(\''+r.id+'\')">✕</button>'
        + '</div>';
    }).join('') || '<span class="rmeta">No routines yet — add a preset below.</span>';
  }
  async function loadPresets(){
    try{
      const d = await (await fetch('/routines/presets')).json();
      window._presets = d.presets || [];
      document.getElementById('rpreset').innerHTML = window._presets.map((p,i) => '<option value="'+i+'">'+esc(p.name)+' — '+esc(p.description)+'</option>').join('');
    }catch(e){}
  }
  document.getElementById('addPreset').addEventListener('click', async () => {
    const p = (window._presets||[])[document.getElementById('rpreset').value]; if(!p) return;
    await fetch('/routines', {method:'POST', headers:{'content-type':'application/json'}, body: JSON.stringify({name:p.name, prompt:p.prompt, interval_hours:p.interval_hours})});
    loadRoutines();
  });
  document.getElementById('addCustom').addEventListener('click', async () => {
    const name=document.getElementById('rname').value.trim(), prompt=document.getElementById('rprompt').value.trim(), interval_hours=parseFloat(document.getElementById('rint').value)||24;
    if(!name || !prompt) return;
    await fetch('/routines', {method:'POST', headers:{'content-type':'application/json'}, body: JSON.stringify({name, prompt, interval_hours})});
    document.getElementById('rname').value=''; document.getElementById('rprompt').value='';
    loadRoutines();
  });
  window.runRoutine = async id => { await fetch('/routines/'+id+'/run', {method:'POST'}); loadRoutines(); };
  window.toggleRoutine = async id => { await fetch('/routines/'+id+'/toggle', {method:'POST'}); loadRoutines(); };
  window.delRoutine = async id => { await fetch('/routines/'+id, {method:'DELETE'}); loadRoutines(); };
  loadPresets();
</script>
</body>
</html>
"""
