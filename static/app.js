/* SecretaryAI — Shared Frontend Logic */

// ─── MOBILE SIDEBAR ───────────────────────────────────────────────────────────

function toggleSidebar() {
  const sidebar = document.getElementById('sidebar');
  const overlay = document.getElementById('sidebarOverlay');
  const isOpen = sidebar.classList.contains('open');
  if (isOpen) {
    sidebar.classList.remove('open');
    overlay.classList.remove('visible');
  } else {
    sidebar.classList.add('open');
    overlay.classList.add('visible');
  }
}

function closeSidebar() {
  document.getElementById('sidebar').classList.remove('open');
  document.getElementById('sidebarOverlay').classList.remove('visible');
}

// Close sidebar when a nav link is tapped on mobile
document.addEventListener('DOMContentLoaded', function() {
  if (window.innerWidth <= 768) {
    document.querySelectorAll('.nav-item').forEach(link => {
      link.addEventListener('click', closeSidebar);
    });
  }
});

// ─── JOB MODAL ───────────────────────────────────────────────────────────────

function openJobModal(jobId, company, role) {
  document.getElementById('modalCompany').textContent = company;
  document.getElementById('modalRole').textContent = role;
  const body = document.getElementById('modalBody');
  body.innerHTML = `<div style="text-align:center;padding:20px;"><div class="typing-indicator" style="justify-content:center;"><div class="typing-dot"></div><div class="typing-dot"></div><div class="typing-dot"></div></div></div>`;
  document.getElementById('jobModal').classList.add('open');

  // Load job detail
  fetch(`/api/pipeline/${jobId}`)
    .then(r => r.json())
    .then(job => {
      if (!job || job.error) {
        body.innerHTML = `<div class="text-muted text-sm">Could not load job details.</div>`;
        return;
      }
      const stages = ['Identified','Applied','Responded','Interview','Offer','Rejected'];
      const curIdx = stages.indexOf(job.pipeline_stage || 'Identified');
      const stageButtons = stages.map((s,i) => `
        <button class="btn btn-sm ${i === curIdx ? 'btn-primary' : 'btn-ghost'}"
          onclick="moveAndRefresh(${jobId}, '${s}')">
          ${s}
        </button>
      `).join('');

      body.innerHTML = `
        <div style="display:flex; flex-direction:column; gap:16px;">
          <div style="display:flex; gap:12px; align-items:flex-start; flex-wrap:wrap;">
            ${job.country || job.city ? `<span class="tag">📍 ${job.city || ''}${job.city && job.country ? ', ' : ''}${job.country || ''}</span>` : ''}
            ${job.salary_max ? `<span class="tag green">€${Number(job.salary_max).toLocaleString()}/yr</span>` : ''}
            ${job.visa_support && job.visa_support !== 'Unknown' ? `<span class="tag accent">Visa: ${job.visa_support}</span>` : ''}
            ${job.match_score ? `<span class="tag green">${Math.round(job.match_score*100)}% match</span>` : ''}
          </div>

          <div>
            <div class="form-label" style="margin-bottom:8px;">Pipeline Stage</div>
            <div style="display:flex; gap:6px; flex-wrap:wrap;">${stageButtons}</div>
          </div>

          ${job.apply_url ? `<div><div class="form-label">Job URL</div><a href="${job.apply_url}" target="_blank" class="btn btn-secondary btn-sm">Open Job Listing →</a></div>` : ''}

          <div class="form-row">
            <div class="form-group">
              <label class="form-label">Contact Name</label>
              <input type="text" class="form-input" id="contactName" value="${job.contact_name || ''}" placeholder="e.g. Sarah (Recruiter)">
            </div>
            <div class="form-group">
              <label class="form-label">Contact Email</label>
              <input type="email" class="form-input" id="contactEmail" value="${job.contact_email || ''}" placeholder="recruiter@company.com">
            </div>
          </div>

          <div class="form-group">
            <label class="form-label">Next Action Due</label>
            <div style="display:flex; gap:8px;">
              <input type="date" class="form-input" id="nextActionDue" value="${job.next_action_due || ''}">
              <input type="text" class="form-input" id="nextAction" value="${job.next_action || ''}" placeholder="e.g. Send follow-up email">
            </div>
          </div>

          <div class="form-group">
            <label class="form-label">Notes</label>
            <textarea class="form-textarea" id="jobNotes" style="min-height:70px;">${job.notes || ''}</textarea>
          </div>

          <div style="display:flex; gap:8px; flex-wrap:wrap;">
            <button class="btn btn-primary btn-sm" onclick="saveJobUpdates(${jobId})">Save Changes</button>
            <button class="btn btn-secondary btn-sm" onclick="generateDraftFromModal(${jobId}, '${company.replace(/'/g,'')}')">✦ Generate Draft Pack</button>
            <button class="btn btn-ghost btn-sm" onclick="archiveJob(${jobId})">Archive</button>
          </div>
        </div>
      `;

      document.getElementById('modalPrimaryAction').onclick = () => generateDraftFromModal(jobId, company);
    });
}

