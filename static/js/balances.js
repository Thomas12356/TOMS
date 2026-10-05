// Fetch balances after the page loads, so the transaction table stays responsive.
async function loadBalances() {
    const container = document.getElementById("account-balances");
    if (!container) return;

    try {
        const response = await fetch(container.dataset.url, {credentials: "same-origin", cache: "no-store"});
        if (!response.ok) throw new Error("Balance request failed");
        const data = await response.json();
        container.replaceChildren();

        const displayedBalances = data.totals ?? data.balances;
        if (displayedBalances.length === 0) {
            container.textContent = "Import your accounts to see their balances here.";
            return;
        }

        for (const balance of displayedBalances) {
            const card = document.createElement("article");
            card.className = "balance-card";
            const name = document.createElement("h3");
            name.textContent = balance.name;
            const amount = document.createElement("p");
            amount.className = balance.error ? "balance-error" : "balance-amount";
            // textContent displays bank data as text, never as executable HTML.
            amount.textContent = balance.error || balance.amount;
            card.append(name, amount);
            container.append(card);
        }
    } catch (error) {
        container.textContent = "Balances are unavailable. Reload the page to try again.";
    }
}

loadBalances();
