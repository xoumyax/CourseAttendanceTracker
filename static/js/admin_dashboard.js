// Admin Dashboard JavaScript

// Check if admin is logged in
if (!localStorage.getItem('token') || localStorage.getItem('userType') !== 'admin') {
    window.location.href = '/admin/login';
}

const alertBox = document.getElementById('alertBox');
const userInfo = JSON.parse(localStorage.getItem('userInfo'));
document.getElementById('adminName').textContent = `Admin: ${userInfo.username}`;
const authHeaders = () => ({ 'Authorization': `Bearer ${localStorage.getItem('token')}` });

// Load dashboard on page load
window.addEventListener('load', () => {
    loadDashboard();
    loadSessions();
    loadSettings();

    // Auto-refresh grades every 30 seconds
    setInterval(loadStudentGrades, 30000);
});

// Load dashboard statistics
async function loadDashboard() {
    try {
        const response = await fetch('/api/admin/dashboard', {
            headers: {
                'Authorization': `Bearer ${localStorage.getItem('token')}`
            }
        });
        
        const data = await response.json();
        
        if (response.ok) {
            document.getElementById('totalStudents').textContent = data.total_students;
            document.getElementById('registeredStudents').textContent = `${data.total_registered_students} registered`;
            document.getElementById('totalSessions').textContent = data.total_sessions;
            document.getElementById('totalAttendances').textContent = data.total_attendances;
            
            // Display recent attendances
            displayRecentAttendances(data.recent_attendances);
        } else {
            showAlert('Failed to load dashboard', 'error');
        }
    } catch (error) {
        showAlert('Network error', 'error');
        console.error('Dashboard error:', error);
    }
}

// Display recent attendances
function displayRecentAttendances(attendances) {
    const container = document.getElementById('recentAttendances');
    
    if (attendances.length === 0) {
        container.innerHTML = '<p class="empty-state">No recent attendances</p>';
        return;
    }
    
    let html = '<table><thead><tr><th>Student</th><th>Student ID</th><th>Session Date</th><th>Marked At</th></tr></thead><tbody>';
    
    attendances.forEach(att => {
        const sessionDate = fmtDate(att.session_date);
        const markedAt = fmtDateTime(att.marked_at);
        
        html += `
            <tr>
                <td>${escapeHtml(att.student_name)}</td>
                <td>${escapeHtml(att.student_id)}</td>
                <td>${sessionDate}</td>
                <td>${markedAt}</td>
            </tr>
        `;
    });
    
    html += '</tbody></table>';
    container.innerHTML = html;
}

// Load sessions for token generation
async function loadSessions() {
    try {
        const response = await fetch('/api/admin/sessions', {
            headers: {
                'Authorization': `Bearer ${localStorage.getItem('token')}`
            }
        });
        
        const sessions = await response.json();
        const select = document.getElementById('tokenSessionSelect');
        select.innerHTML = '<option value="">-- Select a session --</option>';
        
        sessions.forEach(session => {
            const option = document.createElement('option');
            option.value = session.id;
            option.textContent = `Session ${session.id} - ${session.date}`;
            
            if (session.is_test_session) {
                option.textContent += ' (TEST)';
            }
            
            select.appendChild(option);
        });
    } catch (error) {
        showAlert('Error loading sessions', 'error');
        console.error('Load sessions error:', error);
    }
}

