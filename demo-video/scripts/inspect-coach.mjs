import {createRequire} from 'node:module';
import {writeFile,mkdir} from 'node:fs/promises';
const require=createRequire(import.meta.url);
const {chromium}=require('../../frontend/node_modules/playwright');
const root=new URL('../',import.meta.url).pathname;
await mkdir(`${root}/out/v2`,{recursive:true});
const browser=await chromium.launch({headless:true});
try{
 const context=await browser.newContext({viewport:{width:1920,height:1080},colorScheme:'dark'});
 await context.addInitScript(()=>{localStorage.setItem('theme','dark');localStorage.setItem('matchlens.onboarded.v1','1');});
 const page=await context.newPage();
 await page.goto('http://127.0.0.1:3100/coach?tab=tactics',{waitUntil:'networkidle'});
 await page.evaluate(()=>document.fonts.ready);
 await writeFile(`${root}/out/v2/tactics-before.txt`,await page.locator('body').innerText());
 console.log((await page.locator('body').innerText()).slice(0,3500));
 await page.screenshot({path:`${root}/out/v2/tactics-before.png`});
}finally{await browser.close();}
