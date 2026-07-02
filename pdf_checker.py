import sys
import os
import re
import uuid
import socket
import tempfile
import threading
import webbrowser

import fitz  # PyMuPDF
import pandas as pd
from flask import Flask, request, jsonify, Response

app = Flask(__name__)
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0
app.config['MAX_CONTENT_LENGTH'] = 4 * 1024 * 1024 * 1024  # 4GB (local; no network upload limit)

CONFIG = {
    "MAX_PAGES": 10000,
    "REQUEST_TIMEOUT": 3600,
    "MANAGEMENT_FEE_EXCLUDED_PROPERTIES": [
        "PALM910", "PALM912", "PALM914", "PALM 918", "PALM 922",
        "PALM916", "PALM920", "ocbeach8700", "CLEVELAND369",
        "Magnolia20332", "VerdeMar9815", "Lyons17951", "SaintPaul6382",
    ],
}

# ---------------------------------------------------------------------------
# Fee lookup: remembered between runs in the OS application-data folder.
# ---------------------------------------------------------------------------
def get_app_data_dir():
    home = os.path.expanduser("~")
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA", os.path.join(home, "AppData", "Roaming"))
    elif sys.platform == "darwin":
        base = os.path.join(home, "Library", "Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME", os.path.join(home, ".local", "share"))
    d = os.path.join(base, "PDFPropertyValidator")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        d = tempfile.gettempdir()
    return d

CACHED_FEES_PATH = os.path.join(get_app_data_dir(), "property_fees.xlsx")

PROPERTY_FEES = {}
FEES_FILE_ERROR = "No fee file loaded yet \u2014 use \u201cUpdate fee file\u201d to add property_fees.xlsx."
FEES_SOURCE_NAME = None


def _parse_fees_dataframe(df):
    required_cols = {"property_code", "fee_percent", "min_dollar_charge"}
    if not required_cols.issubset(set(df.columns)):
        missing = required_cols - set(df.columns)
        raise ValueError("Missing required column(s): " + ", ".join(sorted(missing)))
    fees = {}
    for _, row in df.iterrows():
        code = str(row["property_code"]).strip()
        if code and code.lower() != "nan":
            fees[code] = {
                "fee_percent": float(row["fee_percent"]) if pd.notna(row["fee_percent"]) else None,
                "min_dollar_charge": float(row["min_dollar_charge"]) if pd.notna(row["min_dollar_charge"]) else None,
            }
    return fees


def load_fees_from_path(path, source_name=None):
    """Load the fee table from an .xlsx. Tries the 'Property Fees' sheet, then the first sheet."""
    global PROPERTY_FEES, FEES_FILE_ERROR, FEES_SOURCE_NAME
    if not os.path.exists(path):
        FEES_FILE_ERROR = "No fee file loaded yet."
        return False
    try:
        try:
            df = pd.read_excel(path, sheet_name="Property Fees", dtype={"property_code": str})
        except ValueError:
            df = pd.read_excel(path, dtype={"property_code": str})  # fall back to first sheet
        fees = _parse_fees_dataframe(df)
        if not fees:
            FEES_FILE_ERROR = "The fee file was read but contained no property rows."
            return False
        PROPERTY_FEES = fees
        FEES_FILE_ERROR = None
        FEES_SOURCE_NAME = source_name or os.path.basename(path)
        print("Loaded %d properties from %s" % (len(PROPERTY_FEES), FEES_SOURCE_NAME))
        return True
    except Exception as ex:
        FEES_FILE_ERROR = "Could not read the fee file: %s" % ex
        print("WARNING:", FEES_FILE_ERROR)
        return False


# Load the remembered fee file on startup, if present.
if os.path.exists(CACHED_FEES_PATH):
    load_fees_from_path(CACHED_FEES_PATH, "property_fees.xlsx (saved)")


