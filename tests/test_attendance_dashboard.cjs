const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function setup(ok = true, status = 200) {
  const elements = {}, calls = [], alerts = [];
  const element = id => elements[id] ||= {value:'',textContent:'',classList:{remove:()=>calls.push('open'),add(){}},
    addEventListener(type,callback){this.close=callback;},showModal(){calls.push('confirm');}};
  const context = vm.createContext({document:{getElementById:element,querySelector:()=>null,querySelectorAll:()=>[],addEventListener(){}},
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

test('checkin before the window shows its exact message without confirmation or a write', async () => {
  const env = setup();
  const button = {disabled:false, dataset:{checkinStart:'2026-10-23T18:00:00+08:00',checkinEnd:'2026-10-23T22:00:00+08:00',checkinMessage:'吉祥！2026/10/23 測試課程的簽到區間為：18:00-22:00。'}};
  env.context.document.querySelector = () => button;
  env.context.Date = {now:()=>Date.parse('2026-10-23T17:59:59+08:00'),parse:Date.parse};
  await env.context.doCheckin(1);
  assert.deepEqual(env.calls, []);
  assert.equal(env.alerts[0], button.dataset.checkinMessage);
});

test('an open dashboard greys and disables checkin when its window ends', async () => {
  const env = setup();
  const button = {disabled:false,dataset:{checkinEnd:'2026-10-23T22:00:00+08:00'}};
  env.context.document.querySelectorAll = () => [button];
  env.context.refreshCheckinButtons(Date.parse('2026-10-23T22:00:00+08:00'));
  assert.equal(button.disabled,false);
  env.context.refreshCheckinButtons(Date.parse('2026-10-23T22:00:01+08:00'));
  assert.equal(button.disabled,true);
  assert.match(button.className,/bg-gray-100/);
  env.context.document.querySelector = () => button;
  await env.context.doCheckin(1);
  assert.deepEqual(env.calls, []);
});
