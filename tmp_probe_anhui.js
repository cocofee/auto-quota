const fs=require('fs');
const files=['tmp_review_items_3300.json','tmp_openclaw_review_draft.json','tmp_openclaw_review_confirm.json','tmp_route_task.json','tmp_route.json'];
for(const p of files){
  try{
    const s=fs.readFileSync(p).toString('utf16le');
    const idx=s.indexOf('安徽');
    if(idx>=0){
      console.log('FOUND',p,'idx',idx);
      console.log(s.slice(Math.max(0,idx-200), idx+1000));
    }
  }catch(e){}
}
const s=fs.readFileSync('tmp_review_items_3300.json').toString('utf16le');
let count=0;
for(const block of s.split('{"id":').slice(1)){
  if(count>=20) break;
  const id=(block.match(/^"([^"]+)"/)||[])[1]||'';
  const bill=(block.match(/"bill_name":"([^"]*)"/)||[])[1]||'';
  const province=(block.match(/"province":"([^"]*)"/)||[])[1]||'';
  const unified=(block.match(/"province"\s*:\s*"([^"]*)"/)||[])[1]||'';
  if(/安徽/.test(block)){
    console.log('ITEM_WITH_ANHUI',id,bill,province||unified);
    count++;
  }
}
console.log('done');
