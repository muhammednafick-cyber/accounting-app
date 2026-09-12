/* Form reliability: inline validation, recoverable drafts, one submission per click.
   Nothing here changes what is posted - it only protects what was typed. */
window.AppForms = (() => {
    'use strict';

    // agent_proposal_id is skipped too: a recovered draft must not carry a
    // proposal that may already have been posted from somewhere else.
    const SKIP = new Set(['csrf_token', 'submission_token', 'agent_proposal_id']);
    const skipField = f => !f.name || SKIP.has(f.name) || f.type === 'file'
        || f.type === 'button' || f.type === 'submit' || f.disabled;

    /* ------------------------------------------------------ inline validation */

    function showFieldError(field, message) {
        if (!field) return;
        clearFieldError(field);
        const note = document.createElement('p');
        note.className = 'field-error';
        note.id = `err-${(field.name || 'field').replace(/\W/g, '')}-${Math.random().toString(36).slice(2, 8)}`;
        note.textContent = message;
        field.parentElement.insertBefore(note, field.nextSibling);
        field.setAttribute('aria-invalid', 'true');
        field.setAttribute('aria-describedby', note.id);
        field.classList.add('has-error');
    }

    function clearFieldError(field) {
        const described = field.getAttribute('aria-describedby');
        if (described) document.getElementById(described)?.remove();
        field.removeAttribute('aria-invalid');
        field.removeAttribute('aria-describedby');
        field.classList.remove('has-error');
    }

    function clearErrors(form) {
        form.querySelectorAll('.field-error').forEach(n => n.remove());
        form.querySelectorAll('[aria-invalid]').forEach(f => {
            f.removeAttribute('aria-invalid');
            f.removeAttribute('aria-describedby');
            f.classList.remove('has-error');
        });
        form.querySelector('.form-error-summary')?.remove();
    }

    function summarise(form, problems) {
        form.querySelector('.form-error-summary')?.remove();
        if (!problems.length) return;
        const box = document.createElement('div');
        box.className = 'form-error-summary';
        box.setAttribute('role', 'alert');
        box.setAttribute('tabindex', '-1');
        const heading = document.createElement('strong');
        heading.textContent = problems.length === 1 ? 'One entry needs attention'
            : `${problems.length} entries need attention`;
        const list = document.createElement('ul');
        problems.forEach(({ field, message }) => {
            const item = document.createElement('li');
            if (field) {
                const link = document.createElement('button');
                link.type = 'button';
                link.textContent = message;
                link.addEventListener('click', () => {
                    field.focus();
                    field.scrollIntoView({ block: 'center' });
                });
                item.appendChild(link);
            } else item.textContent = message;
            list.appendChild(item);
        });
        box.append(heading, list);
        form.insertBefore(box, form.firstChild);
        box.focus();
    }

    /** Show field-level messages the server returned, without losing input. */
    function applyServerErrors(form, errors, summaryMessage) {
        clearErrors(form);
        const problems = [];
        Object.entries(errors || {}).forEach(([name, message]) => {
            const found = form.querySelector(`[name="${CSS.escape(name)}"]`);
            if (found) showFieldError(found, message);
            problems.push({ field: found, message });
        });
        if (summaryMessage) problems.unshift({ field: null, message: summaryMessage });
        summarise(form, problems);
        return problems.length;
    }

    function labelFor(form, field) {
        const label = field.id && form.querySelector(`label[for="${CSS.escape(field.id)}"]`);
        return (label?.textContent || field.getAttribute('aria-label') || field.name || 'This field')
            .replace(/[:*]/g, '').trim();
    }

    function builtInProblems(form) {
        const problems = [];
        Array.from(form.elements).forEach(field => {
            if (skipField(field) || field.type === 'hidden') return;
            if (field.offsetParent === null && field.type !== 'select-one') return;
            if (field.checkValidity && !field.checkValidity()) {
                showFieldError(field, field.validationMessage);
                problems.push({ field, message: `${labelFor(form, field)}: ${field.validationMessage}` });
            }
        });
        return problems;
    }

    function validate(form) {
        clearErrors(form);
        const problems = builtInProblems(form);
        const hook = form.dataset.validateHook && window[form.dataset.validateHook];
        if (typeof hook === 'function') {
            (hook(form) || []).forEach(problem => {
                if (problem.field) showFieldError(problem.field, problem.fieldMessage || problem.message);
                problems.push(problem);
            });
        }
        if (problems.length) {
            summarise(form, problems);
            problems[0].field?.focus();
        }
        return !problems.length;
    }

    /* ------------------------------------------------------------ drafts (X1) */

    const draftKey = form => `draft.${form.dataset.autosave}`;

    function collect(form) {
        const values = [];
        Array.from(form.elements).forEach(field => {
            if (skipField(field)) return;
            if (field.type === 'checkbox' || field.type === 'radio') {
                if (field.checked) values.push([field.name, field.value]);
            } else if (field.multiple && field.selectedOptions) {
                Array.from(field.selectedOptions).forEach(o => values.push([field.name, o.value]));
            } else values.push([field.name, field.value]);
        });
        return values;
    }

    const isEmpty = values => values.every(([, v]) => !String(v ?? '').trim());

    // An untouched form is not an empty one: selects sit on a default value. A
    // draft is only worth keeping - and a stored one only worth offering - when
    // the form differs from how the page drew it.
    const untouched = form => JSON.stringify(collect(form)) === form.dataset.formsBaseline;

    function saveDraft(form) {
        try {
            if (untouched(form)) localStorage.removeItem(draftKey(form));
            else localStorage.setItem(draftKey(form),
                JSON.stringify({ saved: Date.now(), values: collect(form) }));
        } catch { /* Storage is a convenience; entry must still work without it. */ }
    }

    function discardDraft(form) {
        try { localStorage.removeItem(draftKey(form)); } catch { /* ignore */ }
    }

    function fire(field) {
        if (window.jQuery && field.tagName === 'SELECT') window.jQuery(field).trigger('change');
        else field.dispatchEvent(new Event('change', { bubbles: true }));
        field.dispatchEvent(new Event('input', { bubbles: true }));
    }

    function restore(form, values) {
        const counts = {};
        values.forEach(([name]) => { counts[name] = (counts[name] || 0) + 1; });
        const hook = form.dataset.autosaveRows && window[form.dataset.autosaveRows];
        // The page knows how to add its own rows; without enough rows only the
        // first line of a multi-line entry could be put back.
        if (typeof hook === 'function') {
            try { hook(counts); } catch (e) { console.warn('Draft rows', e); }
        }
        const byName = {};
        values.forEach(([name, value]) => (byName[name] = byName[name] || []).push(value));
        Object.entries(byName).forEach(([name, list]) => {
            const fields = Array.from(form.querySelectorAll(`[name="${CSS.escape(name)}"]`));
            if (!fields.length) return;
            if (fields[0].type === 'checkbox' || fields[0].type === 'radio') {
                fields.forEach(f => { f.checked = list.includes(f.value); fire(f); });
                return;
            }
            list.forEach((value, index) => {
                const field = fields[index];
                if (!field || skipField(field)) return;
                field.value = value;
                fire(field);
            });
        });
    }

    function offerDraft(form, draft) {
        const when = new Date(draft.saved);
        const banner = document.createElement('div');
        banner.className = 'draft-banner';
        banner.setAttribute('role', 'status');
        const text = document.createElement('span');
        text.innerHTML = '<strong>Unfinished entry recovered.</strong> '
            + `Last typed ${when.toLocaleDateString('en-GB')} at `
            + `${when.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })}. `
            + 'Nothing has been posted.';
        const restoreBtn = document.createElement('button');
        restoreBtn.type = 'button';
        restoreBtn.className = 'btn';
        restoreBtn.textContent = 'Restore it';
        restoreBtn.addEventListener('click', () => { restore(form, draft.values); banner.remove(); });
        const dropBtn = document.createElement('button');
        dropBtn.type = 'button';
        dropBtn.className = 'btn btn-quiet';
        dropBtn.textContent = 'Discard';
        dropBtn.addEventListener('click', () => { discardDraft(form); banner.remove(); });
        banner.append(text, restoreBtn, dropBtn);
        form.parentElement.insertBefore(banner, form);
    }

    function watchDraft(form) {
        let pending;
        const schedule = () => { clearTimeout(pending); pending = setTimeout(() => saveDraft(form), 700); };
        form.addEventListener('input', schedule);
        form.addEventListener('change', schedule);
        // A row added or removed changes the draft without any field being edited.
        new MutationObserver(schedule).observe(form, { childList: true, subtree: true });
        addEventListener('pagehide', () => { clearTimeout(pending); saveDraft(form); });

        let stored = null;
        try { stored = JSON.parse(localStorage.getItem(draftKey(form)) || 'null'); } catch { stored = null; }
        if (stored && Array.isArray(stored.values) && !isEmpty(stored.values) && untouched(form))
            offerDraft(form, stored);
    }

    /* --------------------------------------------------- one submission a click */

    function rotateSubmissionToken(field) {
        const token = crypto.randomUUID ? crypto.randomUUID().replace(/-/g, '')
            : Date.now().toString(16) + Math.random().toString(16).slice(2);
        if (field) field.value = token;
        return token;
    }

    function release(form) {
        delete form.dataset.submitting;
        form.querySelectorAll('button[type=submit],input[type=submit]').forEach(button => {
            button.disabled = false;
            if (button.dataset.idleLabel && button.tagName === 'BUTTON')
                button.textContent = button.dataset.idleLabel;
        });
    }

    function guardSubmit(form) {
        form.addEventListener('submit', event => {
            if (form.dataset.validateHook !== undefined && !validate(form)) {
                event.preventDefault();
                return;
            }
            if (form.dataset.submitting === '1') { event.preventDefault(); return; }
            form.dataset.submitting = '1';
            if (form.dataset.autosave) discardDraft(form);
            form.querySelectorAll('button[type=submit],input[type=submit]').forEach(button => {
                button.dataset.idleLabel = button.textContent;
                button.disabled = true;
                if (button.tagName === 'BUTTON') button.textContent = 'Saving…';
            });
            // A slow post must not leave the form permanently unusable.
            setTimeout(() => release(form), 20000);
        });
        // Back-navigation shows a cached page; the form has to work again.
        addEventListener('pageshow', event => { if (event.persisted) release(form); });
    }

    /* ------------------------------------------------------------------- wiring */

    function enhance(form) {
        if (form.dataset.formsReady) return;
        form.dataset.formsReady = '1';
        form.dataset.formsBaseline = JSON.stringify(collect(form));
        form.addEventListener('input', event => {
            if (event.target.getAttribute?.('aria-invalid')) clearFieldError(event.target);
        });
        if (form.dataset.autosave) watchDraft(form);
        guardSubmit(form);
    }

    const start = () => document
        .querySelectorAll('form[data-autosave],form[data-validate-hook]')
        .forEach(enhance);
    if (document.readyState === 'loading') addEventListener('DOMContentLoaded', start);
    else start();

    return {
        enhance, validate, showFieldError, clearFieldError, clearErrors,
        applyServerErrors, saveDraft, discardDraft, restore,
        rotateSubmissionToken, release,
    };
})();
