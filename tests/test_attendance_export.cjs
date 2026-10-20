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
