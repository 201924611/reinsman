"""trace / compare HTML 뷰어.

정적 HTML + JS를 돌려준다. JS가 /trace, /compare JSON 엔드포인트를 fetch 해서
- 토큰 사용량 막대
- 세션별 그룹핑된 span 타임라인
- 도구 호출 체인(칩 시퀀스)
- 구조(variant) 비교 표
를 렌더한다.
"""
from __future__ import annotations

_BASE_CSS = """
  :root{--bg:#0f1117;--panel:#171a23;--panel2:#1e222e;--line:#2b3040;--txt:#e6e9f2;
        --mut:#8b93a7;--orch:#5b8cff;--sub:#3ad0a0;--warn:#ffb454;}
  *{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--txt);
    font-family:'Segoe UI','Malgun Gothic',system-ui,sans-serif;padding:20px;line-height:1.5}
  h1{font-size:18px;margin:0 0 4px} .mut{color:var(--mut);font-size:12px}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin:12px 0}
  .kpis{display:flex;flex-wrap:wrap;gap:10px;margin:10px 0}
  .kpi{background:var(--panel2);border:1px solid var(--line);border-radius:10px;padding:8px 12px;min-width:110px}
  .kpi .v{font-size:18px;font-weight:700} .kpi .l{font-size:11px;color:var(--mut)}
  .grp{margin:14px 0} .grp h3{font-size:13px;margin:0 0 6px;color:var(--mut);font-weight:600}
  .span{background:var(--panel2);border:1px solid var(--line);border-radius:10px;padding:10px 12px;margin:8px 0}
  .span .hd{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
  .tag{font-size:11px;font-weight:700;padding:2px 8px;border-radius:999px}
  .tag.orch{background:rgba(91,140,255,.18);color:var(--orch)}
  .tag.sub{background:rgba(58,208,160,.18);color:var(--sub)}
  .small{font-size:11.5px;color:var(--mut)}
  .barwrap{height:8px;background:#0e1018;border-radius:6px;overflow:hidden;margin:8px 0;display:flex}
  .bi{height:100%} .bi.in{background:var(--orch)} .bi.out{background:var(--sub)} .bi.cache{background:#4b5168}
  .tools{display:flex;flex-wrap:wrap;gap:4px;margin-top:8px}
  .tool{font-size:11px;background:#0e1018;border:1px solid var(--line);border-radius:6px;padding:2px 7px;white-space:nowrap}
  .tool b{color:var(--warn)}
  table{width:100%;border-collapse:collapse;font-size:13px} th,td{border:1px solid var(--line);padding:8px 10px;text-align:right}
  th:first-child,td:first-child{text-align:left} th{background:var(--panel2);color:var(--mut)}
  .num{font-variant-numeric:tabular-nums} a{color:var(--orch)}
"""


