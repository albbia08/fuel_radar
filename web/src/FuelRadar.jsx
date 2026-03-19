import { useState, useEffect, useRef } from 'react'

// ═══════════════════════════════════════════════════════════════════
//  FUEL RADAR — App web standalone (Vercel)
//  Senza restrizioni CSP: fetch libera verso jsDelivr e Nominatim
//  Dati MIMIT aggiornati ogni mattina da GitHub Actions
// ═══════════════════════════════════════════════════════════════════

const VENETO_URL = 'https://cdn.jsdelivr.net/gh/albbia08/fuel_radar@gh-pages/fuel-data-veneto.json'

// ── Haversine ─────────────────────────────────────────────────────
function haversine(la1, lo1, la2, lo2) {
  const R = 6371, dL = (la2-la1)*Math.PI/180, dO = (lo2-lo1)*Math.PI/180
  const a = Math.sin(dL/2)**2 + Math.cos(la1*Math.PI/180)*Math.cos(la2*Math.PI/180)*Math.sin(dO/2)**2
  return +(R*2*Math.asin(Math.sqrt(a))).toFixed(2)
}

// ── Geocoding Nominatim ───────────────────────────────────────────
async function geocodeComune(comune) {
  const q = encodeURIComponent(`${comune}, Italy`)
  const r = await fetch(
    `https://nominatim.openstreetmap.org/search?q=${q}&format=json&limit=1&countrycodes=it`,
    { headers: { 'Accept-Language': 'it', 'User-Agent': 'FuelRadar/3.0 (github.com/albbia08/fuel_radar)' } }
  )
  const d = await r.json()
  if (!d?.length) throw new Error(`Comune "${comune}" non trovato`)
  return {
    lat: parseFloat(d[0].lat),
    lon: parseFloat(d[0].lon),
    nome: d[0].display_name.split(',')[0].trim()
  }
}

// ── GPS browser ───────────────────────────────────────────────────
function getGPS() {
  return new Promise((res, rej) => {
    if (!navigator.geolocation) return rej(new Error('GPS non supportato'))
    navigator.geolocation.getCurrentPosition(
      p => res({ lat: p.coords.latitude, lon: p.coords.longitude, nome: 'Posizione attuale' }),
      e => rej(new Error(e.code === 1 ? 'GPS negato' : 'GPS timeout')),
      { timeout: 8000, maximumAge: 60000 }
    )
  })
}

// ── Fetch dati Veneto ─────────────────────────────────────────────
async function fetchVeneto() {
  // Cache-bust ogni ora
  const r = await fetch(VENETO_URL + '?t=' + Math.floor(Date.now() / 3600000))
  if (!r.ok) throw new Error(`HTTP ${r.status} dal CDN`)
  return r.json()
}

// ── Colors ────────────────────────────────────────────────────────
const BAND = {
  'Agip Eni':'#FFD700','Esso':'#E31E24','Q8':'#003DA5','Api-Ip':'#FF6600',
  'Tamoil':'#E8000D','Enercoop':'#008000','Pompe Bianche':'#cbd5e1',
  'Shell':'#FF0000','Vega':'#1a56db','Energyca':'#059669',
  'San Marco Petroli':'#7c3aed','Costantin':'#0891b2','Oil Italia':'#6366f1'
}
const bandClr = b => { for(const [k,v] of Object.entries(BAND)) if(b?.includes(k)) return v; return '#94a3b8' }
const pClr = (p,mn,mx) => {
  if(!p) return '#475569'
  const t = (p-mn)/Math.max(mx-mn,.001)
  return t<.2?'#10b981':t<.5?'#f59e0b':t<.8?'#f97316':'#ef4444'
}

// ── Bar component ─────────────────────────────────────────────────
function Bar({ p, mn, mx }) {
  if (!p) return <span style={{color:'#334155'}}>—</span>
  const col = pClr(p,mn,mx)
  const pct = Math.max(8,((p-mn)/Math.max(mx-mn,.001))*100)
  return (
    <div style={{display:'flex',alignItems:'center',gap:6}}>
      <div style={{width:40,height:3,background:'#1e293b',borderRadius:2,flexShrink:0}}>
        <div style={{width:`${pct}%`,height:'100%',background:col,borderRadius:2,transition:'width .3s'}}/>
      </div>
      <b style={{color:col,fontSize:13,fontVariantNumeric:'tabular-nums'}}>{p.toFixed(3)}</b>
    </div>
  )
}

