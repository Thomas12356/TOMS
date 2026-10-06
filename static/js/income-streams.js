// Keep the annual deduction amount visible only when the owner enters one.
// The server validates these choices too, including when JavaScript is disabled.
document.querySelectorAll('[data-withholding-mode]').forEach((select) => {
    const form = select.closest('form');
    const amount = form.querySelector('[data-withholding-amount]');
    const input = amount.querySelector('input');
    const kind = form.querySelector('[name="kind"]');
    const update = () => {
        const paye = select.querySelector('[value="paye_estimate"]');
        paye.disabled = kind.value !== 'employed';
        if (paye.disabled && select.value === 'paye_estimate') select.value = 'unknown';
        const manual = select.value === 'manual';
        amount.hidden = !manual;
        input.disabled = !manual;
        input.required = manual;
    };
    select.addEventListener('change', update);
    kind.addEventListener('change', update);
    update();
});
