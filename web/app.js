'use strict';
const q = (s) => document.querySelector(s);
const qa = (s) => [...document.querySelectorAll(s)];
const token = new URLSearchParams(location.search).get('token') || '';
window.addEventListener('DOMContentLoaded',()=>{
 const a=document.querySelector('#open-scanner');if(a)a.href='/scanners?token='+encodeURIComponent(token);
});
let current = null;
let lastFrame = 0;
let frameUrl = '';
let calUrl = '';
let calImageReady = false;
let lastCalibrationSerial = 0;
let selection = null;
let dragStart = null;
let pendingMode = 'auto_cycle';
let busy = false;
let toastTimer = 0;
const modeDescriptions = {
  quick_cycle:'Oye preme Gioca in basso a sinistra, attende Annulla e si muove quando la ricerca è terminata. Nei risultati prova Gioca di nuovo, poi Ritorna alla lobby. F8 pausa, F9 stop.',
  auto_cycle:'Gioca → si muove → riscatta le ricompense → torna alla lobby → ricomincia. Fino a STOP.',
  single_match:'Esegue una sola partita, riscatta le ricompense e si ferma alla lobby.',
  observe:'Guarda Fortnite senza mai premere un tasto.',
  train:'Modalità facile: registra mentre giochi, poi rivedi gli esempi uno alla volta prima di autorizzarli per l’apprendimento.',
  guided:'Esplora con il movimento e il piccone, quando il gioco è riconosciuto.'
};
const prettyAction = {
  wait:'In osservazione',menu_click:'Seleziona un pulsante',jump:'Salto',
  patrol_forward:'Cammina avanti',patrol_look_left:'Avanza e guarda a sinistra',
  patrol_look_right:'Avanza e guarda a destra',patrol_weave_left:'Avanza verso sinistra',
  patrol_weave_right:'Avanza verso destra',patrol_harvest:'Usa il piccone',
  patrol_hop:'Supera un ostacolo',forward:'Avanza',reverse:'Arretra',
  strafe_left:'Si sposta a sinistra',strafe_right:'Si sposta a destra',
  turn_left:'Gira la telecamera',turn_right:'Gira la telecamera',
  forward_harvest:'Avanza e raccoglie',harvest:'Usa il piccone',
  equip_pickaxe:'Equipaggia il piccone',shoot:'Clic sinistro',aim_hold:'Clic destro',secondary_click:'Clic destro breve',
  forward_shoot:'Avanza e azione',forward_jump:'Avanza e salta'
};
const friendlyScene={match:'In partita',spectator:'Spettatore',lobby:'Lobby',results:'Risultati',loading:'Caricamento',unknown:'Non determinata',unfocused:'In attesa della finestra di gioco'};
function notify(message,isError=false){
  const el=q('#toast');el.textContent=message;el.classList.toggle('error',isError);
  el.classList.add('visible');clearTimeout(toastTimer);toastTimer=setTimeout(()=>el.classList.remove('visible'),4100);
}
async function api(path, body, raw=false){
  const headers={'X-Oye-Token':token};let options={headers,cache:'no-store'};
  if(body!==undefined){
    options.method='POST';
    if(body instanceof Blob || body instanceof ArrayBuffer){options.body=body;headers['Content-Type']='application/octet-stream';}
    else{headers['Content-Type']='application/json';options.body=JSON.stringify(body);}
  }
  const res=await fetch('/api/'+path,options);
  if(raw){if(!res.ok)throw new Error('Immagine non disponibile');return res.blob();}
  const data=await res.json();if(!res.ok)throw new Error(data.error || 'Operazione non riuscita');
  return data;
}
function nav(name){
  qa('.page').forEach(el=>el.classList.toggle('active',el.id==='page-'+name));
  qa('.nav-item').forEach(el=>el.classList.toggle('selected',el.dataset.tab===name));
  const map={screen:'SCHERMATE E OCR',home:'PANORAMICA',calibrate:'INSEGNA A OYE',archive:'LE MIE PARTITE',timeline:'TIMELINE EDITOR',settings:'IMPOSTAZIONI'};
  q('#breadcrumb').textContent=map[name]||'PANORAMICA';
  if (name==='archive'||name==='timeline')window.OyeStudio?.load(name);
  if (name==='screen')window.OyeScreen?.open();
}
function setText(el,text){if(el.textContent!==String(text))el.textContent=String(text)}
function formatOCR(v){const map={normal:'Nessun avviso',unknown:'Da determinare',spectating:'Spettatore',spectator:'Spettatore',reboot_wait:'Attesa riavvio',waiting_reboot:'Attesa riavvio'};return map[v]||v||'Da determinare'}
function updateActions(action){
  const active = new Set();const a=String(action||'');
  if(a.includes('forward')||a.startsWith('patrol_')||a.startsWith('strafe_'))active.add('w');
  if(a.includes('reverse'))active.add('s');
  if(a.includes('left')&&a!=='turn_left'&&a!=='patrol_look_left')active.add('a');
  if(a.includes('right')&&a!=='turn_right'&&a!=='patrol_look_right')active.add('d');
  qa('.key-cap[data-key]').forEach(el=>el.classList.toggle('active',active.has(el.dataset.key)));
  q('#mouse-left').classList.toggle('active',a.includes('harvest')||a.includes('shoot')||a==='menu_click');
  q('#mouse-right').classList.toggle('active',a==='aim_hold'||a==='secondary_click');
}
function redrawEvents(items){
  const list=q('#event-list');list.replaceChildren();
  if(!items.length){const div=document.createElement('div');div.className='event-empty';div.textContent='Gli eventi della partita compariranno qui.';list.append(div);return;}
  for(const row of items){
    const div=document.createElement('div');div.className='event-row';
    const t=document.createElement('time');t.textContent=row.at||'—';
    const kind=document.createElement('strong');kind.textContent=(row.event||'sistema').replaceAll('_',' ');
    const msg=document.createElement('span');msg.textContent=row.message||'—';
    div.append(t,kind,msg);list.append(div);
  }
}
async function updateFrame(serial){
  if(!serial || serial===lastFrame)return;
  lastFrame=serial;
  try{
    const blob=await api('frame',undefined,true);
    const url=URL.createObjectURL(blob);
    const img=q('#live-frame');img.src=url;img.hidden=false;
    q('#preview-placeholder').hidden=true;
    if(frameUrl) URL.revokeObjectURL(frameUrl);frameUrl=url;
  }catch{
    lastFrame=0;
  }
}
function calibrationLabels(ready){
  const core=['hud_salute','hud_inventario','hud_minimappa'];
  const shownHUD=ready.hud_legacy?['hud_in_partita']:
    [...new Set([...core.filter(x=>ready.present.includes(x)),...core])].slice(0,2);
  const steps=[...new Set([...['gioca','riscatta','continua','torna_lobby','gioca_di_nuovo','spettatore_prossimo','spettatore_precedente'],...shownHUD,...ready.present])];
  q('#cal-progress').textContent=ready.ready?'PRONTO PER UNA PARTITA':(ready.issues?.length?'METADATI DA CORREGGERE': 'MANCANO '+ready.missing.length+' RIFERIMENTI');
  q('#cal-total').textContent=ready.present.length+' RIFERIMENTI SALVATI';
  q('#cal-list').replaceChildren();q('#template-select').replaceChildren();
  for(const name of steps){
    const item=document.createElement('div');item.className='cal-check'+(ready.present.includes(name)?' complete':'');
    const dot=document.createElement('span');dot.className='check-dot';dot.textContent=ready.present.includes(name)?'✓':'·';
    const text=document.createElement('span');text.textContent=(ready.labels[name]||name.replaceAll('_',' ')) + (ready.invalid?.[name]?' · '+ready.invalid[name]:'');
    const button=document.createElement('button');button.textContent=ready.present.includes(name)?'PRONTO':(ready.invalid?.[name]?'RIMOSTRA':'MOSTRAMI');
    button.onclick=()=>{q('#template-select').value=name;if(!ready.present.includes(name))openTeaching(name);};
    item.append(dot,text,button);q('#cal-list').append(item);
  }
  for(const issue of ready.issues||[]){
    const row=document.createElement('div');row.className='cal-check';
    const text=document.createElement('span');text.textContent='ATTENZIONE · '+issue;
    row.append(text);q('#cal-list').append(row);
  }
  for(const name of ready.templates||steps){
    const option=document.createElement('option');option.value=name;option.textContent=ready.labels[name]||name.replaceAll('_',' ');q('#template-select').append(option);
  }
}
async function openTeaching(target=null){
  nav('calibrate');
  if(target && ['gioca','riscatta','continua','torna_lobby','gioca_di_nuovo'].includes(target))q('#teach-text-target').value=target;
  try{await act('teach/start',target?{target}:{});}catch(err){console.warn('Insegnamento:',err.message)}
}
function renderTeaching(teach,ready,running,busyCapturing){
  const missing=ready.missing||[];
  const active=!!teach?.active;
  const done=!!ready.ready && !missing.length && !(active && teach?.target);
  const hasIssues=!!ready.issues?.length;
  const stage=teach?.stage||'idle';
  const target=teach?.target||missing[0];
  if(target && target!==lastTeachingTarget){
    if(['gioca','riscatta','continua','torna_lobby','gioca_di_nuovo'].includes(target))q('#teach-text-target').value=target;
    q('#teach-text-value').value='';
    lastTeachingTarget=target;
  }
  const title=active?teach.title:(hasIssues?teach.title:(done?'Ora so cosa cercare.':'Mi manca '+(ready.labels?.[target]||target||'un riferimento')));
  const instruction=active?teach.instruction:(hasIssues?teach.instruction:(done?'Tutti i riferimenti richiesti sono salvati. Puoi iniziare una partita.':'Te lo chiederò uno alla volta. Non devi modificare file o coordinate.'));
  const message=teach?.message|| (done?'Premi «Avvia una partita» dalla Panoramica.':'Mostrami lo schermo e io provo a imparare.');
  setText(q('#teach-heading'),title);
  setText(q('#teach-instruction'),instruction);
  setText(q('#teach-message'),message);
  setText(q('#teach-state'),stage==='capturing'?'STO CATTURANDO LO SCHERMO':stage==='analysing'?'STO LEGGENDO LA SCHERMATA':stage==='needs_point'?'MI SERVE UN CLIC':'INSEGNAMENTO GUIDATO');
  setText(q('#teach-count'),(done&&!active)?'TUTTO PRONTO':(hasIssues&&!missing.length?'METADATI DA CORREGGERE':(!missing.length?'RIFERIMENTO AGGIUNTIVO':missing.length+' DA MOSTRARE')));
  const btn=q('#teach-capture-btn');
  btn.disabled=running||busyCapturing||stage==='analysing'||(done&&!active)||(hasIssues&&!missing.length&&!active);
  btn.firstChild.textContent=(stage==='capturing'?'CATTURA IN CORSO ':stage==='analysing'?'STO CERCANDO ':stage==='needs_point'?'RIPROVA CATTURA ':active?'FAMMELO VEDERE ':'INIZIA A INSEGNARMI ');
  q('#teach-upload-btn').hidden=running||busyCapturing||stage==='analysing'||(done&&!active)||(hasIssues&&!missing.length&&!active);
  q('#teach-later-btn').hidden=!active||busyCapturing;
  q('#teach-home-btn').hidden=!done;
  q('#teach-manual-btn').hidden=!active;
  q('#teach-preview').hidden=!(active&&stage==='needs_point');
  setText(q('#teach-preview-help'),'Tocca il centro di '+(ready.labels?.[target]||target||'quello che manca'));
}
let prevReadiness='';
let prevEvents='';
let lastTeachingTarget='';
function applyStatus(status){
  const old=current;current=status;
  const currentState=status.state||'idle';const running=!!status.running;
  q('#header-status').className='status-chip '+currentState;
  setText(q('#status-text'),currentState==='running'?'SESSIONE IN CORSO':currentState==='paused'?'IN PAUSA':(q('#mode-select').value==='quick_cycle'?status.readiness?.quick_ready:status.readiness?.ready)?'PRONTO':'DA INSEGNARE');
  setText(q('#view-phase'),status.mode==='quick_cycle'||status.scene==='unknown'?status.phase:(friendlyScene[status.scene]||status.scene||'Nessuna sessione').toUpperCase());
  setText(q('#ocr-phase'),'OCR: '+formatOCR(status.ocr));
  setText(q('#hud-reading'),status.hud||'Da verificare');
  setText(q('#frame-label'),status.frame_count?'FRAME '+status.frame_count:'NESSUN SEGNALE');
  setText(q('#current-action'),running?(prettyAction[status.action]||status.action||'In osservazione'):'Nessuna azione');
  setText(q('#action-reason'),running?[status.reason,status.last_signal].filter(Boolean).join(' · '):'La sessione non è ancora stata avviata.');
  setText(q('#input-message'),status.input_status);
  setText(q('#mini-phase'),status.phase||'—');
  setText(q('#matches-completed'),status.matches_completed??0);
  const tr=status.training||{};
  setText(q('#training-count'),tr.database?`${tr.frames||0} fotogrammi · ${tr.labeled_frames||0} corretti`:'Nessun archivio trovato');
  setText(q('#training-state'),tr.status==='ok'?`Screenshot: ${tr.screenshots_on_disk||0} · azioni addestrabili: ${tr.trainable_actions||0}. I dati esistenti sono preservati.`:tr.status==='not_found'?'Nessun training precedente in questa cartella. Oye puo iniziare a imparare.':'Archivio da verificare: non sovrascriverlo.');
  setText(q('#mini-camera'),status.camera||'Da verificare');
  const diag = status.diagnostic || {};
  q('#diagnostic-panel').hidden = !diag.attention;
  setText(q('#diagnostic-title'),diag.attention||'');
  setText(q('#diagnostic-next'),diag.next_step||'Controlla il registro eventi.');
  q('#diagnostic-help-btn').hidden = !(/calibra|riferiment|OCR|Tesseract|schermat|HUD|pulsant|mostrami/i.test((diag.attention||'')+' '+(diag.next_step||'')));
  q('#start-btn').disabled=running;
  q('#pause-btn').disabled=!running;
  q('#stop-btn').disabled=!running;
  q('#pause-btn').querySelector('span').textContent=status.paused?'RIPRENDI':'PAUSA';
  q('#mode-select').disabled=running;
  q('#easy-train-start').disabled=running;
  q('#easy-train-stop').disabled=!(running&&status.mode==='train');
  q('#easy-train-confirm').disabled=running;
  setText(q('#easy-train-status'),running&&status.mode==='train'?
    'Sto registrando le tue mosse mentre giochi. Premi TERMINA quando hai finito.':
    'Puoi registrare un esempio giocando tu. Poi premi CONFERMA per usare le mosse nel training.');
  q('#save-settings-btn').disabled=running;
  q('#capture-btn').disabled=running||status.calibration.busy;
  q('#save-template-btn').disabled=running||!selection;
  const ready=status.readiness||{ready:false,missing:[],present:[],labels:{}};
  const quick=q('#mode-select').value==='quick_cycle';
  const modeReady=quick?!!ready.quick_ready:!!ready.ready;
  const readinessText=modeReady?'PRONTO PER UNA PARTITA':quick?'SERVE OCR TESSERACT':'MI MANCA '+(ready.labels?.[ready.missing[0]]||'UN RIFERIMENTO');
  q('#readiness-line').classList.toggle('missing',!modeReady);
  q('#show-me-home').hidden=false;
  q('#show-me-home').disabled=running;
  q('#readiness-line').lastElementChild.textContent=readinessText;
  q('#readiness-line').onclick=()=>{if(!modeReady&&!quick)openTeaching('gioca')};
  renderTeaching(status.teach,ready,running,status.calibration.busy);
  q('#cal-status').textContent=status.calibration.error||status.calibration.message;
  q('#cal-status').style.color=status.calibration.error?'#ff837e':'';
  if(JSON.stringify(ready)!==prevReadiness){
    const selected=q('#template-select').value;
    calibrationLabels(ready);if([...q('#template-select').options].some(op=>op.value===selected))q('#template-select').value=selected;
    prevReadiness=JSON.stringify(ready);
  }
  const eventKey=JSON.stringify(status.events);
  if(eventKey!==prevEvents){redrawEvents(status.events||[]);prevEvents=eventKey;}
  updateActions(running&&!status.paused?status.action:'wait');
  if(!old || old.settings!==status.settings){
    if(!running && document.activeElement?.tagName!=='INPUT' && document.activeElement?.tagName!=='SELECT')syncSettings(status.settings);
  }
  updateFrame(status.frame_serial);
  if(status.calibration.image_ready && status.calibration.image_serial!==lastCalibrationSerial){
    lastCalibrationSerial=status.calibration.image_serial;loadCalibrationImage();
  }
  if(!status.calibration.image_ready)calImageReady=false;
}
function syncSettings(settings){
  if(!settings)return;
  q('#profile-setting').value=settings.profile;
  q('#jump-setting').value=settings.jump_seconds;
  q('#interval-setting').value=settings.jump_interval;
  q('#harvest-setting').checked=settings.harvest;
  q('#ocr-setting').checked=settings.ocr_enabled;
  q('#pickaxe-setting').value=settings.keys.pickaxe;
  setText(q('#bind-pickaxe'),String(settings.keys.pickaxe).toUpperCase());
  q('#camera-setting').value=settings.camera_pixels;
  q('#learning-setting').checked=settings.use_learning;
  q('#smooth-setting').checked=settings.smooth;
  q('#recovery-setting').value=settings.recovery_limit;
  q('#motion-setting').value=settings.motion_threshold;
  q('#fall-setting').value=settings.min_fall;
  q('#ollama-setting').checked=settings.ollama_enabled;
  q('#ollama-model-setting').value=settings.ollama_model;

  for(const key of ['forward','back','left','right','jump']){
    setText(q('#bind-'+key),key==='jump'?'SPAZIO':String(settings.keys[key]).toUpperCase());
  }
}
async function refresh(){if(busy)return;busy=true;try{
  const status=await api('status');applyStatus(status);q('#connection').classList.remove('offline');
}catch(err){q('#connection').classList.add('offline');console.warn('Oye offline:',err)}finally{busy=false}}
async function act(path,data={}){try{const ret=await api(path,data);await refresh();return ret}catch(err){notify(err.message,true);throw err}}
async function launch(mode,confirmed){
  try{await act('start',{mode,confirmed});notify('Sessione avviata. Torna a Fortnite per permettere il riconoscimento.');return true;}
  catch(err){if(err.message.includes('calibraz')||err.message.includes('GIOCA'))nav('calibrate');return false}
}
function showConfirmation(){q('#consent-checkbox').checked=false;q('#confirm-start').disabled=true;q('#confirm-overlay').hidden=false}
function dismissConfirmation(){q('#confirm-overlay').hidden=true}
async function loadCalibrationImage(){
  try{
    const blob=await api('calibration/frame',undefined,true);const url=URL.createObjectURL(blob);
    if(calUrl)URL.revokeObjectURL(calUrl);calUrl=url;
    const img=q('#cal-image');img.src=url;img.hidden=false;q('#cal-empty').hidden=true;
    q('#teach-image').src=url;
    calImageReady=true;selection=null;q('#selection-box').hidden=true;
  }catch(err){console.warn(err)}
}
function selectionPoint(ev){const img=q('#cal-image');const r=img.getBoundingClientRect();return {
  x:Math.max(0,Math.min(1,(ev.clientX-r.left)/r.width)),
  y:Math.max(0,Math.min(1,(ev.clientY-r.top)/r.height))
}}
function drawSelection(){const box=q('#selection-box');if(!selection){box.hidden=true;return}
  box.hidden=false;box.style.left=(selection[0]*100)+'%';box.style.top=(selection[1]*100)+'%';
  box.style.width=((selection[2]-selection[0])*100)+'%';box.style.height=((selection[3]-selection[1])*100)+'%';
  q('#save-template-btn').disabled=!(selection[2]-selection[0]>.018 && selection[3]-selection[1]>.025);
}
function setupCalibrationDrag(){
  const wrap=q('#cal-image-wrap');
  wrap.addEventListener('pointerdown',(ev)=>{
    if(!calImageReady || current?.running)return;
    if(ev.button!==0)return;
    dragStart=selectionPoint(ev);selection=null;wrap.setPointerCapture(ev.pointerId);ev.preventDefault();
  });
  wrap.addEventListener('pointermove',(ev)=>{
    if(!dragStart)return;
    const to=selectionPoint(ev);
    selection=[Math.min(dragStart.x,to.x),Math.min(dragStart.y,to.y),Math.max(dragStart.x,to.x),Math.max(dragStart.y,to.y)];
    drawSelection();
  });
  const finish=()=>{dragStart=null};wrap.addEventListener('pointerup',finish);wrap.addEventListener('pointercancel',finish);
}
function register(){
  qa('[data-tab]').forEach(btn=>btn.addEventListener('click',()=>{if(btn.dataset.tab==='calibrate'&&current?.readiness?.missing?.length&&!current?.teach?.active)openTeaching();else nav(btn.dataset.tab)}));
  q('#mode-select').addEventListener('change',(e)=>{setText(q('#mode-description'),modeDescriptions[e.target.value]);
    q('#start-btn').firstChild.textContent=({'quick_cycle':'AVVIA IGNORANTE ', 'auto_cycle':'AVVIA IL CICLO ', 'single_match':'AVVIA UNA PARTITA ','observe':'INIZIA OSSERVAZIONE ',train:'REGISTRA UNA PARTITA ',guided:'AVVIA ESPLORAZIONE '})[e.target.value]||'AVVIA ';});
  q('#start-btn').addEventListener('click',()=>{
    pendingMode=q('#mode-select').value;
    if(pendingMode==='quick_cycle' && current && !current.readiness.quick_ready){notify('Attiva OCR nelle impostazioni e verifica Tesseract.',true);return}
    if(['auto_cycle','single_match'].includes(pendingMode) && current && !current.readiness.ready){openTeaching();return}
    if(pendingMode==='observe'||pendingMode==='train'){launch(pendingMode,false);return}
    showConfirmation();
  });
  q('#easy-train-home').addEventListener('click',()=>{nav('calibrate');q('#easy-train-start').focus()});
  q('#easy-train-start').addEventListener('click',async()=>{
    try{if(await launch('train',false)){notify('Registrazione avviata: torna a Fortnite e gioca tu.');nav('calibrate')}}
    catch(e){notify(e.message,true)}
  });
  q('#easy-train-stop').addEventListener('click',async()=>{
    try{await act('stop');notify('Registrazione fermata. Rivedi gli esempi prima di approvarli.');window.OyeTraining44?.open()}catch(e){notify(e.message,true)}
  });
  q('#easy-train-confirm').addEventListener('click',async()=>{
    window.OyeTraining44?.open();
  });
  q('#show-me-home').addEventListener('click',()=>openTeaching(current?.readiness?.quick_ready?'torna_lobby':'gioca'));
  qa('[data-menu-teach]').forEach(btn=>btn.addEventListener('click',()=>openTeaching(btn.dataset.menuTeach)));
  q('#teach-save-text-btn').addEventListener('click',async()=>{
    const name=q('#teach-text-target').value, label=q('#teach-text-value').value.trim();
    if(!label){notify('Scrivi prima il testo del pulsante',true);return}
    try{await act('teach/text',{name,label});notify('Testo salvato: Oye lo cercherà nella posizione insegnata.')}catch{}
  });
  q('#diagnostic-help-btn').addEventListener('click',()=>openTeaching(/pulsante finale/i.test(current?.diagnostic?.attention||'')?'torna_lobby':null));
q('#teach-capture-btn').addEventListener('click',async()=>{
    if(current?.readiness?.ready && !current?.teach?.active){nav('home');return}
    if(!current?.teach?.active){await openTeaching();return}
    try{await act('teach/capture');notify('Passa a Fortnite e mostra l’elemento entro 5 secondi.')}catch{}
  });
  q('#teach-later-btn').addEventListener('click',async()=>{try{await act('teach/later');nav('home')}catch{}});
  q('#teach-home-btn').addEventListener('click',()=>nav('home'));
  q('#teach-manual-btn').addEventListener('click',()=>{
    const panel=q('#advanced-calibration');panel.open=true;
    if(current?.teach?.target)q('#template-select').value=current.teach.target;
    panel.scrollIntoView({behavior:'smooth',block:'start'});
  });
  q('#teach-image-wrap').addEventListener('click',async(ev)=>{
    if(current?.teach?.stage!=='needs_point')return;
    const rect=q('#teach-image').getBoundingClientRect();
    if(ev.clientX<rect.left||ev.clientX>rect.right||ev.clientY<rect.top||ev.clientY>rect.bottom)return;
    const x=Math.max(0,Math.min(1,(ev.clientX-rect.left)/rect.width));
    const y=Math.max(0,Math.min(1,(ev.clientY-rect.top)/rect.height));
    try{await act('teach/pick',{point:[x,y]});notify('Perfetto. Ho salvato il riferimento.')}catch{}
  });
  q('#consent-checkbox').addEventListener('change',ev=>q('#confirm-start').disabled=!ev.target.checked);
  q('#cancel-confirm').addEventListener('click',dismissConfirmation);
  q('#confirm-start').addEventListener('click',async()=>{dismissConfirmation();await launch(pendingMode,true)});
  q('#pause-btn').addEventListener('click',()=>act('pause').catch(()=>{}));
  q('#stop-btn').addEventListener('click',()=>act('stop').catch(()=>{}));
  q('#confirm-overlay').addEventListener('click',ev=>{if(ev.target===q('#confirm-overlay'))dismissConfirmation()});
  document.addEventListener('keydown',ev=>{if(ev.key==='Escape')dismissConfirmation()});
  q('#open-editor-btn').addEventListener('click',()=>nav('timeline'));
  q('#capture-btn').addEventListener('click',async()=>{try{await act('calibration/capture');notify('Passa a Fortnite entro 5 secondi.')}catch{}});
  q('#upload-input').addEventListener('change',async ev=>{
    const file=ev.target.files?.[0];if(!file)return;
    if(!['image/png','image/jpeg'].includes(file.type)){notify('Scegli un PNG o JPEG',true);return;}
    try{await act('calibration/upload',file);calImageReady=false;await loadCalibrationImage();notify('Immagine caricata. Seleziona il riferimento.')}catch{}
    ev.target.value='';
  });
  q('#save-template-btn').addEventListener('click',async()=>{
    try{await act('calibration/save',{name:q('#template-select').value,rect:selection});notify('Riferimento salvato. Puoi selezionarne un altro.');selection=null;drawSelection()}catch{}
  });
  q('#save-settings-btn').addEventListener('click',async()=>{
    const config={profile:q('#profile-setting').value,jump_seconds:Number(q('#jump-setting').value),
      jump_interval:Number(q('#interval-setting').value),camera_pixels:Number(q('#camera-setting').value),
      harvest:q('#harvest-setting').checked,ocr_enabled:q('#ocr-setting').checked,
      pickaxe_key:q('#pickaxe-setting').value,
      use_learning:q('#learning-setting').checked,smooth:q('#smooth-setting').checked,
      recovery_limit:Number(q('#recovery-setting').value),motion_threshold:Number(q('#motion-setting').value),
      min_fall:Number(q('#fall-setting').value),ollama_enabled:q('#ollama-setting').checked,
      ollama_model:q('#ollama-model-setting').value.trim()};
    try{await act('settings',config);notify('Impostazioni salvate.')}catch{}
  });
  setupCalibrationDrag();
}
register();refresh();setInterval(refresh,1100);
if(location.hash==='#timeline')setTimeout(()=>nav('timeline'),100);
if(location.hash==='#screen')setTimeout(()=>nav('screen'),100);
if(location.hash==='#training')setTimeout(()=>{nav('calibrate');window.OyeTraining44?.open()},150);
