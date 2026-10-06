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

// Suggest journey details without replacing anything the owner has typed.
const mileageShift = document.getElementById('mileage-shift');
if (mileageShift) {
    const date = document.getElementById('journey-date');
    const purpose = document.getElementById('mileage-purpose');
    const edited = new Set();
    // Retained POST values are already the owner's edits, even before typing.
    if (mileageShift.form.dataset.retained === 'true') {
        edited.add(date);
        edited.add(purpose);
    }
    [date, purpose].forEach(field => field.addEventListener('input', () => edited.add(field)));
    const suggestJourney = () => {
        const option = mileageShift.selectedOptions[0];
        if (!edited.has(date) || !date.value) date.value = option?.dataset.journeyDate || date.dataset.defaultDate;
        if (!edited.has(purpose) || !purpose.value) purpose.value = option?.dataset.journeyPurpose || '';
    };
    mileageShift.addEventListener('change', suggestJourney);
    document.getElementById('mileage-stream').addEventListener('change', suggestJourney);
}