// ── AI Box ────────────────────────────────────────────────────────
function AIBox({ impianti, carb, centro }) {
  const [q, setQ] = useState('')
  const [ans, setAns] = useState('')
  const [busy, setBusy] = useState(false)
  const QUICK = ['Dove conviene fare il pieno?','Risparmio 60L min vs max?','Top 3 più economici?','GPL più vicino?']

  const ask = async txt => {
    if (!txt?.trim() || busy) return
    setBusy(true); setAns('')
    const snap = impianti.slice(0,20).map(i => {
      const ps = i.prezzi[`${carb}_self`], pv = i.prezzi[`${carb}_serv`]
      return `• ${i.nome} (${i.comune},${i.dist?.toFixed(1)}km): self=${ps?.toFixed(3)??'—'} serv=${pv?.toFixed(3)??'—'}`
    }).join('\n')
    try {
      const r = await fetch('https://api.anthropic.com/v1/messages', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({
          model: 'claude-sonnet-4-20250514', max_tokens: 600,
          messages: [{ role: 'user', content:
            `Assistente carburanti italiano. Dati MIMIT oggi entro ${centro?.raggio}km da ${centro?.nome}:\n${snap}\n\nDomanda: ${txt}\nRispondi in italiano, conciso (max 100 parole), prezzi concreti.`
          }]
        })
      })
      const d = await r.json()
      setAns(d.content?.[0]?.text ?? 'Errore')
    } catch(e) { setAns('Errore: ' + e.message) }
    setBusy(false)
  }

  return (
    <div style={{background:'#07111f',borderTop:'1px solid #1e293b',padding:'10px 16px',flexShrink:0}}>
      <div style={{fontSize:9,color:'#475569',letterSpacing:'0.12em',marginBottom:7}}>✦ CHIEDI ALL'AI</div>
      <div style={{display:'flex',gap:4,flexWrap:'wrap',marginBottom:7}}>
        {QUICK.map(q2 => (
          <button key={q2} onClick={()=>{setQ(q2);ask(q2)}} disabled={busy} style={{
            background:'#0f172a',border:'1px solid #1e293b',color:'#64748b',padding:'3px 8px',
            borderRadius:3,fontSize:10,cursor:'pointer',fontFamily:'inherit'}}>{q2}</button>
        ))}
      </div>
      <div style={{display:'flex',gap:6}}>
        <input value={q} onChange={e=>setQ(e.target.value)} onKeyDown={e=>e.key==='Enter'&&ask(q)}
          disabled={busy} placeholder="Scrivi la tua domanda..."
          style={{flex:1,background:'#0f172a',border:'1px solid #1e3a5f',color:'#cbd5e1',
            padding:'6px 10px',borderRadius:4,fontFamily:'inherit',fontSize:11,outline:'none'}}/>
        <button onClick={()=>ask(q)} disabled={busy||!q.trim()} style={{
          background:busy?'#0f172a':'#1d4ed8',border:'1px solid #1d4ed8',color:'#fff',
          padding:'6px 13px',borderRadius:4,cursor:'pointer',fontFamily:'inherit',fontSize:11,opacity:busy?.6:1}}>
          {busy?'…':'↵'}
        </button>
      </div>
      {(busy||ans) && (
        <div style={{marginTop:8,background:'#0a1628',border:'1px solid #1e3a5f',
          borderLeft:'3px solid #3b82f6',borderRadius:4,padding:'9px 12px',fontSize:12,
          color:'#94a3b8',lineHeight:1.7}}>
          {busy ? <span style={{color:'#3b82f6'}}>Analisi...</span> : ans}
        </div>
      )}
    </div>
  )
}

// ── STATI ─────────────────────────────────────────────────────────
const S = { GPS:'gps', INPUT:'input', LOADING:'loading', READY:'ready' }