// Create test sessions
async function createTestSessions() {
    if (!confirm('Create 2 test sessions for today?')) return;
    
    try {
        const response = await fetch('/api/admin/sessions/create-test', {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${localStorage.getItem('token')}`
            }
        });
        
        const data = await response.json();
        
        if (response.ok) {
            showAlert(`✅ ${data.message}`, 'success');
            loadSessions();
            loadDashboard();
        } else {
            showAlert(errorText(data, 'Failed to create test sessions'), 'error');
        }
    } catch (error) {
        showAlert('Network error', 'error');
        console.error('Create test sessions error:', error);
    }
}

// Add one session on a chosen date
async function addSession() {
    const date = document.getElementById('newSessionDate').value;
    const isTest = document.getElementById('newSessionTest').checked;
    if (!date) {
        showAlert('Pick a date first', 'error');
        return;
    }

    try {
        const response = await fetch('/api/admin/sessions', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', ...authHeaders() },
            body: JSON.stringify({ date: date, is_test_session: isTest })
        });
        const data = await response.json();

        if (response.ok) {
            showAlert(`✅ Added ${isTest ? 'test ' : ''}session for ${escapeHtml(data.date)}`, 'success');
            loadSessions();
            loadDashboard();
        } else {
            showAlert(errorText(data, 'Failed to add session'), 'error');
        }
    } catch (error) {
        showAlert('Network error', 'error');
        console.error('Add session error:', error);
    }
}

// Load today's sessions
async function loadTodaySessions() {
    try {
        const response = await fetch('/api/admin/sessions/today', {
            headers: {
                'Authorization': `Bearer ${localStorage.getItem('token')}`
            }
        });
        
        const data = await response.json();
        const card = document.getElementById('todaySessionsCard');
        const list = document.getElementById('todaySessionsList');
        
        if (data.sessions && data.sessions.length > 0) {
            let html = '<table><thead><tr><th>Session ID</th><th>Type</th><th>Actions</th></tr></thead><tbody>';
            
            data.sessions.forEach(session => {
                const type = session.is_test_session ? '<span class="badge badge-warning">TEST</span>' : '<span class="badge badge-info">Regular</span>';
                html += `
                    <tr>
                        <td>${session.id}</td>
                        <td>${type}</td>
                        <td><button class="btn btn-success" onclick="quickGenerateToken(${session.id})">Generate Check-in Code</button></td>
                    </tr>
                `;
            });
            
            html += '</tbody></table>';
            list.innerHTML = html;
            card.style.display = 'block';
        } else {
            list.innerHTML = '<p class="empty-state">No sessions for today. Add one first!</p>';
            card.style.display = 'block';
        }
    } catch (error) {
        showAlert('Error loading today\'s sessions', 'error');
        console.error('Load today sessions error:', error);
    }
}

// Generate token
async function generateToken() {
    const sessionId = document.getElementById('tokenSessionSelect').value;
    
    if (!sessionId) {
        showAlert('Please select a session', 'error');
        return;
    }
    
    await quickGenerateToken(sessionId);
}

// Quick generate token
async function quickGenerateToken(sessionId) {
    try {
        const response = await fetch('/api/admin/tokens/generate', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${localStorage.getItem('token')}`
            },
            body: JSON.stringify({ session_id: parseInt(sessionId) })
        });
        
        const data = await response.json();
        
        if (response.ok) {
            document.getElementById('tokenDisplay').textContent = data.token;
            document.getElementById('tokenExpiry').textContent = fmtDateTime(data.expires_at);
            document.getElementById('tokenValidity').textContent = data.expiry_info;
            document.getElementById('generatedToken').style.display = 'block';
            
            showAlert(`✅ Check-in code generated: ${data.token}`, 'success');
        } else {
            showAlert(errorText(data, 'Failed to generate code'), 'error');
        }
    } catch (error) {
        showAlert('Network error', 'error');
        console.error('Generate token error:', error);
    }
}

// Load settings
async function loadSettings() {
    try {
        const response = await fetch('/api/admin/settings', {
            headers: {
                'Authorization': `Bearer ${localStorage.getItem('token')}`
            }
        });
        
        const data = await response.json();
        
        if (response.ok) {
            document.getElementById('timeRestrictionToggle').checked = data.disable_time_restrictions;
        }
    } catch (error) {
        console.error('Load settings error:', error);
    }
}

