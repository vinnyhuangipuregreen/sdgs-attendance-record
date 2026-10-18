const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../templates/admin.html'), 'utf8');
const actions = source.slice(source.indexOf('function closeCourseModal()'), source.indexOf('// 管理者帳號操作'));

function setup(ok = true) {
  const calls = [];
  const course = {id:1, title:'Updated', course_date:'2026/10/18', course_time:'10:00-11:00', open_time:'2026-10-18 10:00:00'};
  const button = {};
  const row = {cells:Array.from({length:6}, () => ({})), querySelector:() => button, remove() { row.removed = true; }};
  const elements = {courseCount:{}, 'course-row-1':row, courseModal:{classList:{add:() => calls.push('close')}}};
  for (const [id,value] of Object.entries({modalCourseId:'1',modalCourseTitle:course.title,modalCourseDate:course.course_date,modalCourseTime:course.course_time,modalCourseOpenTime:course.open_time})) elements[id] = {value};
  const context = vm.createContext({document:{getElementById:id => elements[id], querySelectorAll:() => row.removed ? [] : [row]},
    fetch:async () => {calls.push('fetch'); return {ok,json:async () => ({course})};},
    location:{reload:() => calls.push('reload')}, confirm:() => true, alert:() => calls.push('error')});
  vm.runInContext(actions, context);
  return {context,calls,row,button,elements,course};
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
