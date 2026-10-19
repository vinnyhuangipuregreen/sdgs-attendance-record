const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {test}=require('node:test');
test('embedded srcdoc course forms use the inherited document base and retain their rendered year',async()=>{
  const template=fs.readFileSync(path.join(__dirname,'../templates/platform-footer.html'),'utf8');
  const script=template.match(/<script>([\s\S]*?)<\/script>/)[1].replace(/const renderedYear = .*?;/,'const renderedYear = 2;');
  const calls=[];
  const context=vm.createContext({URL,Headers,document:{baseURI:'https://sdgs-action-platform.vercel.app/#admin'},location:{href:'about:srcdoc',origin:'null'},window:{fetch:async(input,options)=>calls.push({input,options})}});
  vm.runInContext(script,context);
  await context.window.fetch('/attendance-admin/api/admin/courses',{method:'POST',headers:{'Content-Type':'application/json'}});
  assert.equal(calls[0].options.headers.get('X-Academic-Year'),'2');
  assert.equal(calls[0].options.headers.get('Content-Type'),'application/json');
});
