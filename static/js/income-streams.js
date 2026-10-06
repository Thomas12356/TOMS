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
