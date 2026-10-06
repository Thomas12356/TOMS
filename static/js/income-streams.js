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