def fees_payload():
    return {
        "loaded": FEES_FILE_ERROR is None and len(PROPERTY_FEES) > 0,
        "count": len(PROPERTY_FEES),
        "error": FEES_FILE_ERROR,
        "source": FEES_SOURCE_NAME,
    }

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>PDF Property Validator</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,500;9..144,600;9..144,700&family=Hanken+Grotesk:wght@400;500;600;700&family=Spline+Sans+Mono:wght@500;600&display=swap" rel="stylesheet">
<style>
  :root{
    --paper:#F2ECDF; --surface:#FBF7EE; --surface-2:#F6F0E3;
    --ink:#211C14; --ink-soft:#6F6553; --ink-faint:#9A9080;
    --line:#E2D8C4; --line-strong:#D2C5AB;
    --accent:#1E6A53; --accent-deep:#16513F; --accent-soft:#E0EDE6;
    --pass:#1F7A4D; --fail:#B23A2E; --fail-soft:#F8E7E3;
    --info:#9A9080; --warn:#9A6B12; --warn-soft:#F6EAD2;
    --shadow:0 1px 2px rgba(33,28,20,.05),0 8px 28px -12px rgba(33,28,20,.18);
    --radius:14px;
  }
  *{box-sizing:border-box;margin:0;padding:0}
  html{scroll-behavior:smooth}
  body{
    font-family:'Hanken Grotesk',-apple-system,BlinkMacSystemFont,sans-serif;
    color:var(--ink); background:var(--paper);
    background-image:
      radial-gradient(120% 80% at 50% -10%, rgba(30,106,83,.06), transparent 60%),
      radial-gradient(60% 50% at 100% 0%, rgba(154,107,18,.05), transparent 70%);
    background-attachment:fixed;
    line-height:1.5; padding:48px 20px 96px; -webkit-font-smoothing:antialiased;
  }
  .wrap{max-width:940px;margin:0 auto}
  .reveal{opacity:0;transform:translateY(10px);animation:rise .6s cubic-bezier(.2,.7,.2,1) forwards}
  @keyframes rise{to{opacity:1;transform:none}}
  @keyframes spin{to{transform:rotate(360deg)}}

  /* Masthead */
  header{display:flex;align-items:center;gap:16px;margin-bottom:34px}
  .mark{
    width:46px;height:46px;border-radius:12px;flex:0 0 auto;
    background:linear-gradient(150deg,var(--accent),var(--accent-deep));
    color:#F2ECDF;display:grid;place-items:center;
    font-family:'Fraunces',serif;font-weight:600;font-size:24px;
    box-shadow:0 6px 18px -8px rgba(22,81,63,.7);
  }
  .title h1{
    font-family:'Fraunces',serif;font-weight:600;font-size:30px;
    letter-spacing:-.01em;line-height:1.05;
  }
  .title p{color:var(--ink-soft);font-size:14px;margin-top:3px}

  /* Cards */
  .card{
    background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);
    box-shadow:var(--shadow);padding:22px 24px;margin-bottom:18px;
  }
  .card-head{display:flex;align-items:center;justify-content:space-between;gap:14px;margin-bottom:4px}
  .eyebrow{font-size:11px;letter-spacing:.16em;text-transform:uppercase;color:var(--ink-faint);font-weight:600}

  /* Fee status pill */
  .fee-row{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap}
  .pill{display:inline-flex;align-items:center;gap:9px;font-size:14px;font-weight:500;
    padding:9px 14px;border-radius:999px;border:1px solid transparent}
  .pill .dot{width:9px;height:9px;border-radius:50%;flex:0 0 auto}
  .pill.ok{background:var(--accent-soft);color:var(--accent-deep);border-color:#C6DFD4}
  .pill.ok .dot{background:var(--accent)}
  .pill.warn{background:var(--warn-soft);color:var(--warn);border-color:#E9D6A8}
  .pill.warn .dot{background:var(--warn)}
  .pill b{font-weight:700}

  /* Buttons */
  button{font-family:inherit;cursor:pointer;border:none;border-radius:10px;font-weight:600;
    transition:transform .08s ease,background .18s ease,box-shadow .18s ease}
  button:active{transform:translateY(1px)}
  .btn-primary{background:var(--accent);color:#F4EFE4;padding:14px 26px;font-size:15px;
    box-shadow:0 8px 20px -10px rgba(22,81,63,.8)}
  .btn-primary:hover:not(:disabled){background:var(--accent-deep)}
  .btn-primary:disabled{background:var(--line-strong);color:#8d8470;cursor:not-allowed;box-shadow:none}
  .btn-ghost{background:transparent;color:var(--accent-deep);padding:9px 15px;font-size:13.5px;
    border:1px solid var(--line-strong)}
  .btn-ghost:hover{background:var(--surface-2);border-color:var(--accent)}

  /* Dropzone */
  .drop{
    margin-top:14px;border:1.5px dashed var(--line-strong);border-radius:12px;
    background:var(--surface-2);padding:34px 22px;text-align:center;cursor:pointer;
    transition:border-color .18s ease,background .18s ease,transform .18s ease;
  }
  .drop:hover{border-color:var(--accent);background:#F1EBDD}
  .drop.drag{border-color:var(--accent);background:var(--accent-soft);transform:scale(1.005)}
  .drop svg{width:34px;height:34px;color:var(--accent);margin-bottom:10px}
  .drop .big{font-size:16px;font-weight:600}
  .drop .small{font-size:13px;color:var(--ink-soft);margin-top:4px}
  .drop .file{font-size:14px;color:var(--accent-deep);font-weight:600;margin-top:10px;
    word-break:break-all;display:none}
  .drop.has-file .file{display:block}
  .actions{margin-top:18px;display:flex;align-items:center;gap:14px;flex-wrap:wrap}

  /* Progress */
  #progress{display:none}
  .prog-top{display:flex;align-items:baseline;justify-content:space-between;margin-bottom:12px}
  .prog-pct{font-family:'Fraunces',serif;font-size:34px;font-weight:600;line-height:1}
  .prog-msg{color:var(--ink-soft);font-size:14px}
  .track{height:10px;background:var(--surface-2);border:1px solid var(--line);border-radius:999px;overflow:hidden}
  .fill{height:100%;width:0;border-radius:999px;
    background:linear-gradient(90deg,var(--accent),var(--accent-deep));
    transition:width .4s cubic-bezier(.3,.7,.3,1)}
  .indet{position:relative}
  .indet::after{content:"";position:absolute;inset:0;border-radius:999px;
    background:linear-gradient(90deg,transparent,rgba(255,255,255,.5),transparent);
    animation:sheen 1.1s linear infinite}
  @keyframes sheen{from{transform:translateX(-100%)}to{transform:translateX(100%)}}

  /* Alerts */
  .alert{padding:14px 16px;border-radius:10px;margin-bottom:18px;font-size:14.5px;display:none}
  .alert.show{display:block}
  .alert.err{background:var(--fail-soft);color:#8f2c22;border-left:4px solid var(--fail)}
  .alert.good{background:var(--accent-soft);color:var(--accent-deep);border-left:4px solid var(--accent)}

  /* Stats */
  #results{display:none}
  .stats{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:8px}
  .stat{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);
    padding:20px 22px;box-shadow:var(--shadow)}
  .stat .n{font-family:'Fraunces',serif;font-size:46px;font-weight:600;line-height:1}
  .stat .l{font-size:11px;letter-spacing:.16em;text-transform:uppercase;color:var(--ink-faint);
    font-weight:600;margin-top:8px}
  .stat.pass{border-top:3px solid var(--pass)} .stat.pass .n{color:var(--pass)}
  .stat.fail{border-top:3px solid var(--fail)} .stat.fail .n{color:var(--fail)}

  .results-head{display:flex;align-items:center;justify-content:space-between;gap:14px;
    margin:30px 0 14px}
  h2.section{font-family:'Fraunces',serif;font-weight:600;font-size:22px;letter-spacing:-.01em}
  h3.prop{font-family:'Fraunces',serif;font-weight:600;font-size:17px;color:var(--accent-deep);
    margin:24px 0 10px;padding-bottom:7px;border-bottom:1px solid var(--line)}

  /* Tables */
  table{width:100%;border-collapse:collapse;table-layout:fixed;
    background:var(--surface);border:1px solid var(--line);border-radius:10px;overflow:hidden}
  col.c-check{width:38%} col.c-val{width:24%} col.c-exp{width:24%} col.c-st{width:14%}
  th,td{padding:11px 14px;text-align:left;font-size:13.5px;border-bottom:1px solid var(--line);
    word-break:break-word;vertical-align:top}
  th{background:var(--surface-2);font-weight:700;color:var(--ink-soft);font-size:11px;
    letter-spacing:.07em;text-transform:uppercase}
  tr:last-child td{border-bottom:none}
  tbody tr:hover{background:#F4EEE0}
  td.num{font-family:'Spline Sans Mono',monospace;font-weight:500}
  .tag{font-weight:700;font-size:12px;letter-spacing:.04em}
  .tag.PASS{color:var(--pass)} .tag.FAIL{color:var(--fail)} .tag.INFO{color:var(--info);font-style:italic;font-weight:600}
  .summary td.failed{color:var(--fail);font-weight:500}
  .summary td.pname{font-weight:600}
  .loader{display:inline-block;width:14px;height:14px;border:2px solid rgba(244,239,228,.4);
    border-top-color:#F4EFE4;border-radius:50%;animation:spin .9s linear infinite;
    margin-right:9px;vertical-align:-2px}

  footer{text-align:center;color:var(--ink-faint);font-size:12px;margin-top:40px}
  @media(max-width:560px){
    .stats{grid-template-columns:1fr}
    body{padding:30px 14px 70px}
    .title h1{font-size:24px}
  }
</style>
</head>
<body>
<div class="wrap">

  <header class="reveal">
    <div class="mark">P</div>
    <div class="title">
      <h1>PDF Property Validator</h1>
      <p>Validate cash balances, management fees, and rent-roll data from statement PDFs.</p>
    </div>
  </header>

  <!-- Fee file -->
  <section class="card reveal" style="animation-delay:.05s">
    <div class="card-head"><span class="eyebrow">Fee lookup</span></div>
    <div class="fee-row">
      <div id="feePill" class="pill warn"><span class="dot"></span><span id="feeText">Checking fee file…</span></div>
      <button class="btn-ghost" onclick="document.getElementById('feeInput').click()">Update fee file</button>
      <input id="feeInput" type="file" accept=".xlsx,.xls" hidden />
    </div>
  </section>

  <!-- Upload -->
  <section class="card reveal" style="animation-delay:.1s">
    <div class="card-head"><span class="eyebrow">Statement</span></div>
    <div id="drop" class="drop">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
        <path d="M12 16V4"/><path d="m6 10 6-6 6 6"/><path d="M4 20h16"/>
      </svg>
      <div class="big">Drop a statement PDF here</div>
      <div class="small">or click to browse — no size limit, large multi-property files welcome</div>
      <div class="file" id="fileName"></div>
      <input id="pdfInput" type="file" accept="application/pdf" hidden />
    </div>
    <div class="actions">
      <button id="goBtn" class="btn-primary" onclick="validate()"><span id="goText">Validate statement</span></button>
    </div>
  </section>

  <!-- Progress -->
  <section id="progress" class="card">
    <div class="prog-top">
      <div class="prog-pct"><span id="pctNum">0</span>%</div>
      <div class="prog-msg" id="progMsg">Preparing…</div>
    </div>
    <div class="track"><div id="fill" class="fill"></div></div>
  </section>

  <div id="alert" class="alert"></div>

  <!-- Results -->
  <section id="results">
    <div class="stats">
      <div class="stat pass"><div class="n" id="nPass">0</div><div class="l">Properties passing</div></div>
      <div class="stat fail"><div class="n" id="nFail">0</div><div class="l">Properties failing</div></div>
    </div>
    <div class="results-head">
      <h2 class="section">Results</h2>
      <button class="btn-ghost" onclick="exportCsv()">Export CSV</button>
    </div>
    <div id="summary"></div>
    <div id="detail"></div>
  </section>

  <footer>PDF Property Validator v2.0 &middot; runs locally on your computer</footer>
</div>

<script>
  var lastData = null;
  var pollTimer = null;

  function $(id){return document.getElementById(id)}
  function esc(t){var d=document.createElement('div');d.textContent=(t==null?'':t);return d.innerHTML}
  function isNum(v){return typeof v==='string' && /[0-9]/.test(v) && (v.indexOf('$')>=0||v.indexOf('%')>=0)}

  function showAlert(msg,kind){
    var a=$('alert'); a.textContent=msg;
    a.className='alert show '+(kind==='good'?'good':'err');
  }
  function clearAlert(){var a=$('alert');a.className='alert'}

  // ---- Fee file ----
  function renderFees(d){
    var pill=$('feePill'), txt=$('feeText');
    if(d.loaded){
      pill.className='pill ok';
      txt.innerHTML='Fee table loaded &middot; <b>'+d.count+'</b> properties'+(d.source?(' &middot; '+esc(d.source)):'');
    }else{
      pill.className='pill warn';
      txt.textContent = d.error || 'No fee file loaded yet.';
    }
  }
  function refreshFees(){
    fetch('/fees').then(function(r){return r.json()}).then(renderFees).catch(function(){});
  }
  $('feeInput').addEventListener('change',function(e){
    var f=e.target.files[0]; if(!f) return;
    var pill=$('feePill'), txt=$('feeText');
    pill.className='pill warn'; txt.textContent='Loading '+f.name+'…';
    var fd=new FormData(); fd.append('file',f);
    fetch('/fees',{method:'POST',body:fd}).then(function(r){return r.json()}).then(function(d){
      renderFees(d);
      if(!d.loaded && d.error) showAlert(d.error,'err'); else clearAlert();
    }).catch(function(err){ showAlert('Could not load fee file: '+err.message,'err'); });
    e.target.value='';
  });

  // ---- Dropzone ----
  var drop=$('drop'), pdfInput=$('pdfInput');
  drop.addEventListener('click',function(){pdfInput.click()});
  ['dragenter','dragover'].forEach(function(ev){
    drop.addEventListener(ev,function(e){e.preventDefault();drop.classList.add('drag')});
  });
  ['dragleave','drop'].forEach(function(ev){
    drop.addEventListener(ev,function(e){e.preventDefault();drop.classList.remove('drag')});
  });
  drop.addEventListener('drop',function(e){
    var f=e.dataTransfer.files[0];
    if(f){ pdfInput.files=e.dataTransfer.files; setFile(f); }
  });
  pdfInput.addEventListener('change',function(e){ if(e.target.files[0]) setFile(e.target.files[0]); });
  function setFile(f){
    drop.classList.add('has-file');
    var mb=(f.size/1048576).toFixed(1);
    $('fileName').textContent=f.name+'  ('+mb+' MB)';
  }

  // ---- Validate ----
  function setBusy(b){
    $('goBtn').disabled=b;
    $('goText').innerHTML = b ? '<span class="loader"></span>Working…' : 'Validate statement';
  }
  function setProgress(pct,msg,indet){
    $('progress').style.display='block';
    $('pctNum').textContent=Math.round(pct);
    $('fill').style.width=pct+'%';
    $('fill').classList.toggle('indet',!!indet);
    if(msg!=null) $('progMsg').textContent=msg;
  }

  function validate(){
    clearAlert();
    if(pollTimer){clearInterval(pollTimer);pollTimer=null}
    var f=pdfInput.files[0];
    if(!f){ showAlert('Please choose a PDF statement first.','err'); return; }
    $('results').style.display='none';
    setBusy(true);
    setProgress(2,'Uploading to local engine…',true);

    var fd=new FormData(); fd.append('file',f);
    fetch('/start',{method:'POST',body:fd}).then(function(r){
      return r.json().then(function(d){return {ok:r.ok,d:d}});
    }).then(function(res){
      if(!res.ok || res.d.error){ throw new Error(res.d.error||'Could not start.'); }
      poll(res.d.job_id);
    }).catch(function(err){
      setBusy(false); $('progress').style.display='none';
      showAlert(err.message,'err');
    });
  }

  function poll(jobId){
    pollTimer=setInterval(function(){
      fetch('/progress/'+jobId).then(function(r){return r.json()}).then(function(p){
        if(p.error){ stopPoll(); setBusy(false); $('progress').style.display='none'; showAlert(p.error,'err'); return; }
        setProgress(p.percent||0,p.message,(p.percent||0)<1);
        if(p.status==='done'){
          stopPoll();
          fetch('/result/'+jobId).then(function(r){return r.json()}).then(function(data){
            setProgress(100,'Complete');
            setTimeout(function(){$('progress').style.display='none'},500);
            setBusy(false); render(data);
          });
        }else if(p.status==='error'){
          stopPoll(); setBusy(false); $('progress').style.display='none';
          showAlert(p.error||'Processing failed.','err');
        }
      }).catch(function(){ /* transient; keep polling */ });
    },500);
  }
  function stopPoll(){ if(pollTimer){clearInterval(pollTimer);pollTimer=null} }

  // ---- Render results ----
  function render(data){
    lastData=data;
    var total=data.detailed_checks.length;
    var failing=(data.failing_summary||[]).length;
    $('nPass').textContent=total-failing;
    $('nFail').textContent=failing;
    $('results').style.display='block';

    var sum=$('summary'); sum.innerHTML='';
    if(failing>0){
      var h='<h3 class="prop" style="color:var(--fail);border-color:var(--fail-soft)">Properties with failures</h3>';
      h+='<table class="summary"><colgroup><col class="c-check"><col style="width:62%"></colgroup>';
      h+='<thead><tr><th>Property</th><th>Failed checks</th></tr></thead><tbody>';
      data.failing_summary.forEach(function(p){
        h+='<tr><td class="pname">'+esc(p.property)+'</td><td class="failed">'+esc((p.failed_checks||[]).join(', '))+'</td></tr>';
      });
      h+='</tbody></table>';
      sum.innerHTML=h;
    }else{
      showAlert('All properties passed every validation check.','good');
    }

    var det=$('detail'); var html='';
    data.detailed_checks.forEach(function(p){
      html+='<h3 class="prop">'+esc(p.property)+'</h3>';
      html+='<table><colgroup><col class="c-check"><col class="c-val"><col class="c-exp"><col class="c-st"></colgroup>';
      html+='<thead><tr><th>Check</th><th>Value</th><th>Expected</th><th>Status</th></tr></thead><tbody>';
      p.results.forEach(function(r){
        html+='<tr><td>'+esc(r.check)+'</td>'+
              '<td class="'+(isNum(r.value)?'num':'')+'">'+esc(r.value)+'</td>'+
              '<td class="'+(isNum(r.expected)?'num':'')+'">'+esc(r.expected)+'</td>'+
              '<td><span class="tag '+r.status+'">'+r.status+'</span></td></tr>';
      });
      html+='</tbody></table>';
    });
    det.innerHTML=html;
    $('results').scrollIntoView({behavior:'smooth',block:'start'});
  }

  // ---- CSV export ----
  function exportCsv(){
    if(!lastData) return;
    var rows=[['Property','Check','Value','Expected','Status']];
    lastData.detailed_checks.forEach(function(p){
      p.results.forEach(function(r){ rows.push([p.property,r.check,r.value,r.expected,r.status]); });
    });
    var csv=rows.map(function(row){
      return row.map(function(c){ return '"'+String(c==null?'':c).replace(/"/g,'""')+'"'; }).join(',');
    }).join('\r\n');
    var blob=new Blob(['\ufeff'+csv],{type:'text/csv;charset=utf-8;'});
    var url=URL.createObjectURL(blob);
    var a=document.createElement('a'); a.href=url; a.download='validation_results.csv';
    document.body.appendChild(a); a.click(); document.body.removeChild(a); URL.revokeObjectURL(url);
  }

  refreshFees();
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Management fee validation using per-property lookup
# ---------------------------------------------------------------------------
def normalize_code(code):
    """Lowercase, strip whitespace, remove common punctuation for fuzzy comparison."""
    return re.sub(r'[\s\-_/\.,]', '', str(code).lower().strip())

def find_property_fee(prop_code):
    """
    Try to find a matching property in PROPERTY_FEES.
    1. Exact match
    2. Match against just the code portion (before the ' - ') of the Excel key
    3. Normalized match (case/whitespace insensitive) against code portion
    Returns the matched fee entry and the matched key, or (None, None).
    """
    # 1. Exact match
    if prop_code in PROPERTY_FEES:
        return PROPERTY_FEES[prop_code], prop_code

    normalized_input = normalize_code(prop_code)

    for key, entry in PROPERTY_FEES.items():
        # Extract just the code portion before the first dash or slash separator
        # Handles: 'CODE - address', 'CODE- address', 'CODE -address', 'CODE / address'
        code_portion = re.split(r'\s*[-/]\s*', key)[0].strip()

        # 2. Exact match against code portion
        if prop_code.strip() == code_portion:
            return entry, key

        # 3. Normalized match against code portion
        if normalize_code(code_portion) == normalized_input:
            return entry, key

    return None, None

def validate_management_fee(prop_code, management_fee_dollar_extracted, management_fee_percent_extracted):
    """
    Returns a list of result dicts and updates has_failures / failed_checks_for_summary.
    Returns: (results_list, has_failures, failed_checks)
    """
    results = []
    has_failures = False
    failed_checks = []

    # Look up this property in the fee table — no INFO row on success, only show on FAIL
    fee_entry, matched_key = find_property_fee(prop_code)

    if fee_entry is None:
        # Property not found in lookup file — FAIL
        has_failures = True
        failed_checks.append("Property Not in Fee Lookup File")
        results.append({
            "check": "Management Fee — Property Lookup",
            "value": f"'{prop_code}' not found in property_fees.xlsx",
            "expected": "Property must be listed in property_fees.xlsx",
            "status": "FAIL"
        })
        return results, has_failures, failed_checks

    expected_percent = fee_entry.get("fee_percent")
    expected_dollar  = fee_entry.get("min_dollar_charge")

    # Check each individually
    percent_passes = False
    dollar_passes = False

    if expected_percent is not None and management_fee_percent_extracted is not None:
        percent_passes = abs(management_fee_percent_extracted - expected_percent) < 0.001

    if expected_dollar is not None and management_fee_dollar_extracted is not None:
        dollar_passes = abs(management_fee_dollar_extracted - expected_dollar) < 0.01

    # PASS if either matches — % will normally be higher than $ minimum which is expected
    either_passes = percent_passes or dollar_passes
    overall_status = "PASS" if either_passes else "FAIL"

    if not either_passes:
        has_failures = True
        if management_fee_percent_extracted is None and management_fee_dollar_extracted is None:
            failed_checks.append("Management Fee Not Found")
        else:
            failed_checks.append("Management Fee Mismatch")

    # --- Percent row ---
    if expected_percent is not None:
        if management_fee_percent_extracted is not None:
            results.append({
                "check": "Management Fee (%) Match",
                "value": f"{management_fee_percent_extracted:.2f}%",
                "expected": f"{expected_percent:.2f}%",
                "status": "PASS" if percent_passes else overall_status
            })
        else:
            results.append({
                "check": "Management Fee (%) Match",
                "value": "N/A (Not Found)",
                "expected": f"{expected_percent:.2f}%",
                "status": "INFO"
            })

    # --- Dollar row ---
    if expected_dollar is not None:
        if management_fee_dollar_extracted is not None:
            results.append({
                "check": "Management Fee ($) Match",
                "value": f"${management_fee_dollar_extracted:,.2f}",
                "expected": f"${expected_dollar:,.2f}",
                "status": "PASS" if dollar_passes else overall_status
            })
        else:
            results.append({
                "check": "Management Fee ($) Match",
                "value": "N/A (Not Found)",
                "expected": f"${expected_dollar:,.2f}",
                "status": "INFO"
            })

    return results, has_failures, failed_checks


# ---------------------------------------------------------------------------
# PDF parsing
# ---------------------------------------------------------------------------
def parse_pdf(pdf_path, progress_cb=None):
    doc = None
    final_property_checks = []
    failing_properties_summary = []

    # Pre-compute normalised exclusion list once for the whole parse run
    excluded_codes = [normalize_code(c) for c in CONFIG.get("MANAGEMENT_FEE_EXCLUDED_PROPERTIES", [])]

    try:
        doc = fitz.open(pdf_path)
        property_page_map = {}
        current_property_key = None

        total_pages = doc.page_count
        all_pages_text_by_num = {}
        for p_num in range(total_pages):
            all_pages_text_by_num[p_num] = doc.load_page(p_num).get_text("text")
            if progress_cb and (p_num % 20 == 0 or p_num == total_pages - 1):
                progress_cb("reading", p_num + 1, total_pages)

        for page_num, page_text in all_pages_text_by_num.items():
            if page_num >= CONFIG["MAX_PAGES"]:
                break

            property_header_line = None
            for line in page_text.splitlines():
                if line.strip().startswith("Properties:"):
                    property_header_line = line.strip()
                    break

            if property_header_line:
                try:
                    header_content = property_header_line.replace("Properties:", "").strip()
                    if '-' in header_content:
                        code_part, addr_part = header_content.split("-", 1)
                        code = code_part.strip()
                        addr = addr_part.strip()
                    else:
                        code = header_content
                        addr = "N/A"
                    new_property_key = (code, addr)

                    if new_property_key != current_property_key:
                        current_property_key = new_property_key
                        if current_property_key not in property_page_map:
                            property_page_map[current_property_key] = []
                except ValueError:
                    if current_property_key is None:
                        current_property_key = ("UNKNOWN", "UNKNOWN (Header Parse Error)")
                        if current_property_key not in property_page_map:
                            property_page_map[current_property_key] = []

            if current_property_key:
                property_page_map[current_property_key].append(page_num)
            else:
                if ("UNASSIGNED", "NO_HEADER") not in property_page_map:
                    property_page_map[("UNASSIGNED", "NO_HEADER")] = []
                property_page_map[("UNASSIGNED", "NO_HEADER")].append(page_num)

        total_props = len(property_page_map)
        for prop_index, ((prop_code, prop_address), relevant_page_nums_for_prop) in enumerate(property_page_map.items()):
            if progress_cb:
                progress_cb("validating", prop_index + 1, total_props)

            cash_in_bank_operating = None
            actual_ending_cash = None
            management_fee_dollar_extracted = None
            management_fee_percent_extracted = None
            prepaid_rent_liability_value = None
            total_negative_past_due_sum = 0.0
            security_deposit_bank_account = None   # Balance Sheet asset
            security_deposit_liability = None      # Balance Sheet liability ("held in trust")
            rent_roll_deposit_total = None         # Rent Roll grand-total Deposit column

            full_property_text_for_lines = "\n".join([all_pages_text_by_num[p_num] for p_num in relevant_page_nums_for_prop])
            lines_for_extraction = full_property_text_for_lines.splitlines()

            standalone_number_pattern = re.compile(r"^\s*([-]?[\d,]+\.?\d{0,2})\s*$")

            for i, line in enumerate(lines_for_extraction):
                stripped_line = line.strip()

                if "Cash in Bank - Operating" == stripped_line and cash_in_bank_operating is None:
                    if i + 1 < len(lines_for_extraction):
                        next_line = lines_for_extraction[i+1].strip()
                        match = standalone_number_pattern.match(next_line)
                        if match:
                            try: cash_in_bank_operating = float(match.group(1).replace(",", ""))
                            except ValueError: pass

                if "Actual Ending Cash" == stripped_line and actual_ending_cash is None:
                    if i + 1 < len(lines_for_extraction):
                        next_line = lines_for_extraction[i+1].strip()
                        match = standalone_number_pattern.match(next_line)
                        if match:
                            try: actual_ending_cash = float(match.group(1).replace(",", ""))
                            except ValueError: pass

            for i, line in enumerate(lines_for_extraction):
                stripped_line = line.strip()

                if stripped_line == "Management Fees" and management_fee_dollar_extracted is None:
                    if i + 1 < len(lines_for_extraction):
                        next_line = lines_for_extraction[i+1].strip()
                        dollar_match = standalone_number_pattern.match(next_line)
                        if dollar_match:
                            try: management_fee_dollar_extracted = float(dollar_match.group(1).replace(",", ""))
                            except ValueError: pass

                            if i + 2 < len(lines_for_extraction):
                                percent_line = lines_for_extraction[i+2].strip()
                                percent_match = standalone_number_pattern.match(percent_line)
                                if percent_match:
                                    try: management_fee_percent_extracted = float(percent_match.group(1).replace(",", ""))
                                    except ValueError: pass
                    break

            for i, line in enumerate(lines_for_extraction):
                stripped_line = line.strip()
                if "Prepaid Rent Liability" in stripped_line and prepaid_rent_liability_value is None:
                    match = re.search(r"Prepaid Rent Liability.*?([-]?[\d,]+\.?\d{0,2})", stripped_line, re.IGNORECASE)
                    if match:
                        try:
                            value = float(match.group(1).replace(",", ""))
                            if value >= 0:
                                prepaid_rent_liability_value = value
                        except ValueError: pass

                    if prepaid_rent_liability_value is None and i + 1 < len(lines_for_extraction):
                        next_line = lines_for_extraction[i+1].strip()
                        match = standalone_number_pattern.match(next_line)
                        if match:
                            try:
                                value = float(match.group(1).replace(",", ""))
                                if value >= 0:
                                    prepaid_rent_liability_value = value
                            except ValueError: pass
                    break

            # --- Security Deposit (Balance Sheet: asset vs. liability) -------
            # "Security Deposit Bank Account" (asset) and
            # "Security Deposit ( held in trust account)" (liability) each sit
            # on their own line with the dollar amount on the following line.
            security_deposit_liability_pattern = re.compile(
                r"^Security Deposit\s*\(\s*held in trust", re.IGNORECASE
            )
            for i, line in enumerate(lines_for_extraction):
                stripped_line = line.strip()

                if "Security Deposit Bank Account" == stripped_line and security_deposit_bank_account is None:
                    if i + 1 < len(lines_for_extraction):
                        next_line = lines_for_extraction[i+1].strip()
                        match = standalone_number_pattern.match(next_line)
                        if match:
                            try: security_deposit_bank_account = float(match.group(1).replace(",", ""))
                            except ValueError: pass

                if security_deposit_liability_pattern.match(stripped_line) and security_deposit_liability is None:
                    if i + 1 < len(lines_for_extraction):
                        next_line = lines_for_extraction[i+1].strip()
                        match = standalone_number_pattern.match(next_line)
                        if match:
                            try: security_deposit_liability = float(match.group(1).replace(",", ""))
                            except ValueError: pass

            # --- Security Deposit (Rent Roll grand-total Deposit column) -----
            # The Rent Roll totals row reads, line by line:
            #   Total / <n> / Units / <pct>% / Occupied / <Rent total> / <Deposit total> / <Past Due total>
            # We scan for every "Occupied" marker and take the number two lines
            # below it (the Deposit column); if a block is preceded by a
            # standalone "Total" line within the previous few lines, that's the
            # grand-total row, so we prefer and lock in that one.
            for i, line in enumerate(lines_for_extraction):
                if line.strip() == "Occupied":
                    if i + 2 < len(lines_for_extraction):
                        deposit_line = lines_for_extraction[i+2].strip()
                        match = standalone_number_pattern.match(deposit_line)
                        if match:
                            try: rent_roll_deposit_total = float(match.group(1).replace(",", ""))
                            except ValueError: pass

                    preceded_by_total = any(
                        lines_for_extraction[j].strip() == "Total"
                        for j in range(max(0, i - 5), i)
                    )
                    if preceded_by_total and rent_roll_deposit_total is not None:
                        break

            # Rent Roll Logic
            past_due_col_x0 = -1
            past_due_col_x1 = -1
            header_y_coord = -1
            number_pattern_for_past_due = re.compile(r"([-]?[\d,]+\.?\d{0,2})")
            expected_header_phrases = ["Unit", "Tenant", "Additional Tenants", "Status", "Rent", "Deposit", "Move-in", "Lease From", "Lease To", "Past Due"]

            rent_roll_page_num = -1
            rent_roll_title_y = -1
            rent_word_pattern = re.compile(r"rent", re.IGNORECASE)
            roll_word_pattern = re.compile(r"roll", re.IGNORECASE)

            for p_num in relevant_page_nums_for_prop:
                page = doc.load_page(p_num)
                page_words = page.get_text("words")
                page_words.sort(key=lambda w: (w[1], w[0]))

                last_rent_word = None
                for word_bbox in page_words:
                    word_text = word_bbox[4]

                    if rent_word_pattern.search(word_text):
                        last_rent_word = word_bbox
                    elif roll_word_pattern.search(word_text) and last_rent_word:
                        if abs(word_bbox[1] - last_rent_word[1]) < 5 and (word_bbox[0] - last_rent_word[2]) < 10:
                            rent_roll_page_num = p_num
                            rent_roll_title_y = last_rent_word[1]
                            break
                    else:
                        last_rent_word = None

                if rent_roll_page_num != -1:
                    break

            if rent_roll_page_num != -1:
                all_property_words = doc.load_page(rent_roll_page_num).get_text("words")

                if rent_roll_title_y != -1:
                    all_property_words = [word for word in all_property_words if word[1] > rent_roll_title_y + 30]

                all_property_words.sort(key=lambda w: (w[1], w[0]))

                reconstructed_lines_of_words = []
                line_y_group_tolerance = 1

                if all_property_words:
                    current_line_words_group = []
                    current_line_y_sum = 0
                    current_line_word_count = 0

                    for word in all_property_words:
                        word_y_center = (word[1] + word[3]) / 2

                        if not current_line_words_group:
                            current_line_words_group.append(word)
                            current_line_y_sum += word_y_center
                            current_line_word_count += 1
                        else:
                            current_line_y_avg = current_line_y_sum / current_line_word_count
                            if abs(word_y_center - current_line_y_avg) < line_y_group_tolerance:
                                current_line_words_group.append(word)
                                current_line_y_sum += word_y_center
                                current_line_word_count += 1
                            else:
                                reconstructed_lines_of_words.append(current_line_words_group)
                                current_line_words_group = [word]
                                current_line_y_sum = word_y_center
                                current_line_word_count = 1

                    if current_line_words_group:
                        reconstructed_lines_of_words.append(current_line_words_group)

                for line_idx, current_line_words_for_reco in enumerate(reconstructed_lines_of_words):
                    current_line_words_for_reco.sort(key=lambda w: w[0])

                    y_key = round(current_line_words_for_reco[0][1])
                    full_line_text = " ".join([w[4] for w in current_line_words_for_reco])

                    if header_y_coord == -1:
                        found_all_phrases_in_sequence = True
                        current_search_text = full_line_text
                        past_due_word_bbox_in_header = None

                        for i, phrase in enumerate(expected_header_phrases):
                            phrase_pattern = r'\b' + re.escape(phrase) + r'\b'
                            match = re.search(phrase_pattern, current_search_text, re.IGNORECASE)

                            if not match:
                                found_all_phrases_in_sequence = False
                                break

                            if phrase == "Past Due":
                                _past_word_temp = None
                                _due_word_temp = None
                                for word_bbox in current_line_words_for_reco:
                                    if re.search(r'\bPast\b', word_bbox[4], re.IGNORECASE):
                                        _past_word_temp = word_bbox
                                    elif re.search(r'\bDue\b', word_bbox[4], re.IGNORECASE):
                                        _due_word_temp = word_bbox

                                    if _past_word_temp and _due_word_temp and abs(_due_word_temp[1] - _past_word_temp[1]) < 5 and (_due_word_temp[0] - _past_word_temp[2]) < 10:
                                        past_due_word_bbox_in_header = (_past_word_temp[0], _past_word_temp[1], _due_word_temp[2], _due_word_temp[3])
                                        break
                                    elif re.search(r'\bPast\s*Due\b', word_bbox[4], re.IGNORECASE):
                                        past_due_word_bbox_in_header = word_bbox
                                        break
                                if not past_due_word_bbox_in_header:
                                    found_all_phrases_in_sequence = False
                                    break

                            current_search_text = current_search_text[match.end():]

                        if found_all_phrases_in_sequence and past_due_word_bbox_in_header:
                            header_y_coord = y_key
                            temp_past_due_x0 = past_due_word_bbox_in_header[0]
                            temp_past_due_x1 = past_due_word_bbox_in_header[2]

                            if temp_past_due_x0 != float('inf'):
                                past_due_col_x0 = temp_past_due_x0 - 5
                                past_due_col_x1 = temp_past_due_x1 + 5
                            else:
                                header_y_coord = -1
                                past_due_col_x0 = -1
                                past_due_col_x1 = -1

                    if header_y_coord != -1 and past_due_col_x0 != -1 and past_due_col_x1 != -1:
                        if y_key == header_y_coord:
                            continue

                        extracted_words_in_column = []
                        for word in current_line_words_for_reco:
                            x0, y0, x1, y1, text_content, *_ = word
                            if (x0 < past_due_col_x1 + 5 and x1 > past_due_col_x0 - 5):
                                extracted_words_in_column.append(text_content)

                        column_content = " ".join(extracted_words_in_column).strip()

                        is_grand_total_line = bool(re.search(r'\bGrand\s*Total\b', full_line_text, re.IGNORECASE))
                        is_long_separator_line = bool(re.match(r"^\s*[-=]{10,}\s*$", full_line_text))

                        if (is_grand_total_line and y_key > header_y_coord) or \
                           (is_long_separator_line and y_key > header_y_coord + 10 and line_idx > 5):
                            break

                        if y_key > header_y_coord:
                            if column_content:
                                match = number_pattern_for_past_due.search(column_content)
                                if match:
                                    value_str = match.group(1).replace(",", "").replace("$", "").strip()
                                    try:
                                        numeric_value = float(value_str)
                                        is_summary_line = bool(re.search(r'\b(Total|Summary|Grand Total|Subtotal|Current Due|Current\s*Activity|Balance|Activity|Actual)\b', full_line_text, re.IGNORECASE)) or \
                                                          bool(re.search(r'\d{1,3}(?:[,\.]\d{3})*(?:[,\.]\d+)?\s*%', full_line_text, re.IGNORECASE))
                                        is_walnut_exclusion = bool(re.search(r'walnut\d+ - \d+', full_line_text, re.IGNORECASE))

                                        if numeric_value < 0 and not is_summary_line and not is_walnut_exclusion:
                                            total_negative_past_due_sum += numeric_value
                                    except ValueError:
                                        pass

            # -------------------------------------------------------------------
            # Build results
            # -------------------------------------------------------------------
            property_results = []
            has_failures = False
            failed_checks_for_summary = []

            # Cash in Bank - Operating
            if cash_in_bank_operating is not None:
                status = "PASS" if cash_in_bank_operating > 0 else "FAIL"
                if status == "FAIL":
                    has_failures = True
                    failed_checks_for_summary.append("Cash in Bank - Operating Positive")
                property_results.append({
                    "check": "Cash in Bank - Operating Positive",
                    "value": f"${cash_in_bank_operating:,.2f}",
                    "expected": "> $0",
                    "status": status
                })
            else:
                property_results.append({
                    "check": "Cash in Bank - Operating Positive",
                    "value": "N/A (Not Found)",
                    "expected": "> $0",
                    "status": "INFO"
                })

            # Actual Ending Cash
            if actual_ending_cash is not None:
                status = "PASS" if actual_ending_cash > 0 else "FAIL"
                if status == "FAIL":
                    has_failures = True
                    failed_checks_for_summary.append("Actual Ending Cash Positive")
                property_results.append({
                    "check": "Actual Ending Cash Positive",
                    "value": f"${actual_ending_cash:,.2f}",
                    "expected": "> $0",
                    "status": status
                })
            else:
                property_results.append({
                    "check": "Actual Ending Cash Positive",
                    "value": "N/A (Not Found)",
                    "expected": "> $0",
                    "status": "INFO"
                })

            # Management Fee — skip if property is in the exclusion list, otherwise validate
            if normalize_code(prop_code) in excluded_codes:
                property_results.append({
                    "check": "Management Fee — Property Lookup",
                    "value": f"'{prop_code}' is excluded from fee validation",
                    "expected": "Excluded (no check performed)",
                    "status": "INFO"
                })
            else:
                fee_results, fee_has_failures, fee_failed_checks = validate_management_fee(
                    prop_code, management_fee_dollar_extracted, management_fee_percent_extracted
                )
                property_results.extend(fee_results)
                if fee_has_failures:
                    has_failures = True
                    failed_checks_for_summary.extend(fee_failed_checks)

            # Prepaid Rent - Balance Sheet
            if prepaid_rent_liability_value is not None:
                status = "PASS" if prepaid_rent_liability_value >= 0 else "FAIL"
                if status == "FAIL":
                    has_failures = True
                    failed_checks_for_summary.append("Prepaid Rent - Balance Sheet")
                property_results.append({
                    "check": "Prepaid Rent - Balance Sheet",
                    "value": f"${prepaid_rent_liability_value:,.2f}",
                    "expected": ">= $0",
                    "status": status
                })
            else:
                property_results.append({
                    "check": "Prepaid Rent - Balance Sheet",
                    "value": "N/A (Not Found)",
                    "expected": ">= $0",
                    "status": "INFO"
                })

            # Prepaid Rent - Rent Roll
            expected_status_text = "N/A (Calculated Sum)"
            match_status_for_display = "INFO"
            display_value = "N/A (No negative values found)"
            if total_negative_past_due_sum < 0:
                display_value = f"${total_negative_past_due_sum:,.2f}"

            if total_negative_past_due_sum < 0 and prepaid_rent_liability_value is not None:
                epsilon = 0.001
                if abs(abs(total_negative_past_due_sum) - prepaid_rent_liability_value) < epsilon:
                    expected_status_text = "Match"
                    match_status_for_display = "PASS"
                else:
                    expected_status_text = f"No Match (Expected {prepaid_rent_liability_value:,.2f})"
                    match_status_for_display = "FAIL"
                    has_failures = True
                    failed_checks_for_summary.append("Prepaid Rent - Rent Roll")
            elif total_negative_past_due_sum == 0 and prepaid_rent_liability_value == 0:
                expected_status_text = "Match (No Negative Past Due, No Prepaid Liability)"
                match_status_for_display = "PASS"
            elif total_negative_past_due_sum >= 0:
                if prepaid_rent_liability_value is not None and prepaid_rent_liability_value > 0:
                    expected_status_text = f"No Match (Expected {prepaid_rent_liability_value:,.2f}, no negative past due found)"
                    match_status_for_display = "FAIL"
                    has_failures = True
                    failed_checks_for_summary.append("Prepaid Rent - Rent Roll")
                else:
                    expected_status_text = "N/A (No Negative Past Due to Compare)"
                    match_status_for_display = "INFO"
            elif prepaid_rent_liability_value is None:
                expected_status_text = "N/A (Prepaid Liability Not Found for Comparison)"
                match_status_for_display = "INFO"

            property_results.append({
                "check": "Prepaid Rent - Rent Roll",
                "value": display_value,
                "expected": expected_status_text,
                "status": match_status_for_display
            })

            # Security Deposit - Balance Sheet (asset vs. liability match)
            if security_deposit_bank_account is not None and security_deposit_liability is not None:
                epsilon = 0.001
                sd_bs_match = abs(security_deposit_bank_account - security_deposit_liability) < epsilon
                status = "PASS" if sd_bs_match else "FAIL"
                if status == "FAIL":
                    has_failures = True
                    failed_checks_for_summary.append("Security Deposit - Balance Sheet")
                property_results.append({
                    "check": "Security Deposit - Balance Sheet",
                    "value": f"${security_deposit_bank_account:,.2f} (bank)",
                    "expected": f"${security_deposit_liability:,.2f} (liability)",
                    "status": status
                })
            else:
                property_results.append({
                    "check": "Security Deposit - Balance Sheet",
                    "value": "N/A (Not Found)",
                    "expected": "Bank Account = Liability",
                    "status": "INFO"
                })

            # Security Deposit - Rent Roll (liability vs. Rent Roll deposit total)
            if security_deposit_liability is not None and rent_roll_deposit_total is not None:
                epsilon = 0.001
                sd_rr_match = abs(security_deposit_liability - rent_roll_deposit_total) < epsilon
                status = "PASS" if sd_rr_match else "FAIL"
                if status == "FAIL":
                    has_failures = True
                    failed_checks_for_summary.append("Security Deposit - Rent Roll")
                property_results.append({
                    "check": "Security Deposit - Rent Roll",
                    "value": f"${rent_roll_deposit_total:,.2f} (rent roll)",
                    "expected": f"${security_deposit_liability:,.2f} (liability)",
                    "status": status
                })
            else:
                property_results.append({
                    "check": "Security Deposit - Rent Roll",
                    "value": "N/A (Not Found)",
                    "expected": "Liability = Rent Roll Total Deposit",
                    "status": "INFO"
                })

            final_property_checks.append({
                "property": f"{prop_code} - {prop_address}",
                "results": property_results
            })

            if has_failures:
                failing_properties_summary.append({
                    "property": f"{prop_code} - {prop_address}",
                    "failed_checks": failed_checks_for_summary
                })

    finally:
        if doc:
            doc.close()

    return {"detailed_checks": final_property_checks, "failing_summary": failing_properties_summary}


# ---------------------------------------------------------------------------
# Background jobs (so the UI can show live progress on long files)
# ---------------------------------------------------------------------------
JOBS = {}
JOBS_LOCK = threading.Lock()


def _run_job(job_id, pdf_path):
    def cb(phase, current, total):
        if total and total > 0:
            if phase == "reading":
                pct = int((current / total) * 35)
                msg = "Reading page %s of %s\u2026" % ("{:,}".format(current), "{:,}".format(total))
            else:
                pct = 35 + int((current / total) * 63)
                msg = "Validating property %d of %d\u2026" % (current, total)
        else:
            pct, msg = 0, "Starting\u2026"
        with JOBS_LOCK:
            j = JOBS.get(job_id)
            if j:
                j["percent"] = max(j["percent"], pct)
                j["message"] = msg
    try:
        result = parse_pdf(pdf_path, progress_cb=cb)
        with JOBS_LOCK:
            j = JOBS.get(job_id)
            if j:
                if not result or not result.get("detailed_checks"):
                    j["status"] = "error"
                    j["error"] = "No properties were found in this PDF."
                else:
                    j["status"] = "done"; j["percent"] = 100
                    j["message"] = "Complete"; j["result"] = result
    except MemoryError:
        _fail(job_id, "This PDF is too large to fit in memory. Try splitting it into smaller files.")
    except Exception as ex:
        m = str(ex)
        if len(m) > 300:
            m = m[:300] + "\u2026"
        _fail(job_id, "Failed to process PDF: " + m)
    finally:
        try:
            if os.path.exists(pdf_path):
                os.remove(pdf_path)
        except Exception:
            pass


def _fail(job_id, message):
    with JOBS_LOCK:
        j = JOBS.get(job_id)
        if j:
            j["status"] = "error"; j["error"] = message


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route('/')
def index():
    return Response(HTML_TEMPLATE, mimetype='text/html')


@app.route('/fees', methods=['GET'])
def fees_get():
    return jsonify(fees_payload())


@app.route('/fees', methods=['POST'])
def fees_post():
    if 'file' not in request.files:
        return jsonify({"error": "No file part"}), 400
    f = request.files['file']
    if not f.filename:
        return jsonify({"error": "No file selected"}), 400
    if not f.filename.lower().endswith(('.xlsx', '.xls')):
        return jsonify({"error": "Please choose an Excel (.xlsx) file."}), 400
    try:
        f.save(CACHED_FEES_PATH)
    except Exception as ex:
        return jsonify({"error": "Could not save the fee file: %s" % ex}), 500
    ok = load_fees_from_path(CACHED_FEES_PATH, f.filename)
    return jsonify(fees_payload()), (200 if ok else 400)


@app.route('/start', methods=['POST'])
def start():
    if FEES_FILE_ERROR is not None or len(PROPERTY_FEES) == 0:
        return jsonify({"error": "Load a fee file before validating."}), 400
    if 'file' not in request.files:
        return jsonify({"error": "No file part"}), 400
    f = request.files['file']
    if not f.filename:
        return jsonify({"error": "No file selected"}), 400
    if not f.filename.lower().endswith('.pdf'):
        return jsonify({"error": "Please choose a PDF file."}), 400

    fd, pdf_path = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    try:
        f.save(pdf_path)
    except Exception as ex:
        return jsonify({"error": "Could not save the upload: %s" % ex}), 500

    job_id = uuid.uuid4().hex
    with JOBS_LOCK:
        JOBS[job_id] = {"status": "running", "percent": 0, "message": "Starting\u2026",
                        "result": None, "error": None}
    threading.Thread(target=_run_job, args=(job_id, pdf_path), daemon=True).start()
    return jsonify({"job_id": job_id})


@app.route('/progress/<job_id>')
def progress(job_id):
    with JOBS_LOCK:
        j = JOBS.get(job_id)
        if not j:
            return jsonify({"error": "Unknown job"}), 404
        return jsonify({"status": j["status"], "percent": j["percent"],
                        "message": j["message"], "error": j["error"]})


@app.route('/result/<job_id>')
def result(job_id):
    with JOBS_LOCK:
        j = JOBS.get(job_id)
        if not j:
            return jsonify({"error": "Unknown job"}), 404
        if j["status"] != "done":
            return jsonify({"error": "Result not ready"}), 409
        res = j["result"]
    return jsonify(res)


# ---------------------------------------------------------------------------
# Desktop launcher
# ---------------------------------------------------------------------------
def find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0)); s.listen(1)
        return s.getsockname()[1]


def open_browser(port):
    import time
    time.sleep(1.2)
    webbrowser.open("http://127.0.0.1:%d" % port)


if __name__ == '__main__':
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass

    port = find_free_port()
    threading.Thread(target=open_browser, args=(port,), daemon=True).start()

    print("\n  PDF Property Validator is running.")
    print("  Your browser should open automatically.")
    print("  If not, open: http://127.0.0.1:%d" % port)
    print("  Close this window to quit.\n")

    import logging
    logging.getLogger('werkzeug').setLevel(logging.WARNING)
    from werkzeug.serving import run_simple
    run_simple('127.0.0.1', port, app, use_reloader=False, use_debugger=False, threaded=True)
