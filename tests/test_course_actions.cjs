const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../templates/admin.html'), 'utf8');
const actions = source.slice(source.indexOf('function closeCourseModal()'), source.indexOf('// 管理者帳號操作'));

function setup(ok = true) {
  const calls = [];
  const course = {id:1, title:'Updated', course_date:'2026/10/18', course_time:'10:00-11:00', open_time:'2026-10-18 10:00:00',checkin_start:'10:00',checkin_end:'11:00'};
  const button = {};
  const row = {dataset:{recordCounts:'{}'}, cells:Array.from({length:9}, () => ({})), querySelector:() => button, remove() { row.removed = true; }};
  const elements = {courseCount:{}, 'course-row-1':row, courseModal:{classList:{add:() => calls.push('close')}}};
  for (const [id,value] of Object.entries({modalCourseId:'1',modalCourseTitle:course.title,modalCourseDate:course.course_date,modalCourseStart:'10:00',modalCourseEnd:'11:00',modalCheckinStart:'10:00',modalCheckinEnd:'11:00'})) elements[id] = {value};
  const alerts = [];
  const context = vm.createContext({document:{getElementById:id => elements[id], querySelectorAll:() => row.removed ? [] : [row]},
    fetch:async () => {calls.push('fetch'); return {ok,json:async () => ({course})};},
    location:{reload:() => calls.push('reload')}, confirm:() => true, alert:text => {alerts.push(text); calls.push('error');}});
  vm.runInContext(actions, context);
  return {context,calls,row,button,elements,course,alerts};
}

test('editing updates the existing row without another page load', async () => {
  const {context,calls,row,button,course} = setup();
  await context.saveCourse();
  assert.deepEqual(calls, ['fetch','close']);
  assert.equal(row.cells[1].textContent, course.title);
  assert.equal(typeof button.onclick, 'function');
});

test('deleting removes the row and updates the count without reloading', async () => {
  const {context,calls,row,elements} = setup();
  await context.deleteCourse(1);
  assert.deepEqual(calls, ['fetch']);
  assert.equal(row.removed, true);
  assert.equal(elements.courseCount.textContent, '共 0 堂課程');
});

test('failed writes keep the course row and report failure', async () => {
  const {context,calls,row} = setup(false);
  await context.saveCourse();
  await context.deleteCourse(1);
  assert.deepEqual(calls, ['fetch','error','fetch','error']);
  assert.equal(row.removed, undefined);
  assert.equal(row.cells[1].textContent, undefined);
});

test('recorded courses show only positive counts without sending a delete request', async () => {
  const {context,calls,row,alerts} = setup();
  row.dataset.recordCounts = JSON.stringify({SIGNED_IN:3,ON_LEAVE:0,MAKEUP_DONE:2});
  await context.deleteCourse(1);
  assert.deepEqual(calls, ['error']);
  assert.equal(alerts[0], '因為有以下紀錄，故不可刪除該筆資料：簽到：3筆、補課：2筆。');
  assert.equal(row.removed, undefined);
});

test('a stale empty row displays the server rejection instead of removing the course', async () => {
  const {context,row,alerts} = setup();
  context.fetch = async () => ({ok:false,json:async () => ({detail:'因為有以下紀錄，故不可刪除該筆資料：請假：1筆。'})});
  await context.deleteCourse(1);
  assert.match(alerts[0], /請假：1筆/);
  assert.equal(row.removed, undefined);
});

test('embedded edit modal fits the visible parent viewport and adapts to resizing', () => {
  const modal = {classList:{contains:() => false},style:{},firstElementChild:{style:{}}};
  let frameTop = 500;
  const frame = {getBoundingClientRect:() => ({top:frameTop}),scrollIntoView:() => {frameTop = 0;}};
  const context = vm.createContext({document:{getElementById:() => modal},window:{innerHeight:1100,parent:{innerHeight:717},frameElement:frame}});
  const start = source.indexOf('function fitCourseModal()');
  vm.runInContext(source.slice(start, source.indexOf('    if (window.parent !== window) {', start)), context);
  context.fitCourseModal();
  assert.equal(modal.style.height, '717px');
  assert.equal(modal.firstElementChild.style.maxHeight, '693px');
  context.window.parent.innerHeight = 400;
  context.fitCourseModal();
  assert.equal(modal.style.height, '400px');
  assert.equal(modal.firstElementChild.style.maxHeight, '376px');
});
