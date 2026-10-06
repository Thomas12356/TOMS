// Select a form through a normal GET, so links and validation work without JS.
const deductionType = document.getElementById('deduction-type');
if (deductionType) {
    deductionType.addEventListener('change', () => deductionType.form.requestSubmit());
}

// Keep shift choices within their stream; the server enforces the same rule.
document.querySelectorAll('[data-shift-selector]').forEach((select) => {
    const stream = document.getElementById(select.dataset.streamSelector);
    const filterShifts = () => {
        for (const option of select.options) {
            option.disabled = Boolean(option.value && option.dataset.streamId !== stream.value);
            option.hidden = option.disabled;
        }
        if (select.selectedOptions[0]?.disabled) select.value = '';
    };
    stream.addEventListener('change', filterShifts);
    filterShifts();
});
