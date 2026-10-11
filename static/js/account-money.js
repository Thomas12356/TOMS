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
            window.location.assign(response.url.split('#')[0] + '#account-reserves');
        } catch (failure) {
            error.textContent = failure.message;
            error.hidden = false;
            button.disabled = false;
        }
    });
});
