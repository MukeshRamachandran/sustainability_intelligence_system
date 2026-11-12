(function () {
  const domain = document.body.dataset.authDomain;
  // Waste uses this same shared workflow (fieldMaps.waste, the waste_items
  // payload branch below, and the backend's GENERIC_DOMAINS all already
  // assumed it). Omitting it here made the whole module return before
  // initialize() ever ran: no current-period fetch, no Save/Submit wiring,
  // and no KCosmosSubmissionContext - which is also why evidence upload
  // failed with "Save the draft before adding evidence.": context() resolved
  // to undefined instead of a real submission accessor.
  if (!['transport', 'energy', 'lpg', 'water', 'waste'].includes(domain)) return;

  const fieldMaps = {
    transport: {
      petrol: 'transport_petrol_litres',
      'diesel-transport': 'transport_diesel_litres',
      'active-vehicles-petrol': 'petrol_vehicle_count',
      'active-vehicles-diesel': 'diesel_vehicle_count',
      'ev-consumption': 'ev_consumption_kwh',
      // DG is entered as electricity generated (kWh) (0015_dg_kwh_methodology).
      // Diesel litres are derived by the backend and are never sent.
      'dg-generation': 'dg_generation_kwh',
      'active-dg': 'dg_count'
    },
    energy: {
      'grid-ht': 'grid_ht_kwh',
      'grid-comm': 'grid_commercial_kwh',
      'grid-temp': 'grid_temporary_kwh',
      'ren-campus': 'renewable_on_campus_kwh',
      'ren-procured': 'renewable_procured_kwh',
      'ren-solar': 'solar_water_heater_kwh'
    },
    lpg: {
      // lpg_weight_kg (kg) is the governed emissions activity
      // (0013_lpg_kg_governance_v2). The cylinder count is optional reference
      // metadata and drives no calculation; litres are no longer collected.
      'lpg-kg': 'lpg_weight_kg',
      'lpg-cylinders': 'lpg_cylinder_count'
    },
    waste: {
      // Only wet waste is manager-entered. dry_waste_generated_kg and
      // total_waste_generated_kg are derived by the backend and are never sent.
      'wet-waste': 'wet_waste_generated_kg'
    },
    water: {
      'water-twad': 'water_twad_kl',
      'water-borewell': 'water_borewell_kl',
      'water-priv': 'water_private_kl',
      'water-waste': 'wastewater_generated_kl',
      'water-recycled': 'water_recycled_kl',
      'inlet-ph': 'inlet_ph',
      'inlet-tss': 'inlet_tss',
      'inlet-tds': 'inlet_tds',
      'inlet-nh3n': 'inlet_nh3n',
      'inlet-p': 'inlet_phosphorus',
      'inlet-cl': 'inlet_chloride',
      'inlet-so4': 'inlet_sulphate',
      'inlet-og': 'inlet_oil_grease',
      'inlet-cod': 'inlet_cod',
      'inlet-bod': 'inlet_bod',
      'outlet-ph': 'outlet_ph',
      'outlet-tss': 'outlet_tss',
      'outlet-tds': 'outlet_tds',
      'outlet-nh3n': 'outlet_nh3n',
      'outlet-p': 'outlet_phosphorus',
      'outlet-cl': 'outlet_chloride',
      'outlet-so4': 'outlet_sulphate',
      'outlet-og': 'outlet_oil_grease',
      'outlet-cod': 'outlet_cod',
      'outlet-bod': 'outlet_bod',
      'stp-inlet-cod': 'stp_inlet_cod',
      'stp-outlet-cod': 'stp_outlet_cod'
    }
  };

  const calculatedFields = {
    energy: ['grid-total', 'ren-total'],
    water: ['water-consumed']
  };
  const calculatedSwitches = {
    energy: ['grid-auto-calc', 'auto-calc'],
    water: ['auto-calc-consumed']
  };
  const editableStatuses = new Set(['draft', 'correction_requested']);
  const map = fieldMaps[domain];
  const monthNames = ['', 'January', 'February', 'March', 'April', 'May', 'June',
    'July', 'August', 'September', 'October', 'November', 'December'];
  // Months the backend allows for entry (GET /periods): current month first,
  // then the earlier months of the entry window. Never computed in the browser.
  let entryPeriods = [];
  let currentPeriod = null;
  let definitions = new Map();
  let currentSubmission = null;
  let operationInProgress = false;
  let currentLoadSequence = 0;

  function request(path, options = {}) {
    const write = options.method && !['GET', 'HEAD'].includes(options.method.toUpperCase());
    return apiRequest(`/api/manager/${domain}${path}`, { ...options, csrf: write });
  }

  function valueFor(input) {
    return input.value.trim() === '' ? null : input.value;
  }

  function notify(message, type, operation) {
    try {
      window.KCosmosUI?.notify?.(message, type, { operation });
    } catch {
      console.warn('[K-COSMOS] UI notification could not be displayed.', { operation });
    }
  }

  function workflowMessage() {
    let message = document.getElementById('submission-status-message');
    if (!message) {
      message = document.createElement('p');
      message.id = 'submission-status-message';
      message.className = 'text-muted';
      message.style.margin = '8px 0 0';
      document.getElementById('save-draft-btn').parentElement.appendChild(message);
    }
    return message;
  }

  function operationError(error, operation) {
    const status = error.status || 0;
    const prefix = status ? `${operation} failed (${status}).` : `${operation} failed.`;
    let guidance = error.message || 'Please try again.';
    if (status === 401) guidance = 'Your session has expired. Please sign in again.';
    if (status === 403) guidance = error.message || 'This account is not authorized for this action.';
    if (status === 409) guidance = error.message || 'The submission state changed. Refresh and try again.';
    if (status === 422) guidance = error.message || 'One or more submitted values are invalid.';
    if (status >= 500) guidance = 'The server could not complete the request. Please try again.';
    const requestReference = error.requestId ? ` Reference: ${error.requestId}` : '';
    const text = `${prefix} ${guidance}${requestReference}`;
    const message = workflowMessage();
    message.textContent = text;
    message.style.color = 'var(--danger)';
    if (error.code === 'stale_submission') {
      const reload = document.createElement('button');
      reload.type = 'button';
      reload.className = 'btn btn-outline';
      reload.style.marginLeft = '8px';
      reload.textContent = 'Refresh submission';
      reload.addEventListener('click', async () => {
        reload.disabled = true;
        try { await loadCurrent(); }
        catch (refreshError) { operationError(refreshError, 'Refresh submission'); }
      });
      message.appendChild(reload);
    }
    console.warn(`[K-COSMOS] ${operation} failed`, { status, requestId: error.requestId || null });
    notify(text, 'error', operation);
  }

  function validateRequiredMetrics() {
    const missing = Object.entries(map).filter(([id, metricCode]) => {
      const definition = definitions.get(metricCode);
      return definition?.required_for_complete && definition.manager_editable
        && valueFor(document.getElementById(id)) === null;
    }).map(([, metricCode]) => definitions.get(metricCode).display_name || metricCode);
    if (missing.length) {
      const error = new Error(`Complete the required metrics: ${missing.join(', ')}.`);
      error.status = 422;
      throw error;
    }
  }

  function payload(includePeriod) {
    const values = Object.entries(map).map(([id, metricCode]) => ({
      metric_code: metricCode,
      value: valueFor(document.getElementById(id)),
      quality_note: null
    }));
    const body = { remarks: document.getElementById('remarks')?.value.trim() || null, values };
    // Waste sends its dry-material inventory alongside the metric values so
    // the backend saves both in one transaction. The derived dry and total
    // quantities are never sent: the server owns them.
    if (domain === 'waste' && window.KCosmosWasteInventory) {
      body.waste_items = window.KCosmosWasteInventory.items();
    }
    if (includePeriod) body.reporting_period_id = document.getElementById('month').value;
    if (currentSubmission) body.expected_row_version = currentSubmission.row_version;
    return body;
  }

  function statusLabel(status) {
    return status ? status.replaceAll('_', ' ').replace(/\b\w/g, char => char.toUpperCase()) : 'No draft';
  }

  function showStatus(submission) {
    const message = workflowMessage();
    if (!submission) {
      message.textContent = 'Status: No saved submission for this reporting period.';
      message.style.color = '';
      return;
    }
    const correction = submission.status === 'correction_requested' && submission.correction_reason
      ? ` — ${submission.correction_reason}` : '';
    message.textContent = `Status: ${statusLabel(submission.status)} · Revision ${submission.revision_number}${correction}`;
    message.style.color = submission.status === 'correction_requested' ? 'var(--danger)' : '';
    const lpgStatus = document.getElementById('prev-status');
    if (lpgStatus) lpgStatus.textContent = statusLabel(submission.status);
  }

  function showSuccess(text, submission) {
    const message = workflowMessage();
    message.textContent = `${text} Status: ${statusLabel(submission.status)} · Revision ${submission.revision_number}`;
    message.style.color = 'var(--success)';
  }

  function setEditable(editable) {
    const enabled = editable && !operationInProgress;
    document.querySelectorAll('#entry-form input, #entry-form textarea, #entry-form button').forEach(control => {
      if (['reporting-period-label', 'year', 'month'].includes(control.id)) return;
      control.disabled = !enabled;
    });
    // The month picker stays usable on a read-only month so the Manager can
    // move to another month; it is locked only while a save/submit runs.
    const periodSelect = document.getElementById('reporting-period-select');
    if (periodSelect) periodSelect.disabled = operationInProgress;
    document.getElementById('save-draft-btn').disabled = !enabled;
    document.getElementById('submit-btn').disabled = !enabled;
    document.getElementById('reset-btn').disabled = !enabled;
    ['autofill-inlet-btn', 'autofill-outlet-btn', 'autofill-stp-btn'].forEach(id => {
      const button = document.getElementById(id);
      if (button) button.disabled = !enabled;
    });
    (calculatedFields[domain] || []).forEach(id => {
      const input = document.getElementById(id);
      if (input) input.readOnly = true;
    });
    (calculatedSwitches[domain] || []).forEach(id => {
      const checkbox = document.getElementById(id);
      if (checkbox) checkbox.disabled = true;
    });
  }

  function populate(submission) {
    const byCode = new Map((submission?.values || []).map(item => [item.metric_code, item.value]));
    Object.entries(map).forEach(([id, metricCode]) => {
      document.getElementById(id).value = byCode.get(metricCode) ?? '';
    });
    const remarks = document.getElementById('remarks');
    if (remarks) remarks.value = submission?.remarks || '';
    document.querySelectorAll('#entry-form input').forEach(input => input.dispatchEvent(new Event('input')));
    showStatus(submission);
    setEditable(!submission || editableStatuses.has(submission.status));
    document.dispatchEvent(new CustomEvent('kcosmos:submission-loaded', { detail: { submission } }));
  }

  async function loadCurrent() {
    const sequence = ++currentLoadSequence;
    const periodId = document.getElementById('month').value;
    if (!periodId) return;
    let loaded;
    try {
      loaded = await request(`/submissions/current?reporting_period_id=${encodeURIComponent(periodId)}`);
    } catch (error) {
      if (sequence !== currentLoadSequence) return;
      throw error;
    }
    if (sequence !== currentLoadSequence) return;
    currentSubmission = loaded;
    populate(currentSubmission);
  }

  async function saveDraft(announce = true) {
    const isUpdate = currentSubmission && editableStatuses.has(currentSubmission.status);
    const path = isUpdate ? `/submissions/${currentSubmission.id}` : '/submissions';
    const method = isUpdate ? 'PUT' : 'POST';
    currentSubmission = await request(path, {
      method,
      body: JSON.stringify(payload(!isUpdate))
    });
    populate(currentSubmission);
    if (announce) {
      showSuccess('Draft saved.', currentSubmission);
      notify('Draft saved.', 'success', 'Save Draft');
    }
    return currentSubmission;
  }

  async function submitForReview() {
    validateRequiredMetrics();
    const submission = await saveDraft(false);
    await request(`/submissions/${submission.id}/submit`, { method: 'POST' });
    try {
      await loadCurrent();
    } catch (error) {
      currentSubmission = { ...submission, status: 'submitted' };
      populate(currentSubmission);
      console.warn('[K-COSMOS] Submitted state refresh failed', {
        status: error.status || 0,
        requestId: error.requestId || null
      });
      showSuccess('Submitted for review. Refresh this page to reload the latest server state.', currentSubmission);
      notify('Submitted for review. Status refresh is pending.', 'warning', 'Submit refresh');
      return;
    }
    if (!currentSubmission || currentSubmission.id !== submission.id || currentSubmission.status !== 'submitted') {
      currentSubmission = { ...submission, status: 'submitted' };
      populate(currentSubmission);
      showSuccess('Submitted for review. Refresh this page to reload the latest server state.', currentSubmission);
      notify('Submitted for review. Status refresh is pending.', 'warning', 'Submit refresh');
      return;
    }
    showSuccess('Submitted for review.', currentSubmission);
    notify('Submitted for review.', 'success', 'Submit for Review');
  }

  window.KCosmosSubmissionContext = {
    domain,
    getSubmission: () => currentSubmission,
    getStatus: () => currentSubmission?.status || 'draft',
    ensureDraft: () => saveDraft(false)
  };

  function replaceButton(id, operation, handler) {
    const oldButton = document.getElementById(id);
    const button = oldButton.cloneNode(true);
    oldButton.replaceWith(button);
    button.addEventListener('click', async event => {
      event.preventDefault();
      if (operationInProgress) return;
      operationInProgress = true;
      showStatus(currentSubmission);
      setEditable(Boolean(!currentSubmission || editableStatuses.has(currentSubmission.status)));
      try { await handler(); }
      catch (error) { operationError(error, operation); }
      finally {
        operationInProgress = false;
        setEditable(Boolean(!currentSubmission || editableStatuses.has(currentSubmission.status)));
      }
    });
  }

  function periodText(period) {
    return `${monthNames[period.month]} ${period.year}`;
  }

  async function loadDefinitions(period) {
    const items = await request(`/metrics?reporting_period_id=${encodeURIComponent(period.id)}`);
    const loaded = new Map(items.map(item => [item.code, item]));
    for (const metricCode of Object.values(map)) {
      if (!loaded.has(metricCode)) throw new Error(`Backend metric definition is missing: ${metricCode}`);
      if (!loaded.get(metricCode).manager_editable) throw new Error(`Calculated metric cannot be edited: ${metricCode}`);
    }
    definitions = loaded;
  }

  // Mirrors the selected month into every existing period field, so other
  // page scripts (waste topbar, evidence) keep reading the same elements.
  function applyPeriod(period) {
    currentPeriod = period;
    document.getElementById('year').value = String(period.year);
    document.getElementById('month').value = period.id;
    document.getElementById('reporting-period-label').value = periodText(period);
    document.getElementById('ay').innerHTML = `<option>${period.year}-${period.year + 1}</option>`;
    const select = document.getElementById('reporting-period-select');
    if (select) select.value = period.id;
    // Topbar "AY: … | <month>" shows the month being entered, not a fixed one.
    const topbarSpans = document.querySelectorAll('.topbar .current-period span');
    if (topbarSpans.length === 3) {
      topbarSpans[0].textContent = `AY: ${period.year}–${period.year + 1}`;
      topbarSpans[2].textContent = periodText(period);
    }
    const url = new URL(window.location.href);
    if (period.is_current) url.searchParams.delete('reporting_period_id');
    else url.searchParams.set('reporting_period_id', period.id);
    window.history.replaceState(null, '', url);
  }

  // Replaces the read-only "Reporting Month" box with a picker of the months
  // the backend allows. The original input stays (hidden) and in sync.
  function buildPeriodPicker() {
    const label = document.getElementById('reporting-period-label');
    label.hidden = true;
    const select = document.createElement('select');
    select.className = 'form-control';
    select.id = 'reporting-period-select';
    select.setAttribute('aria-label', 'Reporting month');
    entryPeriods.forEach(period => {
      const option = document.createElement('option');
      option.value = period.id;
      option.textContent = `${periodText(period)}${period.is_current ? ' (current)' : ''}${period.is_open ? '' : ' — closed'}`;
      select.appendChild(option);
    });
    label.insertAdjacentElement('afterend', select);
    const formLabel = document.querySelector('label[for="reporting-period-label"]');
    if (formLabel) formLabel.setAttribute('for', 'reporting-period-select');
    const hint = document.createElement('small');
    hint.className = 'text-muted';
    hint.style.display = 'block';
    hint.style.marginTop = '4px';
    hint.textContent = entryPeriods.length > 1
      ? 'You can enter the current month or the previous months listed here.'
      : 'Entry is open for the current month only.';
    select.insertAdjacentElement('afterend', hint);
    // "Reset Form" calls form.reset(), which would snap the picker and the
    // month fields back to their defaults; re-apply the selected month after it.
    document.getElementById('entry-form').addEventListener('reset', () => {
      setTimeout(() => { if (currentPeriod) applyPeriod(currentPeriod); }, 0);
    });
    select.addEventListener('change', async () => {
      const next = entryPeriods.find(period => period.id === select.value);
      if (!next || next.id === currentPeriod?.id) return;
      if (operationInProgress) { select.value = currentPeriod.id; return; }
      const previous = currentPeriod;
      operationInProgress = true;
      setEditable(false);
      try {
        await loadDefinitions(next);
        applyPeriod(next);
        currentSubmission = null;
        await loadCurrent();
      } catch (error) {
        applyPeriod(previous);
        operationError(error, 'Load reporting month');
      } finally {
        operationInProgress = false;
        setEditable(Boolean(!currentSubmission || editableStatuses.has(currentSubmission.status)));
      }
    });
  }

  async function initialize() {
    try {
      const session = await getSession();
      if (session.user.role !== 'manager' || session.user.manager_domain !== domain) {
        window.location.replace(`${domain}-login.html`);
        return;
      }
      entryPeriods = await request('/periods');
      if (!entryPeriods.length) throw new Error('No reporting month is open for entry.');
      // A link from Submission History may name a month; it is honoured only
      // when that month is inside the backend's entry window.
      const requested = new URLSearchParams(window.location.search).get('reporting_period_id');
      const initial = entryPeriods.find(period => period.id === requested)
        || entryPeriods.find(period => period.is_current) || entryPeriods[0];
      await loadDefinitions(initial);
      buildPeriodPicker();
      applyPeriod(initial);
      replaceButton('save-draft-btn', 'Save Draft', saveDraft);
      replaceButton('submit-btn', 'Submit for Review', submitForReview);
      (calculatedFields[domain] || []).forEach(id => { document.getElementById(id).readOnly = true; });
      (calculatedSwitches[domain] || []).forEach(id => {
        const checkbox = document.getElementById(id);
        if (checkbox) {
          checkbox.checked = true;
          checkbox.disabled = true;
        }
      });
      await loadCurrent();
    } catch (error) {
      operationError(error, 'Initialize manager form');
      setEditable(false);
    }
  }

  document.addEventListener('DOMContentLoaded', initialize);
})();
