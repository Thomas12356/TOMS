// Changing accounts submits the existing GET form and starts on page one.
const accountSelector = document.getElementById("account");
if (accountSelector) {
    accountSelector.addEventListener("change", () => {
        accountSelector.form.requestSubmit();
    });
}
