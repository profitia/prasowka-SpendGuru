'use strict';
// Run without dependencies: node --test scripts/test_frontend_recovery.cjs
const { test } = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const source = fs.readFileSync('js/app.js', 'utf8');
function app(fetch) {
  const elements = new Map();
  const storage = new Map();
  const ctx = vm.createContext({ console, fetch, AbortController, AbortSignal,
    setTimeout, clearTimeout, setInterval, clearInterval,
    window: { PRASOWKA_API_URL: 'https://api.example.invalid' },
    localStorage: { getItem: k => storage.get(k) ?? null, setItem: (k,v) => storage.set(k,String(v)), removeItem: k => storage.delete(k) },
    document: { addEventListener() {}, getElementById: id => {
      if (!elements.has(id)) elements.set(id, { textContent:'',innerHTML:'',value:'',dataset:{},classList:{ add(){},remove(){} } });
      return elements.get(id);
    } },
  });
  vm.runInContext(source, ctx);
  vm.runInContext(`buildFilterOptions = () => {}; applyAndRender = () => {}; preloadCampaignHistory = () => {}; showToast = () => {}; showHistoryForEmail = () => {};`, ctx);
  return { ctx, storage, elements, run: code => vm.runInContext(code, ctx) };
}
const response = (data, ok=true) => ({ ok, status:ok?200:503, json:async()=>data, text:async()=>'' });
const article = { id:'test', source_url:'https://example.invalid/article', tier1_person:'Operator', tier1_email:'old@example.invalid', apollo_status:'waiting' };
function prepare(a) {
  a.ctx.fixture = article;
  a.run(`allArticles = [{...fixture}]; apiAvailable = true; saveContact('test',1,allArticles[0],fixture.tier1_email,'waiting');`);
  a.elements.set('email_test_t1', {value:'draft@example.invalid',classList:{add(){},remove(){}}});
  a.ctx.event = {target:{closest:()=>({dataset:{articleId:'test',tier:'1'}})}};
}
test('snapshot renders before cold API and recovers to live DB data', async()=>{
  let release;
  const a=app(url => url.startsWith('https:') ? new Promise(r=>release=r) : Promise.resolve(response([{id:'snapshot'}])));
  const loading = a.run('loadArticles()');
  await new Promise(setImmediate);
  assert.equal(a.run('allArticles[0].id'), 'snapshot');
  assert.equal(a.run('apiAvailable'), false);
  release(response([{id:'live'}]));
  await loading;
  assert.equal(a.run('allArticles[0].id'), 'live');
  assert.equal(a.run('apiAvailable'), true);
});
test('503 keeps the snapshot and blocks writes, then a read reconnects', async()=>{
  let live=false;
  const a=app(url => Promise.resolve(response(url.startsWith('https:') ? [{id:'live'}]:[{id:'snapshot'}], !url.startsWith('https:') || live)));
  await a.run('loadArticles(3)');
  assert.equal(a.run('allArticles[0].id'), 'snapshot');
  assert.equal(a.run('canWrite()'), false);
  assert.equal(a.elements.get('retryApiBtn').hidden,false);
  live=true;
  await a.run('loadArticles(3)');
  assert.equal(a.run('apiAvailable'),true);
});
test('failed contact write retains draft without changing confirmed email', async()=>{
  let calls=0;
  const a=app(async()=>{calls++;return response({},false)}); prepare(a);
  await a.run('handleSaveEmail(event)');
  assert.equal(a.run("getContactEmail('test',1)"),'old@example.invalid');
  assert.equal(a.storage.get('prasowka_draft_test_t1'),'draft@example.invalid');
  assert.match(a.elements.get('save_confirm_test_t1').textContent,/Brak potwierdzenia/);
  assert.equal(calls,1);
  a.run('syncDbContactsToLocalStorage()');
  assert.equal(a.storage.get('prasowka_draft_test_t1'),'draft@example.invalid');
});
test('successful contact write updates confirmed cache and clears draft',async()=>{
  const a=app(async()=>response({apollo_status:'waiting'}));prepare(a);
  await a.run('handleSaveEmail(event)');
  assert.equal(a.run("getContactEmail('test',1)"),'draft@example.invalid');
  assert.equal(a.storage.has('prasowka_draft_test_t1'),false);
});
test('failed acceptance keeps article quality and allows retry',async()=>{
  const a=app(async()=>response({},false));prepare(a);
  a.run("allArticles[0].data_quality_status='needs_review'");
  const button={dataset:{articleId:'test',articleUrl:article.source_url},disabled:false};
  a.ctx.acceptEvent={target:{closest:()=>button}};
  await a.run('handleAcceptArticle(acceptEvent)');
  assert.equal(a.run('allArticles[0].data_quality_status'),'needs_review');
  assert.equal(button.disabled,false);
});
test('timed-out writes are attempted once and report failure',async()=>{
  let calls=0;
  const a=app(async()=>{calls++;const e=new Error('timeout');e.name='AbortError';throw e});
  const result=await a.run("postToApi('/api/articles/contact', {})");
  assert.equal(result.ok,false); assert.equal(calls,1);
});
