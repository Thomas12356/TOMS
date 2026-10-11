// Fetch balances after the page loads, so the transaction table stays responsive.
async function loadBalances() {
    const container = document.getElementById("account-balances");
    if (!container) return;
    const refresh = document.getElementById('refresh-balances');
    const freshness = document.getElementById('balance-freshness');
    if (refresh?.disabled) return;
    if (refresh) refresh.disabled = true;
    if (freshness) freshness.textContent = 'Requesting balances…';

    try {
        const response = await fetch(container.dataset.url, {credentials: "same-origin", cache: "no-store"});
        if (!response.ok) throw new Error("Balance request failed");
        const data = await response.json();
        if (freshness) {
            const checked = new Date(data.checked_at);
            freshness.textContent = Number.isNaN(checked.getTime()) ? 'Balance response timestamp unavailable.' :
                `${data.sample ? 'Example balances displayed' : 'Balance response received'}: ${checked.toLocaleString('en-GB', {timeZone: 'Europe/London'})} Europe/London. Refresh again when needed.`;
            if (data.balances.some(balance => balance.error)) freshness.textContent += ' One or more balances are unavailable.';
        }
        container.replaceChildren();

        const displayedBalances = container.dataset.showAccounts === 'true' ? data.balances : (data.totals ?? data.balances);
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
            if (balance.tax_reserved !== undefined) {
                const breakdown = document.createElement('p');
                breakdown.className = 'balance-breakdown';
                breakdown.textContent = `Earmarked: ${balance.tax_reserved} tax · ${balance.ni_reserved} NI`;
                const remaining = document.createElement('p');
                remaining.className = balance.underfunded ? 'balance-error' : 'secondary';
                remaining.textContent = balance.unassigned === null ? 'Unassigned balance unavailable' : `After earmarks: ${balance.unassigned}`;
                card.append(breakdown, remaining);
                if (balance.underfunded) {
                    const warning = document.createElement('p');
                    warning.className = 'balance-error';
                    warning.textContent = 'Current balance does not fully cover these earmarks.';
                    card.append(warning);
                }
            }
            container.append(card);
        }
    } catch (error) {
        container.textContent = refresh ? 'Balances are unavailable. Use Refresh bank balances to try again.' : "Balances are unavailable. Reload the page to try again.";
        if (freshness) freshness.textContent = 'Latest refresh failed; bank balances are unavailable.';
    } finally {
        if (refresh) refresh.disabled = false;
    }
}

const balanceContainer = document.getElementById('account-balances');
if (balanceContainer?.dataset.manual === 'true') document.getElementById('refresh-balances')?.addEventListener('click', loadBalances);
else loadBalances();
