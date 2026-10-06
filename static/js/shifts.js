// Both modes are accepted by the server; only the chosen amount is submitted.
const shiftPaymentMode = document.getElementById('shift-payment-mode');
if (shiftPaymentMode) {
    const updatePayFields = () => {
        document.querySelectorAll('[data-shift-hourly], [data-shift-total]').forEach((group) => {
            const active = group.hasAttribute('data-shift-hourly') === (shiftPaymentMode.value === 'hourly');
            group.hidden = !active;
            const input = group.querySelector('input');
            input.disabled = !active;
            input.required = active;
        });
    };
    shiftPaymentMode.addEventListener('change', updatePayFields);
    updatePayFields();
}
