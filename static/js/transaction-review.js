// Each confirmation covers exactly the details shown, never an unseen bank update.
(() => {
    const dialog = document.getElementById('transaction-review');
    if (!dialog) return;
    const opener = document.getElementById('review-open');
    const confirm = document.getElementById('review-confirm');
    const retry = document.getElementById('review-retry');
    const error = document.getElementById('review-error');
    let transaction = null;
    let busy = false;
    let dismissed = false;
    let loadNumber = 0;

    async function load(open = false) {
        const number = ++loadNumber;
        transaction = null;
        confirm.disabled = true;
        retry.hidden = true;
        try {
            const response = await fetch(dialog.dataset.url, {credentials: 'same-origin', cache: 'no-store'});
            if (!response.ok) throw new Error('Unable to load transaction review. Try again.');
            const data = await response.json();
            if (number !== loadNumber) return;
            transaction = data.transaction;
            opener.hidden = !transaction;
            if (!transaction) {
                if (dialog.open) dialog.close();
                return;
            }
            document.getElementById('review-count').textContent = `${data.remaining} transaction${data.remaining === 1 ? '' : 's'} awaiting confirmation in this account view.`;
            const details = document.getElementById('review-details');
            details.replaceChildren();
            for (const [label, value] of transaction.details) {
                const term = document.createElement('dt');
                const description = document.createElement('dd');
                term.textContent = label;
                description.textContent = value;
                details.append(term, description);
            }
            document.getElementById('review-edit').href = transaction.edit_url;
            error.textContent = '';
            confirm.disabled = busy;
            if (open && !dismissed && !dialog.open) dialog.showModal();
        } catch (failure) {
            if (number !== loadNumber) return;
            retry.hidden = false;
            throw failure;
        }
    }

    function showLoadError(failure) {
        error.textContent = failure.message;
        opener.hidden = false;
        if (!dismissed && !dialog.open) dialog.showModal();
    }

    dialog.addEventListener('close', () => { dismissed = true; });
    document.getElementById('review-close').addEventListener('click', () => dialog.close());
    opener.addEventListener('click', () => {
        dismissed = false;
        load(true).catch(showLoadError);
    });
    retry.addEventListener('click', () => load(true).catch(showLoadError));
    confirm.addEventListener('click', async () => {
        if (!transaction || busy) return;
        busy = true;
        confirm.disabled = true;
        const reviewed = transaction;
        let saved = false;
        try {
            const response = await fetch(reviewed.confirm_url, {
                method: 'POST', credentials: 'same-origin', redirect: 'error',
                headers: {'Content-Type': 'application/json', 'X-CSRFToken': dialog.dataset.csrf},
                body: JSON.stringify({version: reviewed.version})
            });
            if (response.status === 409) {
                await load(true);
                error.textContent = 'Details changed while this window was open. Check them again before confirming.';
                return;
            }
            if (!response.ok) throw new Error('Confirmation failed. Try again or reload to check its status.');
            const result = await response.json();
            if (result.confirmed !== true) throw new Error('Confirmation was not acknowledged.');
            saved = true;
            document.querySelectorAll('[data-confirm-url]').forEach(row => {
                if (row.dataset.confirmUrl === reviewed.confirm_url) {
                    const label = row.querySelector('.review-state');
                    label.textContent = 'Confirmed';
                    label.className = 'review-state is-confirmed';
                }
            });
            await load(true);
        } catch (failure) {
            error.textContent = saved ? 'Confirmation saved. Unable to load remaining transactions. Try again.' :
                'Unable to complete review. Try again or reload to check its status.';
        } finally {
            busy = false;
            confirm.disabled = !transaction;
        }
    });
    load(true).catch(showLoadError);
})();
