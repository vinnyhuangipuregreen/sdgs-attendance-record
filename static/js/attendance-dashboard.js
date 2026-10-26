function confirmAttendance(message) {
  const dialog = document.getElementById('attendanceConfirmDialog');
  document.getElementById('attendanceConfirmMessage').textContent = message;
  dialog.returnValue = '';
  return new Promise(resolve => {
    dialog.addEventListener('close', () => resolve(dialog.returnValue === 'confirm'), {once:true});
    dialog.showModal();
  });
}

async function sendAttendance(action, courseId, message, reason) {
  try {
    const response = await fetch((document.body?.dataset.attendanceApi || '/api/attendance/') + action, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({course_id:courseId, reason})
    });
    const result = await response.json();
    if (!response.ok) {
      alert(result.detail || result.error || '登記失敗，請稍後重試');
      if (response.status === 409) document.body?.dataset.attendanceReload ? location.replace(document.body.dataset.attendanceReload) : location.reload();
      return;
    }
    alert(message);
    document.body?.dataset.attendanceReload ? location.replace(document.body.dataset.attendanceReload) : location.reload();
  } catch (error) {
    alert('網路連線異常，請稍後再試！');
  }
}

function refreshCheckinButtons(now = Date.now()) {
  document.querySelectorAll('[data-checkin-end]').forEach(button => {
    if (now > Date.parse(button.dataset.checkinEnd)) {
      button.disabled = true;
      button.className = 'w-full py-2.5 bg-gray-100 text-gray-400 text-sm font-medium rounded-xl cursor-not-allowed';
    }
  });
}

document.addEventListener('DOMContentLoaded', () => {
  refreshCheckinButtons();
  setInterval(refreshCheckinButtons, 1000);
});

async function doCheckin(courseId) {
  const button = document.querySelector(`[data-checkin-button="${courseId}"]`);
  if (button?.disabled) return;
  if (button?.dataset.checkinStart) {
    if (Date.now() > Date.parse(button.dataset.checkinEnd)) { refreshCheckinButtons(); return; }
    if (Date.now() < Date.parse(button.dataset.checkinStart)) return alert(button.dataset.checkinMessage);
  }
  if (!(await confirmAttendance('確定現在進行課程簽到？'))) return;
  await sendAttendance('checkin', courseId, '🎉 簽到成功！');
}

async function openLeaveModal(courseId, courseTitle) {
  if (!(await confirmAttendance('確定要[請假]?一旦送出就不可修改！'))) return;
  document.getElementById('leaveCourseId').value = courseId;
  document.getElementById('leaveCourseTitle').textContent = courseTitle;
  document.getElementById('leaveReason').value = '';
  document.getElementById('leaveModal').classList.remove('hidden');
}

function closeLeaveModal() {
  document.getElementById('leaveModal').classList.add('hidden');
}

async function submitLeave() {
  const reason = document.getElementById('leaveReason').value.trim();
  if (!reason) return alert('請填寫請假原因！');
  await sendAttendance('leave', document.getElementById('leaveCourseId').value, '已完成請假登記！', reason);
}

async function doMakeup(courseId) {
  if (!(await confirmAttendance('確定已[補課完成]?'))) return;
  await sendAttendance('makeup', courseId, '🎉 補課登錄成功！');
}