// Update settings
async function updateSettings() {
    const disableTimeRestrictions = document.getElementById('timeRestrictionToggle').checked;
    
    try {
        const response = await fetch('/api/admin/settings', {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${localStorage.getItem('token')}`
            },
            body: JSON.stringify({ disable_time_restrictions: disableTimeRestrictions })
        });
        
        const data = await response.json();
        
        if (response.ok) {
            showAlert(`✅ Time restrictions ${disableTimeRestrictions ? 'disabled' : 'enabled'}`, 'success');
        } else {
            showAlert('Failed to update settings', 'error');
        }
    } catch (error) {
        showAlert('Network error', 'error');
        console.error('Update settings error:', error);
    }
}

// Export to Excel (the server sends the .xlsx file; the browser saves it)
async function exportExcel() {
    try {
        const response = await fetch('/api/admin/export/excel', { headers: authHeaders() });
        if (!response.ok) {
            const data = await response.json().catch(() => null);
            showAlert(errorText(data, 'Failed to export Excel'), 'error');
            return;
        }
        const disposition = response.headers.get('Content-Disposition') || '';
        const match = disposition.match(/filename="([^"]+)"/);
        downloadBlob(await response.blob(), match ? match[1] : 'attendance_report.xlsx');
        showAlert('✅ Report downloaded', 'success');
    } catch (error) {
        showAlert('Network error', 'error');
        console.error('Export Excel error:', error);
    }
}

function downloadBlob(blob, filename) {
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
}

// ============================================================================
// Enrollment codes
// ============================================================================

// Ask the server for one-time codes. everyone=true: all students not registered yet.
async function generateCodes(everyone) {
    const ids = document.getElementById('codeStudentIds').value.split(/[\s,]+/).filter(Boolean);
    if (!everyone && ids.length === 0) {
        showAlert('Enter at least one student ID', 'error');
        return;
    }
    await requestCodes(everyone ? { all_unregistered: true } : { student_ids: ids });
}

// Issue a fresh code for one student (registration or password reset)
async function reissueCode(studentId) {
    await requestCodes({ student_ids: [studentId] });
}

async function postCodes(body) {
    const response = await fetch('/api/admin/codes', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify(body)
    });
    return { ok: response.ok, data: await response.json() };
}

// First ask what would happen. If it would void codes already handed out, say how many and
// ask before replacing them.
async function requestCodes(body) {
    try {
        const preview = await postCodes({ ...body, dry_run: true });
        if (!preview.ok) {
            showAlert(errorText(preview.data, 'Failed to generate codes'), 'error');
            return;
        }
        const plan = preview.data;
        let replace = false;
        if (plan.skipped_outstanding > 0) {
            replace = confirm(
                `${plan.skipped_outstanding} of these students already have an unused code. ` +
                `Replacing them will VOID those ${plan.skipped_outstanding} code(s) that were already handed out.\n\n` +
                'OK = replace them too.  Cancel = keep them and only issue codes to the others.');
        }
        const expected = plan.would_issue + (replace ? plan.skipped_outstanding : 0);
        if (expected === 0) {
            showAlert('No codes to issue (everyone already has an unused code or is registered).', 'error');
            return;
        }
        const result = await postCodes({ ...body, replace_outstanding: replace });
        if (!result.ok) {
            showAlert(errorText(result.data, 'Failed to generate codes'), 'error');
            return;
        }
        showGeneratedCodes(result.data);
    } catch (error) {
        showAlert('Network error', 'error');
        console.error('Codes error:', error);
    }
}

function csvCell(value) {
    // Quote every cell; neutralise spreadsheet formulas.
    let text = String(value);
    if (/^[=+\-@\t\r]/.test(text)) text = "'" + text;
    return '"' + text.replace(/"/g, '""') + '"';
}

function showGeneratedCodes(data) {
    const box = document.getElementById('generatedCodes');
    box.style.display = 'block';
    const missing = data.unknown_student_ids.length
        ? `<p style="color: #dc3545;">Not found: ${data.unknown_student_ids.map(escapeHtml).join(', ')}</p>` : '';
    if (data.codes.length === 0) {
        box.innerHTML = missing + '<p class="empty-state">No codes issued (everyone is already registered).</p>';
        return;
    }
    const rows = data.codes.map(c =>
        `<tr><td>${escapeHtml(c.student_id)}</td><td>${escapeHtml(c.name)}</td><td><strong>${escapeHtml(c.code)}</strong></td></tr>`
    ).join('');
    box.innerHTML = `
        ${missing}
        <p><strong>${data.codes.length}</strong> code(s) issued. They cannot be shown again.</p>
        <button class="btn btn-success" id="downloadCodesBtn">Download codes.csv</button>
        <table><thead><tr><th>Student ID</th><th>Name</th><th>Code</th></tr></thead><tbody>${rows}</tbody></table>`;
    document.getElementById('downloadCodesBtn').addEventListener('click', () => {
        const lines = [['student_id', 'name', 'code'], ...data.codes.map(c => [c.student_id, c.name, c.code])]
            .map(row => row.map(csvCell).join(','));
        downloadBlob(new Blob([lines.join('\n') + '\n'], { type: 'text/csv' }), 'codes.csv');
    });
    loadStudentGrades();
}

// View token history
async function viewTokenHistory() {
    const modal = document.getElementById('tokenHistoryModal');
    const content = document.getElementById('tokenHistoryContent');
    
    modal.style.display = 'block';
    content.innerHTML = '<div class="spinner"></div>';
    
    try {
        const response = await fetch('/api/admin/tokens/history', {
            headers: {
                'Authorization': `Bearer ${localStorage.getItem('token')}`
            }
        });
        
        const data = await response.json();
        
        if (response.ok) {
            if (data.tokens.length === 0) {
                content.innerHTML = '<p class="empty-state">No check-in codes generated yet</p>';
                return;
            }
            
            let html = `
                <p style="color: #666; margin-bottom: 15px;">Total check-in codes generated: ${data.total}</p>
                <table>
                    <thead>
                        <tr>
                            <th>Session ID</th>
                            <th>Date</th>
                            <th>Type</th>
                            <th>Code</th>
                            <th>Generated At</th>
                            <th>Expires At</th>
                            <th>Status</th>
                        </tr>
                    </thead>
                    <tbody>
            `;
            
            data.tokens.forEach(token => {
                const sessionDate = fmtDate(token.session_date);
                const createdAt = fmtDateTime(token.created_at);
                const expiresAt = fmtDateTime(token.expires_at);
                const sessionType = token.is_test_session ? 
                    '<span class="badge badge-warning">TEST</span>' : 
                    '<span class="badge badge-info">Regular</span>';
                
                let statusBadge;
                if (token.is_expired) {
                    statusBadge = '<span class="badge badge-danger">Expired</span>';
                } else if (token.is_active) {
                    statusBadge = '<span class="badge badge-success">Active</span>';
                } else {
                    statusBadge = '<span class="badge">Inactive</span>';
                }
                
                html += `
                    <tr>
                        <td>${token.session_id}</td>
                        <td>${sessionDate}</td>
                        <td>${sessionType}</td>
                        <td><strong>${token.token}</strong></td>
                        <td>${createdAt}</td>
                        <td>${expiresAt}</td>
                        <td>${statusBadge}</td>
                    </tr>
                `;
            });
            
            html += '</tbody></table>';
            content.innerHTML = html;
        } else {
            content.innerHTML = '<p class="empty-state">Failed to load token history</p>';
        }
    } catch (error) {
        content.innerHTML = '<p class="empty-state">Network error</p>';
        console.error('Token history error:', error);
    }
}

// Close token history modal
function closeTokenHistory() {
    document.getElementById('tokenHistoryModal').style.display = 'none';
}

// Close token history modal
function closeTokenHistory() {
    document.getElementById('tokenHistoryModal').style.display = 'none';
}

// ============================================================================
// Live Student Grades
// ============================================================================

let allStudentGrades = [];

// Load student grades
async function loadStudentGrades() {
    try {
        const response = await fetch('/api/admin/students/grades', {
            headers: {
                'Authorization': `Bearer ${localStorage.getItem('token')}`
            }
        });
        
        const data = await response.json();
        
        if (response.ok) {
            allStudentGrades = data;
            displayStudentGrades(data);
            updateAverageStats(data);
        } else {
            document.getElementById('studentGradesList').innerHTML = '<p class="empty-state">Failed to load grades</p>';
        }
    } catch (error) {
        document.getElementById('studentGradesList').innerHTML = '<p class="empty-state">Network error</p>';
        console.error('Grades error:', error);
    }
}

// Grade tiers come from the server's GRADE_SCALE (highest threshold first).
const TIER_CLASSES = ['excellent', 'good', 'average', 'poor'];

function gradeTiers() {
    return publicConfig ? publicConfig.grade_scale : [];
}

function tierIndex(percentage) {
    const tiers = gradeTiers();
    const i = tiers.findIndex(t => percentage >= t.min_percent);
    return i === -1 ? tiers.length - 1 : i;
}

function maxPoints() {
    return publicConfig ? publicConfig.max_points : '?';
}

// Display student grades
function displayStudentGrades(students) {
    const container = document.getElementById('studentGradesList');

    if (students.length === 0) {
        container.innerHTML = '<p class="empty-state" style="padding: 2rem; text-align: center;">No students found</p>';
        return;
    }

    let html = '';
    students.forEach(student => {
        const gradeClass = getGradeClass(student);
        const gradeLabel = student.is_registered ? `${student.grade_points} pts` : 'Not Registered';
        const id = escapeHtml(student.student_id);

        html += `
            <div class="student-grade-card ${!student.is_registered ? 'unregistered' : ''}">
                <div class="student-header">
                    <div>
                        <div class="student-name">${escapeHtml(student.name)}</div>
                        <span class="student-uin">ID: ${id}</span>
                    </div>
                    <div class="grade-badge ${gradeClass}">${gradeLabel}</div>
                </div>
                <div class="student-stats">
                    <div class="stat-item">
                        <span class="label">Attended</span>
                        <span class="value">${student.attended_sessions}/${student.total_sessions}</span>
                    </div>
                    <div class="stat-item">
                        <span class="label">Percentage</span>
                        <span class="value">${student.attendance_percentage.toFixed(1)}%</span>
                    </div>
                    <div class="stat-item">
                        <span class="label">Grade</span>
                        <span class="value">${student.grade_points}/${maxPoints()}</span>
                    </div>
                </div>
                <div class="attendance-bar">
                    <div class="attendance-fill" style="width: ${student.attendance_percentage}%"></div>
                </div>
                <button class="btn-refresh" style="margin-top: 8px;" data-student-id="${id}" onclick="reissueCode(this.dataset.studentId)">
                    ${student.is_registered ? 'Reissue code (password reset)' : 'Issue code'}
                </button>
            </div>
        `;
    });

    container.innerHTML = html;
}

// CSS class for a student's grade badge
function getGradeClass(student) {
    if (!student.is_registered) return 'unregistered';
    return TIER_CLASSES[Math.min(tierIndex(student.attendance_percentage), TIER_CLASSES.length - 1)];
}

// Fill the grade filter from the grade scale
function buildGradeFilter() {
    const select = document.getElementById('gradeFilter');
    const fixed = '<option value="all">All Students</option><option value="registered">Registered Only</option><option value="unregistered">Unregistered</option>';
    const tiers = gradeTiers().map((t, i) =>
        `<option value="tier:${i}">${escapeHtml(t.min_percent)}% or more (${escapeHtml(t.points)} pts)</option>`
    ).join('');
    select.innerHTML = fixed + tiers;
}
loadConfig().then(() => { buildGradeFilter(); loadStudentGrades(); });

// Update average statistics
function updateAverageStats(students) {
    if (students.length === 0) {
        document.getElementById('avgAttendance').textContent = '0%';
        document.getElementById('avgGrade').textContent = '0';
        return;
    }
    
    const totalAttendance = students.reduce((sum, s) => sum + s.attendance_percentage, 0);
    const totalGrade = students.reduce((sum, s) => sum + s.grade_points, 0);
    
    const avgAttendance = (totalAttendance / students.length).toFixed(1);
    const avgGrade = (totalGrade / students.length).toFixed(1);
    
    document.getElementById('avgAttendance').textContent = `${avgAttendance}%`;
    document.getElementById('avgGrade').textContent = `${avgGrade}/${maxPoints()}`;
}

// Filter students
function filterStudents() {
    const searchTerm = document.getElementById('studentSearch').value.toLowerCase();
    const filterType = document.getElementById('gradeFilter').value;

    const filtered = allStudentGrades.filter(student => {
        const matchesSearch = student.name.toLowerCase().includes(searchTerm) ||
                              student.student_id.toLowerCase().includes(searchTerm);
        if (!matchesSearch) return false;

        if (filterType === 'all') return true;
        if (filterType === 'registered') return student.is_registered;
        if (filterType === 'unregistered') return !student.is_registered;
        if (filterType.startsWith('tier:')) {
            return student.is_registered && tierIndex(student.attendance_percentage) === Number(filterType.slice(5));
        }
        return true;
    });

    displayStudentGrades(filtered);
}

// Refresh grades
function refreshGrades() {
    loadStudentGrades();
    showAlert('Grades refreshed', 'success');
}

// Toggle admin guide
function toggleAdminGuide() {
    const guideContent = document.getElementById('adminGuideContent');
    const guideToggleText = document.getElementById('guideToggleText');
    
    if (guideContent.style.display === 'none') {
        guideContent.style.display = 'block';
        guideToggleText.textContent = 'Hide Guide';
    } else {
        guideContent.style.display = 'none';
        guideToggleText.textContent = 'Show Guide';
    }
}

// Logout
function logout() {
    localStorage.clear();
    window.location.href = '/admin/login';
}

// Show alert
function showAlert(message, type) {
    alertBox.innerHTML = `<div class="alert alert-${type}">${message}</div>`;
    setTimeout(() => {
        alertBox.innerHTML = '';
    }, 5000);
}
