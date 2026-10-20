const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../templates/course_attendance.html'), 'utf8');

test('export link includes the current filters and preserves the embedded admin path', () => {
  const fields = {filterName: ' 學員乙 ', filterEmail: 'makeup@example.invalid', filterPhone: '0912', filterStatus: 'SIGNED_IN'};
  const context = vm.createContext({URL, document: {getElementById: id => ({value: fields[id]})}});
  vm.runInContext(source.slice(source.indexOf('function updateAttendanceExport('), source.indexOf('function applyFilter(')), context);
  const link = {href: 'https://example.invalid/attendance-admin/api/admin/courses/1/attendance/export'};
  context.updateAttendanceExport(link);
  const url = new URL(link.href);
  assert.equal(url.pathname, '/attendance-admin/api/admin/courses/1/attendance/export');
  assert.equal(url.searchParams.get('name'), '學員乙');
  assert.equal(url.searchParams.get('email'), fields.filterEmail);
  assert.equal(url.searchParams.get('phone'), '0912');
  assert.equal(url.searchParams.get('status'), 'SIGNED_IN');
  fields.filterName = '';
  fields.filterStatus = 'ALL';
  context.updateAttendanceExport(link);
  assert.equal(new URL(link.href).searchParams.get('name'), '');
  assert.equal(new URL(link.href).searchParams.getAll('status').length, 1);
  assert.equal(new URL(link.href).searchParams.get('status'), 'ALL');
});

test('leave export includes current compound filters and works on empty rosters', () => {
  const template = fs.readFileSync(require('node:path').join(__dirname, '../templates/course_leaves.html'), 'utf8');
  const fields = {filterName: ' 學員乙 ', filterEmail: 'makeup@example.invalid', filterPhone: '0912'};
  const context = vm.createContext({URL, document: {getElementById: id => ({value: fields[id]})}});
  vm.runInContext(template.slice(template.indexOf('function updateLeaveExport('), template.indexOf('function applyFilter(')), context);
  const link = {href: 'https://example.invalid/attendance-admin/api/admin/courses/1/leaves/export'};
  context.updateLeaveExport(link);
  const url = new URL(link.href);
  assert.equal(url.pathname, '/attendance-admin/api/admin/courses/1/leaves/export');
  assert.equal(url.searchParams.get('name'), '學員乙');
  assert.equal(url.searchParams.get('email'), fields.filterEmail);
  assert.equal(url.searchParams.get('phone'), fields.filterPhone);
  assert.ok(template.indexOf('id="exportLeavesBtn"') < template.indexOf('{% if total_count == 0 %}'));
});

test('makeup export passes all compound filters and preserves the embedded download path', () => {
  const template = fs.readFileSync(require('node:path').join(__dirname, '../templates/course_pathdemy.html'), 'utf8');
  const fields = {searchName: ' 學員乙 ', searchEmail: 'makeup@example.invalid', searchPhone: '0912', searchStartDate: '2026-09-20', searchEndDate: '2026-09-21'};
  const context = vm.createContext({URL, document: {getElementById: id => ({value: fields[id]})}});
  vm.runInContext(template.slice(template.indexOf('function updateMakeupExport('), template.indexOf('function filterRecords(')), context);
  const link = {href: 'https://example.invalid/attendance-admin/api/admin/courses/1/pathdemy/export'};
  context.updateMakeupExport(link);
  const url = new URL(link.href);
  assert.equal(url.pathname, '/attendance-admin/api/admin/courses/1/pathdemy/export');
  assert.deepEqual(Object.fromEntries(url.searchParams), {name: '學員乙', email: fields.searchEmail, phone: '0912', start_date: '2026-09-20', end_date: '2026-09-21'});
  fields.searchStartDate = '';
  context.updateMakeupExport(link);
  assert.equal(new URL(link.href).searchParams.get('start_date'), '');
  assert.equal(new URL(link.href).searchParams.getAll('end_date').length, 1);
  assert.ok(template.indexOf('id="exportMakeupBtn"') < template.indexOf('id="contentSection"'));
});
