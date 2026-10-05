// Sync all saved accounts; the account dropdown only filters the ledger view.
(() => {
    const controls = document.querySelector('.sync-controls');
    if (!controls) return;
    const buttons = document.querySelectorAll('[data-sync-now]');
    const message = document.getElementById('sync-message');
    const timestamp = document.getElementById('last-synced');
    let busy = false;

    async function status() {
        const response = await fetch(controls.dataset.statusUrl, {credentials: 'same-origin', cache: 'no-store'});
        if (!response.ok) throw new Error('Unable to check sync status.');
        const run = (await response.json()).run;
        timestamp.textContent = run?.last_success_at ?
            `Last synced: ${new Date(run.last_success_at).toLocaleString()}` : 'Last synced: never';
        return run;
    }

    function showLast(run) {
        message.textContent = run?.status === 'failed' ? 'Last sync failed. Try Sync now.' :
            run?.status === 'completed' ? 'Up to date.' : 'No completed sync yet.';
    }

    async function sync(force) {
        if (busy) return;
        busy = true;
        buttons.forEach(button => { button.disabled = true; });
        try {
            const before = await status();
            if (!force && before?.status !== 'running' && before?.last_success_at && Date.now() - Date.parse(before.last_success_at) < 60000) {
                showLast(before);
                return;
            }
            message.textContent = 'Syncing transactions…';
            const response = await fetch(`${controls.dataset.syncUrl}?force=${force ? '1' : '0'}`, {
                method: 'POST', credentials: 'same-origin', headers: {'X-CSRFToken': controls.dataset.csrf}
            });
            if (!response.ok) throw new Error('Unable to start sync. Reload and try again.');
            for (let attempt = 0; attempt < 300; attempt++) {
                await new Promise(resolve => setTimeout(resolve, 2000));
                const run = await status();
                if (run?.status === 'completed' && run.finished_at !== before?.finished_at) {
                    window.location.reload();
                    return;
                }
                if (run?.status === 'failed' && run.started_at !== before?.started_at) {
                    throw new Error('Sync failed. Try again later; saved transactions are retained.');
                }
                if (!force && run?.status !== 'running' && run?.last_success_at && Date.now() - Date.parse(run.last_success_at) < 60000) {
                    showLast(run);
                    return;
                }
            }
            message.textContent = 'Sync is taking longer than expected. Reload to check progress.';
        } catch (error) {
            if (timestamp.textContent === 'Last synced: checking…') timestamp.textContent = 'Last synced: unavailable';
            message.textContent = error.message;
        } finally {
            busy = false;
            buttons.forEach(button => { button.disabled = false; });
        }
    }
    buttons.forEach(button => button.addEventListener('click', () => sync(true)));
    sync(false);
})();
