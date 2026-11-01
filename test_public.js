// Read-only browser proof. Never submits a ticket, offer or Watch mutation.
const {chromium}=require('playwright');
const assert=require('node:assert/strict'),fs=require('fs');
const url=process.env.BOARD_URL||'https://liveintel.edgebotpicks.tech';
const out='/opt/liveintel/verification/board-cutover';
const forbidden=['TEST_USER','Early Test','Test Owner','null','NoneQ','#None','Daniel Jones','Jonathan Taylor','$373.00','$191.00'];
(async()=>{
    fs.mkdirSync(out,{recursive:true});
    const browser=await chromium.launch({headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
    try{
        const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
        page.on('pageerror',e=>errors.push(e.message));
        const response=await page.goto(url,{waitUntil:'domcontentloaded',timeout:45000});
        assert.equal(response.status(),200);
        await page.waitForSelector('[data-desk="CFB_AI"]',{timeout:30000});
        const initial=await page.request.get(url+'/api/board');assert.equal(initial.status(),200);
        const data=await initial.json();
        assert.equal(data.specialists.length,3);
        for(const tab of ['live','players','watch','picks','tickets','intelligence','heat','math']){
            await page.locator('[data-tab="'+tab+'"]').click();
            if(tab==='players'){
                const sportOptions=await page.locator('#player-sport option').evaluateAll(x=>x.map(o=>o.value).filter(Boolean));
                if(sportOptions.length){await page.selectOption('#player-sport',sportOptions[0]);const games=await page.locator('#player-game option').evaluateAll(x=>x.map(o=>o.value).filter(Boolean));if(games.length){await page.selectOption('#player-game',games[0]);await page.waitForSelector('#player-cards .player-card',{timeout:30000});}}
            }
            if(tab==='heat')await page.waitForFunction(()=>!document.getElementById('heat-status').textContent.startsWith('Loading'),{timeout:30000});
            if(tab==='picks'){
                assert.equal(await page.locator('#picks-cards > article').count(),4);
                assert.equal(await page.locator('#owner-ticket-form').count(),1);
                const before=await page.request.get(url+'/api/board');
                const copy=page.locator('.copy-pick');
                if(await copy.count()){await copy.first().click();assert.ok(await page.locator('input[name="book"]').inputValue());}
                const after=await page.request.get(url+'/api/board');
                assert.equal((await before.json()).tickets.length,(await after.json()).tickets.length);
            }
            if(tab==='watch')assert.ok(await page.locator('#watch-cards > article').count()<=5);
            const text=await page.locator('#'+tab).innerText();
            for(const token of forbidden)assert.ok(!text.includes(token),tab+': forbidden text '+token);
            assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth),false,tab+': horizontal overflow');
            await page.screenshot({path:out+'/'+tab+'-desktop.png',fullPage:true});
        }
        for(const width of [900,390,320]){
            await page.setViewportSize({width,height:900});
            for(const tab of ['live','players','watch','picks','tickets','intelligence','heat','math']){
                await page.locator('[data-tab="'+tab+'"]').click();
                assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth),false,tab+' overflow at '+width);
                await page.screenshot({path:out+'/'+tab+'-'+width+'.png',fullPage:true});
            }
        }
        assert.deepEqual(errors,[]);
        fs.writeFileSync(out+'/result.json',JSON.stringify({public_http:200,tabs:8,responsive_widths:[1440,900,390,320],errors},null,2));
        console.log('PASS: public HTTP 200, eight tabs, four desks, owner-only tickets, no debug/legacy text, desktop/tablet/mobile overflow checks');
    }finally{await browser.close();}
})().catch(error=>{console.error(error.message.slice(0,1500));process.exitCode=1;});