function closeModal() {
  document.getElementById('jobModal').classList.remove('open');
}

function saveJobUpdates(jobId) {
  const updates = {
    contact_name: document.getElementById('contactName')?.value || '',
    contact_email: document.getElementById('contactEmail')?.value || '',
    next_action_due: document.getElementById('nextActionDue')?.value || '',
    next_action: document.getElementById('nextAction')?.value || '',
    notes: document.getElementById('jobNotes')?.value || '',
  };
  fetch(`/api/pipeline/${jobId}`, {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify(updates)
  })
  .then(r => r.json())
  .then(d => {
    if (d.error) { alert(d.error); return; }
    closeModal();
    showFlash('Job updated successfully', 'success');
  });
}

function moveAndRefresh(jobId, stage) {
  fetch(`/api/pipeline/${jobId}/stage`, {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({stage})
  })
  .then(r => r.json())
  .then(() => {
    closeModal();
    window.location.reload();
  });
}

function archiveJob(jobId) {
  if (!confirm('Archive this job from your pipeline?')) return;
  moveAndRefresh(jobId, 'Archived');
}

function generateDraftFromModal(jobId, company) {
  closeModal();
  showFlash(`Generating draft pack for ${company}...`, 'info');
  fetch(`/api/jobs/${jobId}/draft-pack`, {method:'POST'})
    .then(r => r.json())
    .then(() => window.location.href = `/pipeline?draft_job_id=${jobId}`);
}

// ─── ADD JOB MODAL ───────────────────────────────────────────────────────────

function openAddModal() {
  document.getElementById('addJobModal').classList.add('open');
}

function closeAddModal() {
  document.getElementById('addJobModal').classList.remove('open');
}

function submitAddJob(e) {
  e.preventDefault();
  const form = new FormData(e.target);
  const data = Object.fromEntries(form.entries());
  // Convert salary fields to numbers
  if (data.salary_min) data.salary_min = parseInt(data.salary_min) || 0;
  if (data.salary_max) data.salary_max = parseInt(data.salary_max) || 0;

  fetch('/api/pipeline', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify(data)
  })
  .then(r => r.json())
  .then(d => {
    if (d.error) { alert(d.error); return; }
    closeAddModal();
    showFlash(`${data.company} added to pipeline (${data.stage || 'Identified'})`, 'success');
    setTimeout(() => window.location.reload(), 800);
  });
}

// ─── FLASH MESSAGES ──────────────────────────────────────────────────────────

function showFlash(message, level = 'info') {
  const existing = document.querySelector('.flash-toast');
  if (existing) existing.remove();
  const div = document.createElement('div');
  div.className = `flash ${level} flash-toast`;
  div.style.cssText = 'position:fixed; top:20px; right:20px; z-index:999; min-width:280px; max-width:400px; animation:slideIn 0.2s ease;';
  div.innerHTML = `<span>${level === 'success' ? '✓' : level === 'error' ? '✕' : 'ℹ'}</span> ${message}`;
  document.body.appendChild(div);
  setTimeout(() => div.remove(), 4000);
}

// ─── MODAL CLOSE ON BACKDROP ─────────────────────────────────────────────────

document.addEventListener('click', function(e) {
  if (e.target.classList.contains('modal-overlay')) {
    e.target.classList.remove('open');
  }
});

document.addEventListener('keydown', function(e) {
  if (e.key === 'Escape') {
    document.querySelectorAll('.modal-overlay.open').forEach(m => m.classList.remove('open'));
  }
});

// ─── STYLE: SLIDE-IN ANIMATION ───────────────────────────────────────────────

const style = document.createElement('style');
style.textContent = `
@keyframes slideIn {
  from { opacity:0; transform:translateX(20px); }
  to   { opacity:1; transform:translateX(0); }
}
`;
document.head.appendChild(style);
