/* Oye 44 — one-example-at-a-time training review. No extra storage or controller. */
'use strict';
(() => {
 const $ = s=>document.querySelector(s);
 let state={report:null,session:'',filter:'pending',position:0,offset:0,imageUrl:'',loadId:0,busy:false};
 const set = (id,v) => {const el=$(id);if(el)el.textContent=String(v)};
 function current(){return state.report?.items?.[state.position]||null}
 function dispose(){if(state.imageUrl){URL.revokeObjectURL(state.imageUrl);state.imageUrl=''}}
 function buildSessions(){const select=$('#training44-session');if(!select)return;const before=select.value;select.replaceChildren();for(const name of state.report?.sessions||[]){select.add(new Option(name,name))}select.value=state.report?.session||before||''}
 function buildActions(row){const select=$('#training44-action');select.replaceChildren();const options=new Set(state.report?.available_actions||[]);if(row?.action_label&&!options.has(row.action_label))options.add(row.action_label);
  for(const name of [...options].sort()){select.add(new Option(name.replaceAll('_',' '),name))}select.value=row?.action_label||''}
 function render(){const r=state.report;if(!r)return;
  set('#training44-usable',r.usable);set('#training44-pending',r.pending);set('#training44-rejected',r.excluded);set('#training44-actions',Object.keys(r.actions||{}).length);
  set('#training44-policy',r.policy_ready?'Azioni presenti: suggerimenti possibili, efficacia da verificare':'Servono almeno 3 esempi utilizzabili e 2 azioni diverse');
  buildSessions();$('#training44-filter').value=state.filter;
  $('#training44-first').disabled=!state.offset;
  $('#training44-more').disabled=!r.has_more;
  set('#training44-page-label','Esempi '+(r.total ? state.offset+1:0)+'–'+(state.offset+(r.items?.length||0)));
  const row=current();state.loadId++;$('#training44-review').hidden=!row;$('#training44-empty').hidden=!!row;
  if(!row){set('#training44-empty',r.total?(state.filter==='pending'?'Nessun esempio incerto in questa registrazione. Passa a «Tutti» per controllare anche gli esempi già riconosciuti.':'Nessun esempio disponibile nel filtro.'):'Nessuna dimostrazione registrata. Premi «Registra le mie mosse».');dispose();return}
  set('#training44-index',`${state.position+1} / ${r.items.length}`);
  set('#training44-frame',`#${row.id} · ${row.action_label?.replaceAll('_',' ')||'sconosciuta'}`);
  set('#training44-scene',`Schermata: ${row.hud_scene} · Stato: ${row.status} · Controlli: ${(row.observed_controls||[]).join(' + ')||'nessuno'}`);
  buildActions(row);
  const blocked=row.status==='blocked';
  set('#training44-help',blocked?'Questa schermata è stata identificata come non giocabile: puoi scartarla, ma non confermarla come partita.':row.status==='excluded'?'Hai già escluso questa mossa. Puoi ripristinarla in qualsiasi momento.':row.status==='usable'?'Esempio utilizzabile. Puoi correggerne l’azione o escluderlo.':'Guarda lo screenshot: conferma solo se eri davvero in partita e controllavi il personaggio.');
  $('#training44-accept').disabled=blocked||!row.has_image;
  $('#training44-exclude').disabled=row.status==='excluded';
  $('#training44-reset').hidden=row.status!=='excluded';
  $('#training44-noimage').hidden=!!row.has_image;
  const img=$('#training44-image');img.hidden=true;dispose();if(!row.has_image)return;
  const id=state.loadId;
  api('learning/image?id='+encodeURIComponent(row.id),undefined,true).then(blob=>{if(id!==state.loadId)return;state.imageUrl=URL.createObjectURL(blob);img.src=state.imageUrl;img.hidden=false;$('#training44-noimage').hidden=true}).catch(()=>{if(id===state.loadId){img.hidden=true;$('#training44-noimage').hidden=false}});
 }
 async function load({session='',filter=state.filter,offset=0}={}){
  if(state.busy)return;
  state.busy=true;
  const button=$('#training44-refresh');button.disabled=true;
  try{const r=await api('learning/review?session='+encodeURIComponent(session)+'&filter='+encodeURIComponent(filter)+'&limit=12&offset='+offset);state.report=r;state.session=r.session;state.position=0;state.filter=filter;state.offset=offset;render()}
  catch(err){notify('Training: '+err.message,true)}
  finally{state.busy=false;button.disabled=false}
 }
 function focus(){const section=$('#training44');section?.scrollIntoView({behavior:'smooth',block:'start'})}
 async function decide(decision){const row=current();if(!row||state.busy)return;state.busy=true;
  try{
   const action=$('#training44-action').value;
   await api('learning/review',{session:state.session,id:row.id,decision, ...(decision==='accept'&&action!==row.action_label?{action}:{})});
   notify(decision==='accept'?'Mossa confermata: disponibile per la policy.':decision==='exclude'?'Esempio escluso dall’apprendimento.':'Esempio ripristinato.');
   state.busy=false;await load({session:state.session,filter:state.filter});
  }catch(err){notify(err.message,true)}finally{state.busy=false}
 }
 $('#training44-refresh')?.addEventListener('click',()=>load({session:state.session}));
 $('#training44-first')?.addEventListener('click',()=>load({session:state.session,offset:0}));
 $('#training44-more')?.addEventListener('click',()=>load({session:state.session,offset:state.offset+12}));
 $('#training44-session')?.addEventListener('change',ev=>load({session:ev.target.value}));
 $('#training44-filter')?.addEventListener('change',ev=>load({session:state.session,filter:ev.target.value}));
 $('#training44-accept')?.addEventListener('click',()=>decide('accept'));
 $('#training44-exclude')?.addEventListener('click',()=>decide('exclude'));
 $('#training44-reset')?.addEventListener('click',()=>decide('reset'));
 $('#training44-skip')?.addEventListener('click',()=>{if(!state.report?.items?.length)return;state.position=(state.position+1)%state.report.items.length;render()});
 window.OyeTraining44={load,open:async()=>{await load();focus()}};
 load();
})();
