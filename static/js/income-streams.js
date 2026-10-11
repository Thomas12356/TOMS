// Native dialogs provide keyboard focus trapping and Escape-to-close.
const createDialog = document.getElementById('create-stream-dialog');
const createButton = document.getElementById('open-create-stream');
if (createDialog && createButton) {
    createButton.addEventListener('click', (event) => {
        event.preventDefault();
        createDialog.showModal();
        document.getElementById('stream-name').focus();
    });
    document.getElementById('close-create-stream').addEventListener('click', () => createDialog.close());
    createDialog.addEventListener('close', () => createButton.focus());
    if (createDialog.dataset.reopen === 'true') {
        if (createDialog.open) createDialog.close();
        createDialog.showModal();
    }
}

// Regular forecasts and entered shifts are alternative sources of gross income.
document.querySelectorAll('[data-income-mode]').forEach((select) => {
    const forecast = select.closest('form').querySelector('[data-regular-forecast]');
    const updateIncomeMode = () => {
        const shifts = select.value === 'shifts';
        forecast.hidden = shifts;
        forecast.disabled = shifts;
    };
    select.addEventListener('change', updateIncomeMode);
    updateIncomeMode();
});

// Employment and business mileage are separate schemes; mirror server checks.
document.querySelectorAll('[data-mileage-relationship]').forEach((select) => {
    const kind = select.form.querySelector('[name="kind"]');
    const updateChoices = () => {
        for (const option of select.options) {
            option.disabled = Boolean(option.value && (kind.value === '' ||
                (option.dataset.employed === 'true') !== (kind.value === 'employed')));
            option.hidden = option.disabled;
        }
        if (select.selectedOptions[0]?.disabled) select.value = '';
    };
    kind.addEventListener('change', updateChoices);
    updateChoices();
});

// Keep each stream's controls together; native dialogs handle keyboard focus.
const dialogOpeners = new WeakMap();
function openStreamDialog(dialog, opener) {
    document.querySelectorAll('dialog[open]').forEach(other => { if (other !== dialog) other.close(); });
    if (opener) dialogOpeners.set(dialog, opener);
    if (!dialog.open) dialog.showModal();
}
document.addEventListener('close', event => dialogOpeners.get(event.target)?.focus(), true);
document.addEventListener('click', async event => {
    const close = event.target.closest('.dialog-close');
    if (close) { event.preventDefault(); close.closest('dialog').close(); return; }
    const configure = event.target.closest('[data-open-dialog]');
    if (configure) {
        const dialog = document.getElementById(configure.dataset.openDialog);
        if (!dialog) return;
        event.preventDefault();
        openStreamDialog(dialog, configure);
        return;
    }
    const link = event.target.closest('[data-load-dialog]');
    if (!link) return;
    event.preventDefault();
    link.setAttribute('aria-busy', 'true');
    try {
        const response = await fetch(link.href, {credentials: 'same-origin', cache: 'no-store'});
        if (!response.ok || response.redirected) throw new Error('Unable to load records');
        const documentCopy = new DOMParser().parseFromString(await response.text(), 'text/html');
        const updated = documentCopy.getElementById(link.dataset.loadDialog);
        if (!updated) throw new Error('Missing records');
        const existing = document.getElementById(updated.id);
        const opener = existing?.open ? dialogOpeners.get(existing) : link;
        if (existing?.open) existing.close();
        const dialog = document.importNode(updated, true);
        dialog.removeAttribute('open');
        existing.replaceWith(dialog);
        openStreamDialog(dialog, opener);
    } catch (error) {
        // A normal GET is also a usable entry point for errors and expired sessions.
        window.location.assign(link.href);
    } finally {
        link.removeAttribute('aria-busy');
    }
});
document.querySelectorAll('dialog[data-reopen="true"]:not(#create-stream-dialog)').forEach(dialog => {
    if (dialog.open) dialog.close();
    openStreamDialog(dialog);
});

document.querySelectorAll('[data-reserve-form]').forEach(form => {
    form.addEventListener('submit', async event => {
        event.preventDefault();
        const error = form.querySelector('[data-reserve-error]');
        const button = form.querySelector('button');
        error.hidden = true;
        button.disabled = true;
        try {
            const response = await fetch(form.action, {method: 'POST', body: new FormData(form), credentials: 'same-origin'});
            if (!response.ok) {
                const body = await response.json().catch(() => ({}));
                throw new Error(body.error || 'Unable to save earmarks. Reload and try again.');
            }
            window.location.assign(response.url);
        } catch (failure) {
            error.textContent = failure.message;
            error.hidden = false;
            button.disabled = false;
        }
    });
});