// ── MAIN ─────────────────────────────────────────────────────────
export default function FuelRadar() {
  const [stato, setStato]     = useState(S.GPS)
  const [centro, setCentro]   = useState(null)
  const [impianti, setImpianti] = useState([])
  const [aggiornato, setAgg]  = useState('')
  const [errMsg, setErrMsg]   = useState('')
  const [comuneInput, setComuneInput] = useState('')
  const [raggio, setRaggio]   = useState(20)
  const [carb, setCarb]       = useState('Gasolio')
  const [tipo, setTipo]       = useState('self')
  const [sort, setSort]       = useState('prezzo')
  const [search, setSearch]   = useState('')
  const [sel, setSel]         = useState(null)
  const venetoRef = useRef(null)

  // Carica JSON Veneto (una volta sola, cachato in ref)
  async function loadVeneto() {
    if (venetoRef.current) return venetoRef.current
    const d = await fetchVeneto()
    venetoRef.current = d
    setAgg(d.aggiornato ?? '')
    return d
  }

  // Filtra impianti per posizione e raggio
  function filtra(lat, lon, nome, rag, dati) {
    const src = dati || venetoRef.current
    if (!src) return
    const found = (src.impianti ?? [])
      .map(i => ({ ...i, dist: haversine(lat, lon, i.lat, i.lon) }))
      .filter(i => i.dist <= rag)
      .sort((a,b) => (a.prezzi['Gasolio_self']??99) - (b.prezzi['Gasolio_self']??99))
    setCentro({ lat, lon, nome, raggio: rag })
    setImpianti(found)
    setStato(S.READY)
  }

  // Tenta GPS all'avvio
  useEffect(() => {
    ;(async () => {
      try {
        const pos = await getGPS()
        setStato(S.LOADING)
        const d = await loadVeneto()
        filtra(pos.lat, pos.lon, pos.nome, raggio, d)
      } catch(e) {
        setErrMsg(e.message)
        setStato(S.INPUT)
      }
    })()
  }, [])

  // Ricalcola quando cambia raggio
  useEffect(() => {
    if (stato === S.READY && centro && venetoRef.current) {
      filtra(centro.lat, centro.lon, centro.nome, raggio, null)
    }
  }, [raggio])

  async function handleCerca() {
    if (!comuneInput.trim()) return
    setErrMsg(''); setStato(S.LOADING)
    try {
      const pos = await geocodeComune(comuneInput.trim())
      const d = await loadVeneto()
      filtra(pos.lat, pos.lon, pos.nome || comuneInput, raggio, d)
    } catch(e) {
      setErrMsg(e.message); setStato(S.INPUT)
    }
  }

  async function handleGPS() {
    setErrMsg(''); setStato(S.GPS)
    try {
      const pos = await getGPS()
      setStato(S.LOADING)
      const d = await loadVeneto()
      filtra(pos.lat, pos.lon, pos.nome, raggio, d)
    } catch(e) {
      setErrMsg(e.message); setStato(S.INPUT)
    }
  }

  const key = `${carb}_${tipo==='servito'?'serv':'self'}`
  const filtered = impianti
    .filter(i => !search || [i.nome,i.comune,i.bandiera].some(s=>s?.toLowerCase().includes(search.toLowerCase())))
    .sort((a,b) =>
      sort==='prezzo' ? (a.prezzi[key]??99)-(b.prezzi[key]??99) :
      sort==='distanza' ? a.dist-b.dist :
      (a.nome??'').localeCompare(b.nome??'')
    )

  const prices = filtered.map(i=>i.prezzi[key]).filter(Boolean)
  const minP = prices.length ? Math.min(...prices) : 1.9
  const maxP = prices.length ? Math.max(...prices) : 2.3
  const avgP = prices.length ? prices.reduce((a,b)=>a+b,0)/prices.length : 0
  const bestImp = filtered.find(i=>i.prezzi[key]===minP)
  const ts = aggiornato
    ? new Date(aggiornato).toLocaleString('it-IT',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'})
    : '—'
  const CARBS = ['Gasolio','Benzina','GPL','Metano','HVO']
  const chip = (on, fn, lbl) => (
    <button onClick={fn} style={{
      background:on?'#1d4ed8':'#0f172a', color:on?'#fff':'#64748b',
      border:`1px solid ${on?'#1d4ed8':'#1e293b'}`,padding:'4px 9px',
      borderRadius:3,fontSize:10,cursor:'pointer',fontFamily:'inherit',transition:'all .15s'
    }}>{lbl}</button>
  )

  // ── Schermate di stato ────────────────────────────────────────
  if (stato === S.GPS) return (
    <div style={{height:'100vh',display:'flex',alignItems:'center',justifyContent:'center',
      background:'#060c18',color:'#cbd5e1',fontFamily:"'DM Mono',monospace",flexDirection:'column',gap:16}}>
      <style>{`@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}`}</style>
      <span style={{fontSize:36,animation:'pulse 1.5s infinite'}}>📡</span>
      <div style={{fontSize:13,color:'#60a5fa'}}>Rilevamento posizione GPS...</div>
      <div style={{fontSize:10,color:'#334155'}}>Potrebbe apparire una richiesta di permesso</div>
    </div>
  )

  if (stato === S.LOADING) return (
    <div style={{height:'100vh',display:'flex',alignItems:'center',justifyContent:'center',
      background:'#060c18',color:'#cbd5e1',fontFamily:"'DM Mono',monospace",flexDirection:'column',gap:16}}>
      <style>{`@keyframes spin{to{transform:rotate(360deg)}}`}</style>
      <span style={{fontSize:28,animation:'spin 1.2s linear infinite'}}>⟳</span>
      <div style={{fontSize:12,color:'#60a5fa',textAlign:'center',maxWidth:280,lineHeight:1.6}}>
        Caricamento impianti Veneto da MIMIT...
      </div>
    </div>
  )

  if (stato === S.INPUT) return (
    <div style={{height:'100vh',display:'flex',alignItems:'center',justifyContent:'center',
      background:'#060c18',color:'#cbd5e1',fontFamily:"'DM Mono','Fira Code',monospace",padding:24}}>
      <div style={{maxWidth:420,width:'100%'}}>
        <div style={{display:'flex',alignItems:'center',gap:12,marginBottom:28}}>
          <span style={{fontSize:28}}>⛽</span>
          <div>
            <div style={{fontFamily:'sans-serif',fontSize:20,fontWeight:800,color:'#f8fafc'}}>FUEL RADAR</div>
            <div style={{fontSize:10,color:'#475569'}}>Veneto · Prezzi MIMIT aggiornati ogni mattina</div>
          </div>
        </div>

        {errMsg && (
          <div style={{background:'#1c0a0a',border:'1px solid #ef444433',borderLeft:'3px solid #ef4444',
            borderRadius:4,padding:'10px 14px',fontSize:11,color:'#f87171',marginBottom:20,lineHeight:1.5}}>
            ⚠ {errMsg}
          </div>
        )}

        <div style={{fontSize:11,color:'#64748b',marginBottom:10,lineHeight:1.6}}>
          Inserisci il tuo comune per trovare i distributori più vicini:
        </div>

        <div style={{display:'flex',gap:8,marginBottom:10}}>
          <input value={comuneInput} onChange={e=>setComuneInput(e.target.value)}
            onKeyDown={e=>e.key==='Enter'&&handleCerca()} placeholder="Es: Treviso, Mestre, Verona..." autoFocus
            style={{flex:1,background:'#0f172a',border:'1px solid #1e3a5f',color:'#cbd5e1',
              padding:'11px 14px',borderRadius:4,fontFamily:'inherit',fontSize:13,outline:'none'}}/>
          <button onClick={handleCerca} disabled={!comuneInput.trim()} style={{
            background:'#1d4ed8',border:'none',color:'#fff',padding:'11px 20px',
            borderRadius:4,cursor:'pointer',fontFamily:'inherit',fontSize:12,
            opacity:!comuneInput.trim()?.5:1,whiteSpace:'nowrap'}}>
            Cerca
          </button>
        </div>

        <button onClick={handleGPS} style={{background:'#0f172a',border:'1px solid #1e3a5f',color:'#60a5fa',
          padding:'9px 14px',borderRadius:4,cursor:'pointer',fontFamily:'inherit',fontSize:11,width:'100%',marginBottom:16}}>
          📍 Usa GPS automatico
        </button>

        <div style={{fontSize:10,color:'#334155',lineHeight:1.7}}>
          Copertura: <b style={{color:'#475569'}}>Veneto</b> · Province: VE TV PD VR VI RO BL<br/>
          Fonte: MIMIT Osservaprezzi · Licenza IODL 2.0
        </div>
      </div>
    </div>
  )

  // ── Dashboard ────────────────────────────────────────────────
  return (
    <div style={{height:'100vh',display:'flex',flexDirection:'column',background:'#060c18',
      color:'#cbd5e1',fontFamily:"'DM Mono','Fira Code','Courier New',monospace",overflow:'hidden'}}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Syne:wght@800&display=swap');
        @keyframes spin{to{transform:rotate(360deg)}}
        @keyframes fi{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:translateY(0)}}
        *{box-sizing:border-box}
        .row{transition:background .1s;cursor:pointer}.row:hover{background:#0d1e3a!important}
        .row.s{background:#0d1e3a!important;border-left-color:#3b82f6!important}
        ::-webkit-scrollbar{width:5px}::-webkit-scrollbar-thumb{background:#1e293b;border-radius:3px}
      `}</style>

      {/* HEADER */}
      <div style={{background:'linear-gradient(180deg,#0a1628,#060c18)',borderBottom:'1px solid #1e293b',padding:'11px 16px',flexShrink:0}}>
        <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',flexWrap:'wrap',gap:8}}>
          <div style={{display:'flex',alignItems:'center',gap:10}}>
            <span style={{fontSize:20}}>⛽</span>
            <div>
              <div style={{display:'flex',alignItems:'center',gap:8,flexWrap:'wrap'}}>
                <span style={{fontFamily:"'Syne',sans-serif",fontSize:16,fontWeight:800,color:'#f8fafc'}}>FUEL RADAR</span>
                <span style={{fontSize:9,color:'#10b981',background:'#10b98118',border:'1px solid #10b98144',padding:'2px 8px',borderRadius:3}}>● LIVE MIMIT</span>
              </div>
              <div style={{fontSize:10,color:'#475569',display:'flex',alignItems:'center',gap:8,flexWrap:'wrap'}}>
                <span>📍 {centro?.nome}</span>
                <span>· {ts}</span>
                <button onClick={()=>{setStato(S.INPUT);setComuneInput('');setErrMsg('')}} style={{
                  background:'none',border:'none',color:'#60a5fa',cursor:'pointer',
                  fontFamily:'inherit',fontSize:10,padding:0,textDecoration:'underline'}}>
                  cambia posizione
                </button>
              </div>
            </div>
          </div>

          <div style={{display:'flex',gap:6,flexWrap:'wrap'}}>
            {[
              {l:`MIN ${carb}`,v:prices.length?`€ ${minP.toFixed(3)}`:'—',sub:bestImp?.comune,c:'#10b981'},
              {l:'MEDIA',v:prices.length?`€ ${avgP.toFixed(3)}`:'—',c:'#f59e0b'},
              {l:'RISP. 60L',v:prices.length?`€ ${((maxP-minP)*60).toFixed(2)}`:'—',c:'#a78bfa'},
              {l:`${raggio}km`,v:`${filtered.length} imp.`,c:'#60a5fa'},
            ].map(k => (
              <div key={k.l} style={{background:'#0a1628',border:`1px solid ${k.c}22`,borderLeft:`3px solid ${k.c}`,borderRadius:4,padding:'5px 10px',minWidth:80}}>
                <div style={{fontSize:9,color:'#475569',letterSpacing:'0.1em'}}>{k.l}</div>
                <div style={{fontSize:14,fontWeight:500,color:k.c}}>{k.v}</div>
                {k.sub && <div style={{fontSize:9,color:'#64748b'}}>{k.sub}</div>}
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* TOOLBAR */}
      <div style={{background:'#080e1c',borderBottom:'1px solid #1e293b',padding:'6px 16px',
        display:'flex',gap:5,flexWrap:'wrap',alignItems:'center',flexShrink:0}}>
        {CARBS.map(c => chip(carb===c, ()=>setCarb(c), c))}
        <div style={{width:1,height:16,background:'#1e293b'}}/>
        {chip(tipo==='self', ()=>setTipo('self'), 'Self')}
        {chip(tipo==='servito', ()=>setTipo('servito'), 'Serv.')}
        <div style={{width:1,height:16,background:'#1e293b'}}/>
        {[5,10,20].map(r => chip(raggio===r, ()=>setRaggio(r), `${r}km`))}
        <div style={{width:1,height:16,background:'#1e293b'}}/>
        {chip(sort==='prezzo', ()=>setSort('prezzo'), '€')}
        {chip(sort==='distanza', ()=>setSort('distanza'), '📍')}
        {chip(sort==='nome', ()=>setSort('nome'), 'A-Z')}
        <input value={search} onChange={e=>setSearch(e.target.value)} placeholder="🔍 Cerca..."
          style={{marginLeft:'auto',background:'#0f172a',border:'1px solid #1e293b',color:'#cbd5e1',
            padding:'4px 8px',borderRadius:3,fontFamily:'inherit',fontSize:10,width:120,outline:'none'}}/>
      </div>

      {/* BODY */}
      <div style={{flex:1,display:'flex',overflow:'hidden'}}>
        <div style={{flex:1,overflowY:'auto'}}>
          {filtered.length === 0 ? (
            <div style={{padding:40,textAlign:'center',color:'#475569',fontSize:12}}>
              Nessun impianto con prezzi comunicati oggi nel raggio di {raggio}km
            </div>
          ) : (
            <table style={{width:'100%',borderCollapse:'collapse',fontSize:11}}>
              <thead style={{position:'sticky',top:0,background:'#080e1c',zIndex:5}}>
                <tr style={{borderBottom:'1px solid #1e293b'}}>
                  {['#','Impianto','Comune','km',`${carb} self`,`${carb} serv.`,'Altri',''].map((h,i) => (
                    <th key={i} style={{padding:'7px 10px',textAlign:i===0?'center':'left',
                      color:'#334155',fontSize:9,letterSpacing:'0.12em',fontWeight:500,whiteSpace:'nowrap'}}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {filtered.map((imp,idx) => {
                  const isSel = sel?.id===imp.id
                  const bc = bandClr(imp.bandiera)
                  const pS = imp.prezzi[`${carb}_self`]
                  const pV = imp.prezzi[`${carb}_serv`]
                  const isBest = pS===minP && prices.length>1
                  const altri = Object.entries(imp.prezzi).filter(([k])=>!k.startsWith(carb))
                  return (
                    <tr key={imp.id} className={`row${isSel?' s':''}`}
                      onClick={()=>setSel(isSel?null:imp)}
                      style={{background:isSel?'#0d1e3a':idx%2===0?'#060c18':'#080e1c',
                        borderLeft:`3px solid ${isSel?'#3b82f6':'transparent'}`,
                        borderBottom:'1px solid #0f172a',
                        animation:`fi .2s ease ${Math.min(idx,30)*12}ms both`}}>
                      <td style={{padding:'8px 10px',textAlign:'center',color:isBest?'#10b981':'#334155',fontWeight:700}}>
                        {isBest?'★':idx+1}
                      </td>
                      <td style={{padding:'8px 10px'}}>
                        <div style={{display:'flex',alignItems:'center',gap:6}}>
                          <div style={{width:6,height:6,borderRadius:2,background:bc,boxShadow:`0 0 4px ${bc}77`,flexShrink:0}}/>
                          <div>
                            <div style={{color:'#e2e8f0',fontWeight:500}}>{imp.nome}</div>
                            <div style={{fontSize:9,color:'#475569'}}>{imp.bandiera}</div>
                          </div>
                        </div>
                      </td>
                      <td style={{padding:'8px 10px',color:'#64748b'}}>{imp.comune}</td>
                      <td style={{padding:'8px 10px',color:'#475569'}}>{imp.dist?.toFixed(1)}</td>
                      <td style={{padding:'8px 13px 8px 10px',minWidth:120}}><Bar p={pS} mn={minP} mx={maxP}/></td>
                      <td style={{padding:'8px 10px',color:'#475569',fontVariantNumeric:'tabular-nums'}}>
                        {pV ? pV.toFixed(3) : <span style={{color:'#1e293b'}}>—</span>}
                      </td>
                      <td style={{padding:'8px 10px'}}>
                        <div style={{display:'flex',gap:3,flexWrap:'wrap'}}>
                          {altri.slice(0,2).map(([k,v]) => (
                            <span key={k} style={{padding:'1px 5px',borderRadius:2,fontSize:9,
                              background:'#0f172a',border:'1px solid #1e293b',color:'#64748b'}}>
                              {k.replace('_self','').replace('_serv','✦')} {v.toFixed(3)}
                            </span>
                          ))}
                        </div>
                      </td>
                      <td style={{padding:'8px 10px',color:'#1e3a5f'}}>{isSel?'▾':'▸'}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          )}
        </div>

        {/* Sidebar */}
        {sel && (
          <div style={{width:260,borderLeft:'1px solid #1e293b',background:'#080e1c',
            overflowY:'auto',padding:14,flexShrink:0,animation:'fi .2s ease'}}>
            <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:10}}>
              <span style={{fontSize:10,color:'#94a3b8',fontWeight:700,letterSpacing:'0.1em'}}>IMPIANTO</span>
              <button onClick={()=>setSel(null)} style={{background:'none',border:'none',color:'#334155',cursor:'pointer',fontSize:14}}>✕</button>
            </div>
            <div style={{height:2,background:bandClr(sel.bandiera),borderRadius:1,marginBottom:10,boxShadow:`0 0 8px ${bandClr(sel.bandiera)}88`}}/>
            <div style={{fontSize:13,fontWeight:500,color:'#e2e8f0',marginBottom:1}}>{sel.nome}</div>
            <div style={{fontSize:9,color:'#475569',marginBottom:9}}>{sel.bandiera}</div>
            {[
              {l:'Indirizzo', v:sel.indirizzo},
              {l:'Comune', v:`${sel.comune} (${sel.prov})`},
              {l:'Distanza', v:`${sel.dist?.toFixed(2)} km`},
              {l:'ID MIMIT', v:`#${sel.id}`},
            ].map(r => (
              <div key={r.l} style={{marginBottom:6}}>
                <div style={{fontSize:9,color:'#334155',letterSpacing:'0.1em'}}>{r.l}</div>
                <div style={{fontSize:11,color:'#94a3b8'}}>{r.v||'—'}</div>
              </div>
            ))}

            <div style={{marginTop:12,marginBottom:4,fontSize:9,color:'#334155',letterSpacing:'0.12em'}}>TUTTI I PREZZI</div>
            {Object.entries(sel.prezzi).sort().map(([k,v]) => {
              const [cb,t] = k.split('_')
              return (
                <div key={k} style={{display:'flex',justifyContent:'space-between',alignItems:'center',
                  padding:'5px 8px',marginBottom:2,background:'#0a1628',borderRadius:3,border:'1px solid #1e293b'}}>
                  <span style={{color:'#64748b',fontSize:11}}>{cb} <span style={{color:'#334155'}}>{t==='self'?'self':'✦'}</span></span>
                  <b style={{color:pClr(v,minP,maxP),fontSize:13,fontVariantNumeric:'tabular-nums'}}>€ {v.toFixed(3)}</b>
                </div>
              )
            })}

            {sel.prezzi[`${carb}_self`] && (
              <div style={{marginTop:11,background:'#0a1628',border:'1px solid #1e3a5f',borderRadius:4,padding:10}}>
                <div style={{fontSize:9,color:'#334155',letterSpacing:'0.12em',marginBottom:6}}>PIENO {carb.toUpperCase()}</div>
                {[40,50,60,80].map(l => (
                  <div key={l} style={{display:'flex',justifyContent:'space-between',marginBottom:2}}>
                    <span style={{color:'#475569',fontSize:10}}>{l}L</span>
                    <span style={{color:'#f59e0b',fontSize:11,fontWeight:500}}>€ {(l*sel.prezzi[`${carb}_self`]).toFixed(2)}</span>
                  </div>
                ))}
                {prices.length>1 && (
                  <div style={{borderTop:'1px solid #1e293b',marginTop:5,paddingTop:5,display:'flex',justifyContent:'space-between'}}>
                    <span style={{fontSize:9,color:'#475569'}}>Risp. vs max (60L)</span>
                    <span style={{color:'#10b981',fontSize:11,fontWeight:700}}>€ {((maxP-sel.prezzi[`${carb}_self`])*60).toFixed(2)}</span>
                  </div>
                )}
              </div>
            )}

            <a href={`https://www.google.com/maps/search/?api=1&query=${sel.lat},${sel.lon}`}
              target="_blank" rel="noopener noreferrer"
              style={{display:'block',marginTop:10,background:'#0f2744',border:'1px solid #1d4ed8',
                color:'#60a5fa',textDecoration:'none',textAlign:'center',padding:6,borderRadius:4,fontSize:10}}>
              🗺 Google Maps
            </a>
          </div>
        )}
      </div>

      <AIBox impianti={filtered} carb={carb} centro={centro}/>
    </div>
  )
}