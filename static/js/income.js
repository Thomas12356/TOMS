// Payroll NI belongs to employment; retain typed values when switching back.
(() => {
    const stream = document.getElementById('income-stream');
    const fields = document.querySelector('[data-payroll-ni]');
    if (!stream || !fields) return;
    const update = () => {
        const employed = stream.selectedOptions[0]?.dataset.employed === 'true';
        fields.hidden = !employed;
        fields.querySelector('input').disabled = !employed;
    };
    stream.addEventListener('change', update);
    update();
})();
