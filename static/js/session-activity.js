// Only trusted user input renews inactivity. Polling and tab visibility do not.
(() => {
    const control = document.getElementById('session-activity');
    if (!control) return;
    const message = document.getElementById('session-activity-error');
    let lastSent = -Infinity;
    let busy = false;
    let expired = false;
    async function activity(event) {
        if (!event.isTrusted || document.hidden || busy || expired || performance.now() - lastSent < 60000) return;
        busy = true;
        lastSent = performance.now();
        try {
            const response = await fetch(control.dataset.url, {
                method: 'POST', credentials: 'same-origin', redirect: 'error',
                signal: AbortSignal.timeout(10000),
                headers: {'X-CSRFToken': control.dataset.csrf},
            });
            if (response.status === 401 || response.status === 400) {
                expired = true;
                message.replaceChildren(response.status === 401 ? 'Your session expired. ' : 'This page could not verify your session. ');
                const link = document.createElement('a');
                link.href = control.dataset.loginUrl;
                link.textContent = 'Sign in again';
                message.append(link, '. Unsaved entries remain on this page.');
                message.hidden = false;
            } else if (!response.ok) throw new Error('Unable to renew your session. Check your connection or sign in again before saving.');
            else {
                message.hidden = true;
                // Keep long-lived forms usable without replacing unsaved fields.
                const token = response.headers.get('X-CSRFToken');
                if (token) {
                    document.querySelectorAll('[data-csrf]').forEach(element => { element.dataset.csrf = token; });
                    document.querySelectorAll('input[name="csrf_token"]').forEach(input => { input.value = token; });
                }
            }
        } catch (error) {
            message.textContent = 'Unable to renew your session. Check your connection or sign in again before saving.';
            message.hidden = false;
        } finally {
            busy = false;
        }
    }
    for (const type of ['pointerdown', 'pointermove', 'keydown', 'wheel', 'touchstart']) {
        document.addEventListener(type, activity, {passive: true});
    }
})();
