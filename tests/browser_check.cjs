// Local UI check in a disposable browser profile on E:. No npm dependencies.
const {spawn} = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const runtime = path.join(root, 'runtime');
const env = {...process.env, TEMP:path.join(runtime,'tmp'), TMP:path.join(runtime,'tmp'),
  PYTHONDONTWRITEBYTECODE:'1', IR_WB_PORT:'8876', IR_WB_NO_BROWSER:'1'};
const delay=ms=>new Promise(r=>setTimeout(r,ms));
let chrome, server, ws;
async function main(){
  server=spawn(path.join(root,'.venv','Scripts','python.exe'),['-B','ir_wb_gui.py'],
    {cwd:root,env,windowsHide:true,stdio:'ignore'});
  chrome=spawn('C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',[
    '--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check',
    '--remote-debugging-port=9336','--user-data-dir='+path.join(runtime,'browser-profile'),
    '--disk-cache-dir='+path.join(runtime,'browser-cache'),'about:blank'],
    {env,windowsHide:true,stdio:'ignore'});
  let ready=false;
  for(let i=0;i<50;i++){
    try{ await fetch('http://127.0.0.1:8876/',{signal:AbortSignal.timeout(500)}); await fetch('http://127.0.0.1:9336/json/version',{signal:AbortSignal.timeout(500)}); ready=true;break; }
    catch { await delay(200); }
  }
  if(!ready)throw new Error('Application or headless Chrome did not start');
  const target=await (await fetch('http://127.0.0.1:9336/json/new?about:blank',{method:'PUT'})).json();
  ws=new WebSocket(target.webSocketDebuggerUrl);
  await Promise.race([new Promise((res,rej)=>{ws.onopen=res;ws.onerror=rej;}),delay(5000).then(()=>{throw new Error('Browser connection timed out');})]);
  let id=0; const pending=new Map(), errors=[];
  ws.onmessage=e=>{
    const m=JSON.parse(e.data);
    if(m.id){ const p=pending.get(m.id); if(!p)return;
      pending.delete(m.id); m.error?p.reject(m.error):p.resolve(m.result); }
    if(m.method==='Runtime.exceptionThrown') errors.push(m.params.exceptionDetails);
  };
  const send=(method,params={})=>new Promise((resolve,reject)=>{
    const n=++id;pending.set(n,{resolve,reject});ws.send(JSON.stringify({id:n,method,params}));
  });
  const evaluate=async expression=>{
    const r=await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});
    if(r.exceptionDetails)throw new Error(JSON.stringify(r.exceptionDetails));return r.result.value;
  };
  const waitFor=async expression=>{
    for(let i=0;i<150;i++){if(await evaluate(expression))return;await delay(150);}
    throw new Error('UI timeout: '+expression);
  };
  await send('Runtime.enable'); await send('Page.enable');
  await send('Emulation.setDeviceMetricsOverride',{width:1440,height:1100,deviceScaleFactor:1,mobile:false});
  await send('Page.navigate',{url:'http://127.0.0.1:8876/'});
  await waitFor("!!document.querySelector('#s_mr')");
  const {root:doc}=await send('DOM.getDocument');
  const {nodeId}=await send('DOM.querySelector',{nodeId:doc.nodeId,selector:'#file'});
  const fixtureDir=path.join(root,'output','.cache');
  const source=process.env.IR_WB_TEST_RAW || path.join(fixtureDir,
    fs.readdirSync(fixtureDir).find(name=>/\.(arw|dng)$/i.test(name))||'missing.raw');
  if(!fs.existsSync(source))throw new Error('Set IR_WB_TEST_RAW to a real RAW fixture on E:');
  await send('DOM.setFileInputFiles',{nodeId,files:[source]});
  await evaluate("document.querySelector('#file').dispatchEvent(new Event('change',{bubbles:true}))");
  await waitFor("items[0]?.data && document.querySelector('#img').naturalWidth>0 && !document.querySelector('#spin').classList.contains('on')");
  await evaluate("document.querySelector('#s_exposure').value=2;document.querySelector('#s_exposure').dispatchEvent(new Event('input'));document.querySelector('#s_highlights').value=75;document.querySelector('#s_highlights').dispatchEvent(new Event('input'));");
  await delay(800);
  await waitFor("!document.querySelector('#spin').classList.contains('on')");
  const info=await evaluate("({rows:document.querySelectorAll('#pgrid .row').length,dimensions:[imgEl.naturalWidth,imgEl.naturalHeight],exposure:pv.exposure,highlights:pv.highlights,wb:[pv.mr,pv.mb],meta:document.querySelector('#meta').textContent})");
  if(info.rows!==24||info.exposure!==2||info.highlights!==75)throw new Error(JSON.stringify(info));
  if(!await evaluate("document.querySelector('#histInfo').textContent.includes('接近白色')")) throw new Error('Histogram missing');
  await evaluate("document.querySelector('#v_mr').value=80;document.querySelector('#v_mr').dispatchEvent(new Event('change'))");
  if(await evaluate('pv.mr')!==80)throw new Error('WB numeric input outside slider range failed');
  await evaluate("document.querySelector('#btnAuto').click()");
  await delay(800);await waitFor("!document.querySelector('#spin').classList.contains('on')");
  await evaluate("document.querySelector('#s_black').value=30;document.querySelector('#s_black').dispatchEvent(new Event('input'));document.querySelector('#btnJpg').click()");
  await waitFor("!busy && document.querySelector('#toast').textContent.includes('.jpg')");
  const jpgMessage=await evaluate("document.querySelector('#toast').textContent");
  if(!jpgMessage.startsWith('已保存 '))throw new Error(jpgMessage);
  const jpgPath=jpgMessage.slice('已保存 '.length);
  if(!fs.existsSync(jpgPath)||fs.readFileSync(jpgPath).readUInt16BE(0)!==0xffd8)throw new Error('No valid JPG saved');
  const screen=await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:true});
  fs.writeFileSync(path.join(runtime,'ui-verified.png'),Buffer.from(screen.data,'base64'));
  if(errors.length)throw new Error(JSON.stringify(errors));
  console.log(JSON.stringify({passed:true,...info,screenshot:path.join(runtime,'ui-verified.png')},null,2));
}
main().catch(e=>{console.error(e);process.exitCode=1;}).finally(()=>{
  if(ws)ws.close();if(chrome)chrome.kill();if(server)server.kill();
});
