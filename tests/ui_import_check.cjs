// Optional browser integration test. Set IR_TEST_RAW, PLAYWRIGHT_DIR and IR_BROWSER.
const {spawn} = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const runtime = path.join(root, 'runtime');
const raw = process.env.IR_TEST_RAW;
if (!raw || !process.env.PLAYWRIGHT_DIR || !process.env.IR_BROWSER) {
  throw new Error('Set IR_TEST_RAW, PLAYWRIGHT_DIR and IR_BROWSER to local paths');
}
const {chromium} = require(process.env.PLAYWRIGHT_DIR);
const env = {...process.env, TEMP:path.join(runtime,'tmp'), TMP:path.join(runtime,'tmp'),
  PYTHONDONTWRITEBYTECODE:'1', IR_WB_PORT:'18876', IR_WB_NO_BROWSER:'1'};
let server, browser;
(async()=>{
  server=spawn(path.join(root,'.venv','Scripts','python.exe'), ['-B','ir_wb_gui.py'],
    {cwd:root, env, windowsHide:true, stdio:'ignore'});
  browser=await chromium.launch({executablePath:process.env.IR_BROWSER,headless:true,
    args:['--no-first-run','--no-default-browser-check'],env});
  const page=await browser.newPage();
  const errors=[];page.on('pageerror', e=>errors.push(e.message));
  for(let i=0;;i++){
    try{await page.goto('http://127.0.0.1:18876/',{timeout:2000});break;}
    catch(e){if(i>=25)throw e;await new Promise(r=>setTimeout(r,200));}
  }
  await page.locator('#file').setInputFiles(raw);
  await page.locator('.raw-item .raw-sub b').waitFor({timeout:30000});
  await page.waitForFunction(()=>document.querySelector('#img').naturalWidth>0,{timeout:30000});
  if(await page.locator('#btnJpg').isDisabled())throw new Error('Export did not enable after import');
  const info=await page.locator('#histInfo').textContent();
  if(!info.includes('%'))throw new Error('Histogram did not update');
  await page.locator('#s_color_mode').selectOption('coral_canopy');
  await page.waitForFunction(()=>document.querySelector('#img').src.includes('color_mode=coral_canopy') && !document.querySelector('#spin').classList.contains('active'), null, {timeout:30000});
  if(await page.locator('#s_color_mode').inputValue()!=='coral_canopy')throw new Error('Coral look missing');
  await page.keyboard.down('\\');
  if(!await page.locator('#compareBadge').evaluate(x=>x.classList.contains('active')))throw new Error('Compare key did not activate');
  await page.keyboard.up('\\');
  if(await page.locator('#compareBadge').evaluate(x=>x.classList.contains('active')))throw new Error('Compare key did not release');
  const bad=path.join(runtime,'tmp','invalid-import.raw');
  fs.writeFileSync(bad,'not a RAW file');
  await page.locator('#file').setInputFiles(bad);
  await page.locator('.raw-item.error .raw-sub').waitFor({timeout:15000});
  const failure=await page.locator('.raw-item.error .raw-sub').textContent();
  if(!failure.includes('：') || failure.endsWith('：'))throw new Error('Missing import error details');
  await page.evaluate(()=>{
    const transfer=new DataTransfer();
    transfer.items.add(new File(['invalid'], 'dragged.raw'));
    document.querySelector('#drop').dispatchEvent(new DragEvent('drop',{bubbles:true,dataTransfer:transfer}));
  });
  await page.waitForFunction(()=>document.querySelectorAll('.raw-item.error').length===2, null, {timeout:15000});
  if(errors.length)throw new Error(errors.join('\n'));
  console.log(JSON.stringify({ok:true,preview:await page.locator('#img').evaluate(x=>[x.naturalWidth,x.naturalHeight]),failure}));
})().catch(e=>{console.error(e);process.exitCode=1;}).finally(async()=>{
  if(browser)await browser.close();if(server)server.kill();
});
