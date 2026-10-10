// Notices are private, in-app and read-only until the owner acknowledges one.
(() => {
    const bar = document.getElementById('income-alert-bar');
    if (!bar) return;
    const dialog = document.getElementById('income-notices-dialog');
    const list = document.getElementById('income-notices-list');
    const error = document.getElementById('income-notices-error');
    const opener = document.getElementById('show-income-notices');
    let loading = null;
    let stopped = false;
    let refreshFailed = false;
    function textElement(tag, text, className) {
        const element = document.createElement(tag);
        element.textContent = text;
        if (className) element.className = className;
        return element;
    }
    async function refresh(forceRender = false) {
        if (stopped || document.hidden) return false;
        if (loading) {
            const refreshed = await loading;
            if (forceRender) return refresh(true);
            return refreshed;
        }
        loading = loadNotices(forceRender);
        try { return await loading; } finally { loading = null; }
    }
    async function loadNotices(forceRender) {
        try {
            const response = await fetch(bar.dataset.url, {credentials: 'same-origin', cache: 'no-store'});
            if (response.redirected || response.status === 401) { stopped = true; bar.hidden = true; return false; }
            if (!response.ok) throw new Error('Income notices could not be refreshed. Reload to try again.');
            const data = await response.json();
            const recovered = refreshFailed;
            refreshFailed = false;
            if (recovered) error.hidden = true;
            bar.hidden = data.count === 0;
            const first = data.notices[0];
            document.getElementById('income-alert-summary').textContent = first ? `${data.count} income notice${data.count === 1 ? '' : 's'} · ${first.source} received ${first.amount}. ${first.message}` : '';
            // Keep active controls stable while somebody is reading the popup.
            if (forceRender || recovered || !dialog.open || !list.children.length) {
                const restoreFocus = dialog.open && list.contains(document.activeElement);
                render(data);
                if (restoreFocus) (list.querySelector('button') || document.getElementById('close-income-notices')).focus();
            }
            return true;
        } catch (failure) {
            refreshFailed = true;
            error.textContent = failure.message;
            error.hidden = false;
            bar.hidden = false;
            document.getElementById('income-alert-summary').textContent = 'Income notices are unavailable. Reload to try again.';
            return false;
        }
    }
    function render(data) {
        list.replaceChildren();
        for (const notice of data.notices) {
            const article = document.createElement('article');
            article.className = 'income-notice';
            article.append(textElement('h3', `${notice.source} · ${notice.amount}`),
                textElement('p', `${notice.date} · ${notice.payer}`, 'secondary'), textElement('p', notice.message));
            const link = textElement('a', 'Review income details');
            link.href = notice.edit_url;
            const button = textElement('button', 'Mark as read', 'secondary-button');
            button.type = 'button';
            button.addEventListener('click', async () => {
                button.disabled = true;
                error.hidden = true;
                try {
                    const body = new FormData();
                    body.set('csrf_token', dialog.dataset.csrf);
                    body.set('payment', notice.payment);
                    body.set('version', notice.version);
                    const response = await fetch(dialog.dataset.readUrl, {method: 'POST', credentials: 'same-origin', body});
                    if (!response.ok || response.redirected) throw new Error('The payment or login changed. Reload before marking it as read.');
                    // Reload the batch without reopening the dialog or losing its focus.
                    if (!await refresh(true)) throw new Error('Marked as read, but notices could not refresh. Try again or reload.');
                    (list.querySelector('button') || document.getElementById('close-income-notices')).focus();
                } catch (failure) {
                    error.textContent = failure.message;
                    error.hidden = false;
                    button.disabled = false;
                }
            });
            const actions = document.createElement('div');
            actions.className = 'stream-actions';
            actions.append(link, button);
            article.append(actions);
            list.append(article);
        }
        if (!data.count) list.append(textElement('p', 'All income notices are read.'));
        else if (data.count > data.notices.length) list.append(textElement('p', 'Showing the latest 20. Mark notices as read to see the next batch.'));
    }
    opener.addEventListener('click', () => { if (!dialog.open) dialog.showModal(); });
    document.getElementById('close-income-notices').addEventListener('click', () => dialog.close());
    dialog.addEventListener('close', () => { (bar.hidden ? document.querySelector('.brand') : opener).focus(); refresh(); });
    document.addEventListener('visibilitychange', () => refresh());
    refresh();
    window.setInterval(refresh, 60000);
})();
