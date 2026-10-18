const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function setup(ok = true, status = 200) {
  const elements = {}, calls = [], alerts = [];
  const element = id => elements[id] ||= {value:'',textContent:'',classList:{remove:()=>calls.push('open'),add(){}},
    addEventListener(type,callback){this.close=callback;},showModal(){calls.push('confirm');}};
  const context = vm.createContext({document:{getElementById:element},
    fetch:async (path,options)=>{calls.push({path,payload:JSON.parse(options.body)});return {ok,status,json:async()=>({detail:'吉祥！只能在課程時間進行簽到。'})};},
    alert:text=>alerts.push(text),location:{reload:()=>calls.push('reload')}});
  vm.runInContext(fs.readFileSync(require('node:path').join(__dirname,'../static/js/attendance-dashboard.js'),'utf8'),context);
  return {context,element,calls,alerts,confirm(accept){const dialog=element('attendanceConfirmDialog');dialog.returnValue=accept?'confirm':'cancel';dialog.close();}};
}

test('leave requires the requested confirmation and a nonblank reason', async()=>{
  const env=setup();
  let task=env.context.openLeaveModal(1,'課程');
  assert.equal(env.element('attendanceConfirmMessage').textContent,'確定要[請假]?一旦送出就不可修改！');
  env.confirm(false);await task;
  assert.deepEqual(env.calls,['confirm']);
  task=env.context.openLeaveModal(1,'課程');env.confirm(true);await task;
  assert.equal(env.element('leaveCourseTitle').textContent,'課程');
  env.element('leaveReason').value='  ';
  await env.context.submitLeave();
  assert.equal(env.alerts[0],'請填寫請假原因！');
  assert.equal(env.calls.filter(call=>call.path).length,0);
  env.element('leaveReason').value='請假事由';
  await env.context.submitLeave();
  assert.equal(env.calls.find(call=>call.path).payload.reason,'請假事由');
});

test('makeup cancellation never submits and confirmation uses the exact wording', async()=>{
  const env=setup();
  const task=env.context.doMakeup(1);
  assert.equal(env.element('attendanceConfirmMessage').textContent,'確定已[補課完成]?');
  env.confirm(false);await task;
  assert.deepEqual(env.calls,['confirm']);
});

test('server time rejection is displayed and the stale dashboard refreshes', async()=>{
  const env=setup(false,409);
  const task=env.context.doCheckin(1);env.confirm(true);await task;
  assert.equal(env.alerts[0],'吉祥！只能在課程時間進行簽到。');
  assert.equal(env.calls.at(-1),'reload');
});

test('successful makeup submits only after confirmation and reloads persisted timestamps', async()=>{
  const env=setup();
  const task=env.context.doMakeup(2);
  assert.equal(env.calls.length,1);
  env.confirm(true);await task;
  assert.equal(env.calls[1].path,'/api/attendance/makeup');
  assert.equal(env.calls[1].payload.course_id,2);
  assert.equal(env.alerts[0],'🎉 補課登錄成功！');
  assert.equal(env.calls.at(-1),'reload');
});