def trace_view_html(task_id: str) -> str:
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Trace {task_id}</title><style>{_BASE_CSS}</style></head><body>
<div id="app" class="mut">불러오는 중…</div>
<script>
const TID = {task_id!r};
const fmt = n => (n||0).toLocaleString();
function tokenBar(s){{
  const t=(s.input_tokens||0)+(s.output_tokens||0)+(s.cache_read_tokens||0)+(s.cache_creation_tokens||0)||1;
  const p=x=>((x||0)/t*100).toFixed(1)+'%';
  return `<div class="barwrap">
    <div class="bi in" style="width:${{p(s.input_tokens)}}"></div>
    <div class="bi out" style="width:${{p(s.output_tokens)}}"></div>
    <div class="bi cache" style="width:${{p((s.cache_read_tokens||0)+(s.cache_creation_tokens||0))}}"></div></div>`;
}}
function spanCard(s){{
  const cls = s.kind==='orchestrator'?'orch':'sub';
  const tools = (s.tools||[]).map(t=>`<span class="tool"><b>${{t.name.replace(/^mcp__[^_]+__/,'')}}</b> ${{(t.brief||'').replace(/</g,'&lt;')}}</span>`).join('');
  return `<div class="span"><div class="hd">
      <span class="tag ${{cls}}">${{s.kind}}</span>
      <b>${{s.role||''}}</b>
      <span class="small">${{s.model||''}} · turns ${{s.num_turns??'-'}} · ${{((s.duration_ms||0)/1000).toFixed(1)}}s · ${{s.status}}</span>
    </div>
    <div class="small">in ${{fmt(s.input_tokens)}} · out ${{fmt(s.output_tokens)}} · cache ${{fmt((s.cache_read_tokens||0)+(s.cache_creation_tokens||0))}} · 도구 ${{(s.tools||[]).length}}</div>
    ${{tokenBar(s)}}
    ${{tools?`<div class="tools">${{tools}}</div>`:''}}</div>`;
}}
fetch('/trace/'+TID).then(r=>r.json()).then(d=>{{
  if(d.error){{document.getElementById('app').textContent=d.error;return;}}
  const T=d.totals||{{}};
  const groups={{}};
  (d.spans||[]).forEach(s=>{{const k=s.session_id||'(세션 미상)';(groups[k]=groups[k]||[]).push(s);}});
  const kpi=(v,l)=>`<div class="kpi"><div class="v num">${{v}}</div><div class="l">${{l}}</div></div>`;
  let html=`<h1>Trace · <span class="num">${{TID}}</span></h1>
    <div class="mut">variant: <b>${{d.variant}}</b> · ${{(d.goal||'').slice(0,120)}}</div>
    <div class="kpis">
      ${{kpi(fmt(T.input_tokens),'input 토큰')}}${{kpi(fmt(T.output_tokens),'output 토큰')}}
      ${{kpi(fmt(T.cache_read_tokens),'cache read')}}${{kpi(T.tool_calls,'도구 호출')}}
      ${{kpi(T.subagents,'하위에이전트')}}${{kpi(T.sessions,'세션')}}${{kpi(T.num_turns,'턴 합계')}}
      ${{kpi(T.cost_usd==null?'—':('$'+T.cost_usd),'비용(USD)')}}</div>`;
  for(const [sess,spans] of Object.entries(groups)){{
    html+=`<div class="grp"><h3>● 세션 ${{sess.slice(0,18)}} <span class="small">(${{spans.length}} span)</span></h3>${{spans.map(spanCard).join('')}}</div>`;
  }}
  html+=`<p class="mut">범례: <span style="color:var(--orch)">■</span> input · <span style="color:var(--sub)">■</span> output · ■ cache · <span class="tag orch">orchestrator</span> <span class="tag sub">subagent</span></p>`;
  document.getElementById('app').innerHTML=html;
}});
</script></body></html>"""


def compare_view_html(ids_csv: str) -> str:
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>구조 비교</title><style>{_BASE_CSS}</style></head><body>
<div id="app" class="mut">불러오는 중…</div>
<script>
const IDS = {ids_csv!r};
const fmt = n => (n==null?'—':(typeof n==='number'?n.toLocaleString():n));
fetch('/compare?ids='+encodeURIComponent(IDS)).then(r=>r.json()).then(d=>{{
  const rows=d.rows||[];
  if(!rows.length){{document.getElementById('app').textContent='비교할 데이터가 없습니다.';return;}}
  const cols=[
    ['variant','구조'],['status','상태'],['overall','종합점수'],
    ['completion','완료'],['quality','품질'],['safety','안전'],['efficiency','효율'],
    ['input_tokens','in토큰'],['output_tokens','out토큰'],['tool_calls','도구'],
    ['subagents','하위'],['num_turns','턴'],['cost_usd','비용$']];
  let h=`<h1>구조 비교 (A/B)</h1><div class="mut">같은(혹은 다른) 목적을 구조별로 돌린 결과를 나란히 비교</div><div class="card"><table><tr>`;
  h+=`<th>task</th>`+cols.map(c=>`<th>${{c[1]}}</th>`).join('')+`</tr>`;
  rows.forEach(r=>{{
    h+=`<tr><td><a href="/trace/${{r.task_id}}/view">${{r.task_id.slice(0,8)}}</a></td>`+
       cols.map(c=>`<td class="num">${{fmt(r[c[0]])}}</td>`).join('')+`</tr>`;
  }});
  h+=`</table></div><p class="mut">종합점수=judge 4항목 평균 · 토큰/도구/턴은 trace 집계 · task 클릭 시 상세 trace</p>`;
  document.getElementById('app').innerHTML=h;
}});
</script></body></html>"""
