import {createRequire} from 'node:module';
import {mkdir,writeFile} from 'node:fs/promises';
import {execFileSync} from 'node:child_process';
const require=createRequire(import.meta.url);
const {chromium}=require('../../frontend/node_modules/playwright');
const root=new URL('../',import.meta.url).pathname;
const report={source:'ui-redesign-codex isolated capture',recordedAt:new Date().toISOString(),clips:[],notes:[]};
const api=async(path,body)=>{
 const res=await fetch(`http://127.0.0.1:8877/api/${path}`,body?{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)}:{});
 if(!res.ok)throw new Error(`${path}: ${res.status} ${await res.text()}`);
 return res.json();
};
await mkdir(`${root}/out/v2`,{recursive:true});
const health=await api('health');
if(health.store.backend!=='memory')throw new Error('Coach recording requires isolated memory state');
await api('replay',{seed:7,speed:1,start_minute:80});
await api('replay/control',{paused:true});
const browser=await chromium.launch({headless:true});
try{
 const context=await browser.newContext({viewport:{width:1920,height:1080},colorScheme:'dark'});
 await context.addInitScript(()=>{localStorage.setItem('theme','dark');localStorage.setItem('matchlens.onboarded.v1','1');});
 const page=await context.newPage();
 async function go(path){await page.goto(`http://127.0.0.1:3100${path}`,{waitUntil:'domcontentloaded'});await page.getByRole('tab',{name:'Tactics',exact:true}).waitFor();await page.evaluate(()=>document.fonts.ready);}
 async function capture(name,seconds,action=async()=>{}){
  const dir=`${root}/capture-cache/v2-${name}`;await mkdir(dir,{recursive:true});
  const start=performance.now(),frames=[{data:await page.screenshot({type:'jpeg',quality:95}),time:0}];
  const cdp=await context.newCDPSession(page);
  cdp.on('Page.screencastFrame',e=>{frames.push({data:Buffer.from(e.data,'base64'),time:(performance.now()-start)/1000});cdp.send('Page.screencastFrameAck',{sessionId:e.sessionId}).catch(()=>{});});
  await cdp.send('Page.startScreencast',{format:'jpeg',quality:95,maxWidth:1920,maxHeight:1080,everyNthFrame:1});
  await action();
  const left=seconds*1000-(performance.now()-start);if(left>0)await page.waitForTimeout(left);
  await cdp.send('Page.stopScreencast');await cdp.detach();
  const ordered=frames.filter((f,i)=>f.time<seconds&&(i===0||f.time>frames[i-1].time+.001));
  const lines=[];
  for(let i=0;i<ordered.length;i++){
   const file=`frame-${String(i).padStart(5,'0')}.jpg`;await writeFile(`${dir}/${file}`,ordered[i].data);
   lines.push(`file '${file}'`,`duration ${Math.max(.001,(ordered[i+1]?.time??seconds)-ordered[i].time).toFixed(6)}`);
  }
  lines.push(`file 'frame-${String(ordered.length-1).padStart(5,'0')}.jpg'`);await writeFile(`${dir}/frames.txt`,lines.join('\n'));
  execFileSync('ffmpeg',['-y','-v','error','-f','concat','-safe','0','-i',`${dir}/frames.txt`,'-vf','fps=30,format=yuv420p','-c:v','libx264','-crf','16','-preset','fast','-movflags','+faststart','-t',String(seconds),`${root}/public/footage/${name}.mp4`]);
  report.clips.push({name,seconds,frames:ordered.length,visibleText:await page.locator('body').innerText()});
  console.log(`Recorded ${name}: ${seconds}s`);
  await page.screenshot({path:`${root}/out/v2/${name}.png`});
 }
 await go('/coach');
 await page.getByRole('heading',{name:/^1\./}).first().waitFor({timeout:30000});
 await capture('11-coach-advice',8,async()=>{await page.waitForTimeout(700);await page.getByRole('button',{name:'Why',exact:true}).first().click();});
 await go('/coach?tab=tactics');
 await page.getByRole('button',{name:/Chasing a goal/}).click();
 const response=page.waitForResponse(r=>r.url().endsWith('/api/backend/tactics/boards')&&r.request().method()==='POST',{timeout:30000});
 await capture('12-coach-build',4,async()=>{await page.getByRole('button',{name:'Build ideas',exact:true}).click();});
 const created=await (await response).json();
 if(!created.board_id)throw new Error(`No board: ${JSON.stringify(created)}`);
 const begun=performance.now();let board;
 for(let i=0;i<90;i++){
  board=await api(`tactics/boards/${created.board_id}`);
  if(board.status==='done'||board.status==='failed')break;
  await page.waitForTimeout(1000);
 }
 if(board.status!=='done'||!board.tactics.some(t=>t.status==='ready'))throw new Error(`Board did not complete: ${board.status}`);
 report.board=board;report.generationSeconds=(performance.now()-begun)/1000;
 report.notes.push('Generation wait is omitted between setup and ready-state shots; model/source and uncertainty labels remain visible.');
 await writeFile(`${root}/out/v2/coach-board.json`,JSON.stringify(board,null,2));
 await page.waitForTimeout(1500);
 await writeFile(`${root}/out/v2/tactics-ready.txt`,await page.locator('body').innerText());
 await page.screenshot({path:`${root}/out/v2/tactics-ready.png`,fullPage:true});
 const compare=page.getByRole('button',{name:'Compare',exact:true}).first();
 await compare.scrollIntoViewIfNeeded();await compare.click();
 await page.getByRole('heading',{name:'Side by side',exact:true}).waitFor({timeout:30000});
 const mainHeading=page.getByRole('heading',{name:'Side by side',exact:true});
 if(await mainHeading.count())await mainHeading.scrollIntoViewIfNeeded();
 await page.mouse.wheel(0,320);
 await page.waitForTimeout(2200);
 console.log('COMPARE', (await page.locator('body').innerText()).slice(0,8000));
 await capture('13-coach-compare',16,async()=>{
  const replay=page.getByRole('button',{name:/^Replay$|^Play$/}).first();
  if(await replay.count())await replay.click();
 });
 await writeFile(`${root}/out/v2/tactics-compare.txt`,await page.locator('body').innerText());
 await page.screenshot({path:`${root}/out/v2/tactics-compare-full.png`,fullPage:true});
 const variants=page.getByRole('button',{name:'Worst case',exact:true});
 if(await variants.count()){
  await variants.first().click();await page.waitForTimeout(1200);
  await capture('14-coach-risk',8);
 }
 const verdict=page.getByRole('heading',{name:/No clear winner/}).first();
 await verdict.scrollIntoViewIfNeeded();
 await page.waitForTimeout(400);
 await capture('15-coach-verdict',8);
 await writeFile(`${root}/out/v2/tactics-verdict.txt`,await page.locator('body').innerText());
}finally{await writeFile(`${root}/out/v2/coach-capture-report.json`,JSON.stringify(report,null,2));await browser.close();}
