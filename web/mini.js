'use strict';
const token=new URLSearchParams(location.search).get('token')||'';
const $=s=>document.querySelector(s);
let running=false, paused=false, consentMode='quick_cycle', loading=false, lastEvents='';
async function api(name,data){const head={'X-Oye-Token':token},options={headers:head,cache:'no-store'};if(data!==undefined){options.method='POST';head['Content-Type']='application/json';options.body=JSON.stringify(data)}const response=await fetch('/api/'+name,options);const parsed=await response.json();if(!response.ok)throw Error(parsed.error||'Richiesta non riuscita');return parsed}
function toast(text){$('#toast').textContent=text;$('#toast').classList.add('visible');setTimeout(()=>$('#toast').classList.remove('visible'),3800)}
function studio(hash=''){const path='/studio?token='+encodeURIComponent(token)+(hash||'');const w=window.open(path,'_blank');if(!w)location.href=path}
function update(st){running=!!st.running;paused=!!st.paused;$('#pulse').classList.toggle('active',running&&!paused);$('#mode-state').textContent=paused?'SESSIONE IN PAUSA':running?'SESSIONE ATTIVA':'CONTROLLO IN ATTESA';$('#status').innerHTML=(paused?'In pausa':running?'Oye è attivo':'In attesa')+'<span>.</span>';
const scene=st.scene||'unknown';$('#phase').textContent=({match:'IN PARTITA',lobby:'IN LOBBY',results:'RISULTATI',spectator:'SPETTATORE',loading:'CARICAMENTO',unknown:'SCHERMATA NON NOTA'})[scene]||scene.toUpperCase();
$('#description').textContent=st.diagnostic?.attention||st.reason||'Nessuna sessione attiva';$('#games').textContent=st.matches_completed??0;
const detections=st.ocr_detections||{};
$('#ocr-discoveries').textContent=detections.session_total??0;
$('#ocr-discovery-total').textContent='Storico: '+(detections.all_time_total??0);
const latest=(detections.recent||[])[0];
$('#ocr-discovery-latest').textContent=latest?(latest.label||latest.target):'Nessuna rilevata';
$('#training-usable').textContent=(st.training?.trainable_actions??0)+' campioni utilizzabili';
$('#action').textContent=({wait:'Attende',jump:'Salta',menu_click:'Menu',patrol_forward:'Cammina',harvest:'Piccona'})[st.action]||st.action||'—';$('#ocr').textContent=st.ocr||'—';const pf=st.screen_profile?.selected;$('#elapsed').textContent=pf?pf.width+' × '+pf.height:'PROFILO NON CONFIGURATO';
$('#start').disabled=running;$('#pause').disabled=!running;$('#stop').disabled=!running;$('#pause').textContent=paused?'▶':'Ⅱ';
const events=(st.events||[]).slice(0,5);const key=JSON.stringify(events);if(key!==lastEvents){lastEvents=key;$('#events').replaceChildren();for(const row of events){const div=document.createElement('div');div.className='event';const time=document.createElement('time');time.textContent=row.at||'—';const message=document.createElement('span');message.textContent=row.message||row.event||'—';div.append(time,message);$('#events').append(div)}if(!events.length){const p=document.createElement('div');p.className='empty';p.textContent='Nessun evento disponibile.';$('#events').append(p)}}}
async function refresh(){if(loading)return;loading=true;try{update(await api('status'))}catch(e){$('#mode-state').textContent='CONNESSIONE NON DISPONIBILE';$('#description').textContent=e.message}finally{loading=false}}
$('#studio').onclick=()=>studio();$('#teach-actions').onclick=()=>studio('#training');$('#scanner-link').href='/scanners?token='+encodeURIComponent(token);$('#teach').onclick=()=>studio('#screen');$('#advanced').onclick=()=>studio();
$('#start').onclick=()=>{consentMode=$('#mode').value;if(['observe'].includes(consentMode)){api('start',{mode:consentMode,confirmed:false}).then(refresh).catch(e=>toast(e.message));return}$('#ack').checked=false;$('#proceed').disabled=true;$('#confirm').hidden=false};
$('#cancel').onclick=()=>$('#confirm').hidden=true;$('#ack').onchange=()=>$('#proceed').disabled=!$('#ack').checked;
$('#proceed').onclick=async()=>{$('#confirm').hidden=true;try{await api('start',{mode:consentMode,confirmed:true});toast('Sessione avviata. Torna a Fortnite.');await refresh()}catch(e){toast(e.message)}};
$('#pause').onclick=()=>api('pause',{}).then(refresh).catch(e=>toast(e.message));$('#stop').onclick=()=>api('stop',{}).then(refresh).catch(e=>toast(e.message));
refresh();setInterval(refresh,1350);
