'use strict';
/* Oye Studio 37. Read/write only the existing learning/observer.sqlite3. */
(() => {
  const $ = selector => document.querySelector(selector);
  const state = { data:null, session:'', frames:[], timeline:[], index:0, page:0,
                  filter:'all', inFrame:null, outFrame:null, timer:null,
                  blobs:new Map(), loading:false, tab:'' };
  const pageSize=16;
  const safeTime = value => {
    if(!value)return '—';
    const d=new Date(value);return Number.isFinite(d.getTime())?d.toLocaleTimeString('it-IT',{hour:'2-digit',minute:'2-digit',second:'2-digit'}):String(value).slice(11,19);
  };
  const prettyDate = value => {
    const d=new Date(value);return Number.isFinite(d.getTime())?d.toLocaleDateString('it-IT',{day:'2-digit',month:'short',year:'numeric'}):String(value||'').slice(0,10);
  };
  function labelOrAI(frame){return frame.user_label||frame.predicted_event||'non classificato'}
  function set(el,str){if(el)el.textContent=String(str)}
  function clear(el){el.replaceChildren()}
  function stopPlayback(){if(state.timer){clearInterval(state.timer);state.timer=null;set($('#timeline-play'),'▶ RIPRODUCI')}}
  function hasData(){return !!state.data?.sessions?.length}
  async function fetchData(session=''){
    if(state.loading)return;
    state.loading=true;
    try{
      const next=await api('learning?session='+encodeURIComponent(session)+'&limit=2000');
      state.data=next;state.session=next.selected||'';
      state.frames=next.frames||[];state.timeline=[...state.frames].reverse();
      populateSelectors();renderStats();renderArchive();
      $('#session-notes-input').value=next.note||'';
      if(state.tab==='timeline'){state.index=0;state.inFrame=null;state.outFrame=null;renderTimeline()}
    }catch(error){notify('Archivio: '+error.message,true)}
    finally{state.loading=false}
  }
  function populateSelectors(){
    for(const id of ['archive-session','timeline-session']){
      const select=$('#'+id);if(!select)return;
      clear(select);
      if(!hasData()){const option=new Option('Nessuna sessione registrata','');select.add(option)}
      else for(const session of state.data.sessions){
        const option=new Option(session,session);select.add(option)
      }
      select.value=state.session;
    }
    const actions=$('#inspect-action');clear(actions);
    actions.add(new Option('Nessuna nuova azione',''));
    for(const action of state.data?.actions||[])actions.add(new Option(action.replaceAll('_',' '),action));
  }
  function renderStats(){
    const stats=state.data?.stats||{};
    set($('#lib-frames'),stats.frames||0);set($('#lib-labeled'),stats.labeled||0);
    set($('#lib-actions'),stats.actions||0);set($('#lib-sessions'),state.data?.sessions?.length||0);
  }
  async function imageBlob(id){
    if(state.blobs.has(id))return state.blobs.get(id);
    const blob=await api('learning/image?id='+encodeURIComponent(id),undefined,true);
    const url=URL.createObjectURL(blob);state.blobs.set(id,url);
    if(state.blobs.size>90){
      const old=state.blobs.keys().next().value;
      if(old!==id){URL.revokeObjectURL(state.blobs.get(old));state.blobs.delete(old)}
    }
    return url;
  }
  function showImage(img,frame){
    if(!frame?.has_image){img.hidden=true;return}
    img.hidden=false;img.removeAttribute('src');
    imageBlob(frame.id).then(url=>{if(img.dataset.frame===String(frame.id))img.src=url})
      .catch(()=>{if(img.dataset.frame===String(frame.id)){img.hidden=true}});
  }
  function archiveFiltered(){
    return state.frames.filter(frame=>state.filter==='review'?!frame.user_label:
      state.filter==='labeled'?!!frame.user_label:
      state.filter==='actions'?!!frame.action_label:true);
  }
  function renderArchive(){
    const grid=$('#archive-grid');if(!grid)return;clear(grid);
    const all=archiveFiltered(), pages=Math.max(1,Math.ceil(all.length/pageSize));state.page=Math.min(state.page,pages-1);
    const view=all.slice(state.page*pageSize,(state.page+1)*pageSize);
    $('#archive-empty').hidden=hasData();
    for(const frame of view){
      const card=document.createElement('button');card.type='button';card.className='archive-frame';
      const image=document.createElement('div');image.className='archive-thumb';
      const img=document.createElement('img');img.alt='Frame '+frame.id;img.loading='lazy';img.dataset.frame=String(frame.id);
      image.append(img);
      const badge=document.createElement('span');badge.className='frame-badge '+(frame.user_label?'confirmed':'');badge.textContent=frame.user_label?'CORRETTO':'AI · STIMA';image.append(badge);
      const info=document.createElement('div');info.className='archive-frame-info';
      const title=document.createElement('strong');title.textContent=labelOrAI(frame);
      const secondary=document.createElement('span');secondary.textContent='#'+frame.id+' · '+safeTime(frame.captured_at)+(frame.action_label?' · '+frame.action_label:'');
      info.append(title,secondary);card.append(image,info);grid.append(card);
      showImage(img,frame);
      card.addEventListener('click',()=>{
        state.index=state.timeline.findIndex(row=>row.id===frame.id);
        nav('timeline');renderTimeline();
      });
    }
    set($('#archive-page'),all.length?`PAGINA ${state.page+1} / ${pages} · ${all.length} FRAME`:'NESSUN FRAME');
    $('#archive-prev').disabled=state.page===0;$('#archive-next').disabled=state.page>=pages-1;
  }
  function formatRelative(index){
    const frames=state.timeline;
    if(!frames.length)return '00:00';
    const first=Date.parse(frames[0].captured_at),at=Date.parse(frames[index]?.captured_at);
    if(!Number.isFinite(first)||!Number.isFinite(at))return safeTime(frames[index]?.captured_at);
    const seconds=Math.max(0,Math.floor((at-first)/1000));
    return `${String(Math.floor(seconds/60)).padStart(2,'0')}:${String(seconds%60).padStart(2,'0')}`;
  }
  function goTo(index){
    if(!state.timeline.length)return;
    state.index=Math.max(0,Math.min(state.timeline.length-1,index));renderTimeline();
  }
  function renderTimeline(){
    const frames=state.timeline;
    const can=!!frames.length;
    const frame=frames[state.index];
    $('#timeline-placeholder').hidden=can;
    const img=$('#timeline-image');img.hidden=!can;
    set($('#timeline-counter'),can?`${state.index+1} / ${frames.length} FOTOGRAMMI`:'NESSUN FOTOGRAMMA');
    const slider=$('#timeline-slider');slider.max=Math.max(0,frames.length-1);slider.value=state.index;slider.disabled=!can;
    set($('#timeline-at'),formatRelative(state.index));set($('#timeline-total'),formatRelative(frames.length-1));
    for(const id of ['timeline-prev','timeline-next','timeline-play','inspect-save','interval-save','timeline-set-in','timeline-set-out'])$('#'+id).disabled=!can;
    if(!can){clear($('#timeline-filmstrip'));set($('#inspect-id'),'Nessuna sessione');return}
    img.dataset.frame=String(frame.id);showImage(img,frame);
    set($('#inspect-id'),'FRAME #'+frame.id+' / '+prettyDate(frame.captured_at)+' / '+safeTime(frame.captured_at));
    set($('#inspect-prediction'),(frame.predicted_event||'Incerto')+' · confidenza '+Math.round((frame.predicted_score||0)*100)+'% · '+(frame.predicted_source||'non disponibile'));
    $('#inspect-event').value=frame.user_label||'';
    $('#inspect-note').value=frame.user_note||'';
    $('#inspect-action').value='';
    const strip=$('#timeline-filmstrip');clear(strip);
    const from=Math.max(0,state.index-8),to=Math.min(frames.length,state.index+9);
    for(let i=from;i<to;i++){
      const f=frames[i],button=document.createElement('button');button.type='button';
      button.className='film-thumb'+(i===state.index?' selected':'')+(f.user_label?' reviewed':'');
      button.setAttribute('aria-label','Vai al frame '+f.id);
      const im=document.createElement('img');im.dataset.frame=String(f.id);im.alt='';
      const time=document.createElement('span');time.textContent=formatRelative(i);
      button.append(im,time);strip.append(button);showImage(im,f);
      button.addEventListener('click',()=>goTo(i));
    }
    renderRange();renderIntervalHistory();renderTracks();
  }
  function renderTracks(){
    const root=$('#timeline-tracks');clear(root);
    const frames=state.timeline;
    if(!frames.length)return;
    const first=Date.parse(frames[0].captured_at),last=Date.parse(frames[frames.length-1].captured_at);
    const position=timestamp=>Math.min(99,Math.max(0,(Date.parse(timestamp)-first)*100/Math.max(1,last-first)));
    const lanes=[['EVENTI / CORREZIONI', frames.filter((x,i)=>i===0||x.user_label|| (i>0 && x.predicted_event!==frames[i-1].predicted_event)).slice(0,85), 'captured_at'],
                 ['INPUT / DIMOSTRAZIONI',(state.data?.transitions||[]).slice(-85),'observed_at']];
    for(const [caption,items,key] of lanes){
      const row=document.createElement('div');row.className='timeline-track-row';
      const name=document.createElement('span');name.className='timeline-track-name';name.textContent=caption;
      const lane=document.createElement('div');lane.className='timeline-track-lane';
      for(const item of items){
        const p=position(item[key]);if(!Number.isFinite(p))continue;
        const mark=document.createElement('button');mark.type='button';mark.className='track-mark'+(key==='observed_at'?' input-mark':'');
        mark.style.left=p+'%';mark.title=key==='captured_at'?(item.user_label||item.predicted_event):(item.actions||[]).join(' + ');
        mark.setAttribute('aria-label',mark.title||'Evento');
        mark.addEventListener('click',()=>{
          const target=key==='captured_at'?frames.findIndex(f=>f.id===item.id):frames.reduce((best,f,i)=>
              Math.abs(Date.parse(f.captured_at)-Date.parse(item[key]))<
              Math.abs(Date.parse(frames[best].captured_at)-Date.parse(item[key]))?i:best,0);
          if(target>=0)goTo(target);
        });lane.append(mark);
      }
      row.append(name,lane);root.append(row)
    }
  }
  function renderRange(){
    const a=state.inFrame,b=state.outFrame;
    const text=(a&&b)?`DA ${safeTime(a.captured_at)} A ${safeTime(b.captured_at)}`:
      a?`INIZIO: ${safeTime(a.captured_at)} · segna fine`:
      b?`FINE: ${safeTime(b.captured_at)} · segna inizio`:'Segna inizio e fine su due fotogrammi';
    set($('#timeline-range-summary'),text);
    const valid=a&&b&&Date.parse(a.captured_at)<Date.parse(b.captured_at);
    $('#interval-save').disabled=!valid;
  }
  function renderIntervalHistory(){
    const list=$('#interval-history');clear(list);
    const history=state.data?.intervals||[];
    if(!history.length){const p=document.createElement('p');p.className='subtle';p.textContent='Nessun intervallo salvato.';list.append(p);return}
    for(const item of history.slice(-7).reverse()){
      const row=document.createElement('div');row.className='interval-row';
      const txt=document.createElement('span');txt.textContent=item.event+' · '+safeTime(item.start_at)+'–'+safeTime(item.end_at);
      const del=document.createElement('button');del.type='button';del.textContent='Rimuovi';
      del.addEventListener('click',async()=>{if(!confirm('Rimuovere l’annotazione? Le correzioni esistenti restano salvate.'))return;
        try{await api('learning/edit',{operation:'delete_interval',id:item.id,session:state.session});await fetchData(state.session);notify('Annotazione rimossa.')}catch(e){notify(e.message,true)}});
      row.append(txt,del);list.append(row)
    }
  }
  function play(){
    if(state.timer){stopPlayback();return}
    if(!state.timeline.length)return;
    set($('#timeline-play'),'Ⅱ PAUSA');
    state.timer=setInterval(()=>{if(state.index>=state.timeline.length-1){stopPlayback();return}goTo(state.index+1)},950);
  }
  async function saveInspector(){
    const frame=state.timeline[state.index];if(!frame)return;
    const label=$('#inspect-event').value.trim(),note=$('#inspect-note').value.trim();
    const action=$('#inspect-action').value;
    if(!label&&!action){notify('Scrivi un evento oppure scegli un’azione.',true);return}
    try{
      if(label)await api('learning/edit',{operation:'event',id:frame.id,label,note});
      if(action)await api('learning/edit',{operation:'action',id:frame.id,action});
      await fetchData(state.session);
      const index=state.timeline.findIndex(row=>row.id===frame.id);goTo(index>=0?index:0);
      notify('Correzione salvata nel database SQLite.');
    }catch(error){notify(error.message,true)}
  }
  async function saveRange(){
    const a=state.inFrame,b=state.outFrame,event=$('#interval-event').value.trim();
    if(!a||!b||!event||Date.parse(a.captured_at)>=Date.parse(b.captured_at)){notify('Seleziona un intervallo valido e scrivi un evento.',true);return}
    try{await api('learning/edit',{operation:'interval',session:state.session,start:a.captured_at,end:b.captured_at,event});
      state.inFrame=null;state.outFrame=null;await fetchData(state.session);notify('Intervallo salvato e fotogrammi aggiornati.')}
    catch(error){notify(error.message,true)}
  }
  async function exportData(){
    try{
      const response=await fetch('/api/learning/export?session='+encodeURIComponent(state.session),
                                 {headers:{'X-Oye-Token':token},cache:'no-store'});
      if(!response.ok){const error=await response.json();throw Error(error.error||'Esportazione non riuscita')}
      const blob=await response.blob(),url=URL.createObjectURL(blob),a=document.createElement('a');
      a.href=url;a.download='Oye_'+(state.session||'tutti').replace(/[^a-zA-Z0-9_-]/g,'_').slice(0,70)+'.jsonl';
      document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),2000);
      notify('Metadati esportati. Gli screenshot originali restano al loro posto.');
    }catch(e){notify(e.message,true)}
  }
  function wire(){
    $('#archive-refresh').addEventListener('click',()=>fetchData(state.session));
    $('#archive-session').addEventListener('change',e=>{stopPlayback();state.page=0;fetchData(e.target.value)});
    $('#timeline-session').addEventListener('change',e=>{stopPlayback();state.index=0;fetchData(e.target.value)});
    $('#archive-filter').addEventListener('change',e=>{state.filter=e.target.value;state.page=0;renderArchive()});
    $('#archive-prev').addEventListener('click',()=>{state.page--;renderArchive()});
    $('#archive-next').addEventListener('click',()=>{state.page++;renderArchive()});
    $('#archive-export').addEventListener('click',exportData);$('#timeline-export').addEventListener('click',exportData);
    $('#timeline-prev').addEventListener('click',()=>goTo(state.index-1));
    $('#timeline-next').addEventListener('click',()=>goTo(state.index+1));
    $('#timeline-slider').addEventListener('input',e=>{stopPlayback();goTo(Number(e.target.value))});
    $('#timeline-play').addEventListener('click',play);
    $('#timeline-set-in').addEventListener('click',()=>{state.inFrame=state.timeline[state.index];renderRange()});
    $('#timeline-set-out').addEventListener('click',()=>{state.outFrame=state.timeline[state.index];renderRange()});
    $('#inspect-save').addEventListener('click',saveInspector);$('#interval-save').addEventListener('click',saveRange);
    $('#interval-confirm-demo').addEventListener('click',async()=>{
      const a=state.inFrame,b=state.outFrame;
      if(!a||!b){notify('Seleziona prima inizio e fine intervallo.',true);return}
      if(!confirm('Confermare le dimostrazioni con HUD incerto in questo intervallo?'))return;
      try{const result=await api('learning/edit',{operation:'confirm_demo',session:state.session,start:a.captured_at,end:b.captured_at});
        await fetchData(state.session);notify('Dimostrazioni confermate: '+result.confirmed)}catch(e){notify(e.message,true)}
    });
    $('#session-notes-save').addEventListener('click',async()=>{
      if(!state.session){notify('Seleziona una sessione.',true);return}
      try{await api('learning/edit',{operation:'note',session:state.session,note:$('#session-notes-input').value});notify('Note di sessione salvate.')}
      catch(e){notify(e.message,true)}
    });
    $('#test-forward-btn').addEventListener('click',()=>diagnose('forward'));
    $('#test-camera-btn').addEventListener('click',()=>diagnose('camera'));
    document.addEventListener('keydown',e=>{
      if(state.tab!=='timeline'||!$('#page-timeline').classList.contains('active'))return;
      if(['INPUT','TEXTAREA','SELECT'].includes(document.activeElement?.tagName))return;
      if(e.key==='ArrowLeft'){e.preventDefault();goTo(state.index-1)}
      if(e.key==='ArrowRight'){e.preventDefault();goTo(state.index+1)}
      if(e.code==='Space'){e.preventDefault();play()}
    });
    document.addEventListener('visibilitychange',()=>{if(document.hidden)stopPlayback()});
  }
  async function diagnose(kind){
    const text=kind==='forward'?'invierà brevemente W':'proverà a ruotare la telecamera';
    if(!confirm('Test manuale: Oye '+text+' dopo 3 secondi. Assicurati di tornare alla finestra di gioco. Continuare?'))return;
    try{const result=await api('input-test',{kind,confirmed:true});notify(result.message)}
    catch(e){notify(e.message,true)}
  }
  window.OyeStudio={load(tab){state.tab=tab;stopPlayback();if(!state.data)fetchData();else if(tab==='timeline')renderTimeline();else renderArchive();}};
  wire();
})();
