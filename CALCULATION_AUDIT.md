# Calculation, tax scope and usability audit

Reviewed 11 October 2026 on `codex/tax-calculation-legislation-audit`.
This audits supported 2026–27 England/Wales/Northern Ireland earnings planning.
It is not certification of a complete tax return or an individual tax assessment.
Working financial records and live bank traffic were not used or changed.

## Five-task walkthrough

[USABILITY_WALKTHROUGH.md](USABILITY_WALKTHROUGH.md) asks the owner to practise:

1. Find a payment needing attention on Home; review, save and confirm its details.
2. Create an income source and finish its forecast or individual-shift setup.
3. Log work and a qualifying journey; reuse defaults and find an older record.
4. Check Account money, refresh balances and compare earmarks with the target.
5. Resolve invoice/receipt matching using explicit allocations and check totals.

Run in sample mode. Record independent completion, the first confusing label,
unexpected destination and repeated entry. Automated checks cannot tell us where
the owner hesitates. Actual owner observations remain outstanding.

## Supported rules and calculation findings

| Area | Evidence and result |
| --- | --- |
| Income tax | £12,570 standard allowance; £1 reduction per £2 over £100,000; zero at £125,140. Main earnings rates 20%, 40%, 45%. Independent effective-rate totals matched 20,802 inputs, including ±£1 in pennies around each breakpoint. |
| Class 4 NI | Combined business profit has one £12,570 lower / £50,270 upper threshold, with 6% / 2% rates. Employment excluded from that profit. Independent totals matched the same 20,802 inputs. Class 1 remains recorded payroll evidence, not a tax credit. |
| Class 2 | No compulsory cash charge added. Current small-profits threshold £7,105 and voluntary rate £3.65/week match HMRC. Voluntary contributions are outside the calculation. |
| Mileage | 2026–27 car/van rates are 55p for the first 10,000 business miles, then 25p; motorcycle 24p; employee bicycle 20p. HMRC's 11,000-mile example produces £5,750 deduction. Employee reimbursement reduces the annual approved amount, floored at zero. |
| Mileage pooling | A trade shares the car/van threshold across qualifying vehicles. Separate employments normally have separate limits; associated employments share one. The owner must configure related sources correctly. Vehicle-method eligibility and prior-year capital allowances cannot be inferred from bank records. |
| Actual withholding | CIS is an advance credit, not an expense or extra income. TOMS uses logged deductions rather than guessing 20%/30% of a deposit. Received-income reserves apportion the annual business charge before subtracting credits once. |
| Forecast and work | Calendar-day proration and the stated 52-week/12-month convention; unpaid absence and explicit overtime; Decimal hourly work with elapsed UTC time and breaks; unknown/foreign-currency forecasts block ready targets. These are planning conventions, not HMRC payroll calculations. |
| Invoices and matching | Issued snapshots stay fixed. Allocations consume separate actual cash/tax/NI budgets; changed receipts become stale. Forecasts, work, invoices and receipts are separate evidence, never summed as extra income. Equal aggregate totals do not prove each shift was paid. |

All four current GOV.UK publications passed TOMS's actual content validators in a
temporary cache: income tax, NI, business mileage and employee mileage. Pinned
rates were retained; working caches were not rewritten. Three new regression
tests retain the independent tax/NI comparisons and the official mileage example.
The deterministic input set spans £0–£1,000,000 with explicit penny boundaries.
The independent oracle checks TOMS's penny-level planning convention; it does not
validate HMRC tax-return field rounding or an individual's final assessment.

## Remaining hassle and limits, in priority order

| Priority | Finding | Workaround now | Next decision |
| --- | --- | --- | --- |
| High | The savings target excludes payments on account and direct HMRC payments. A correct annual liability is not the cash due on 31 January/31 July. | Check the actual HMRC statement alongside TOMS; keep those deadlines and payments separately. | Add a separate statement/payment schedule and recorded HMRC payments, with explicit year and credit treatment. Do not guess a bill from the current forecast. |
| High | Tax assumptions are text rather than a confirmed owner tax profile. Scotland journey location currently adds a blocker, although journey location does not establish tax residence. Conversely an EWNI journey does not establish EWNI residence. | Use the estimate only within its stated EWNI scope. Keep the real journey location; do not relabel it to bypass a warning. Verify tax status separately if travelling or living across borders. | Confirm supported tax jurisdiction explicitly, separate residence from journey location, and retain a block for unsupported Scottish tax. This needs a persisted/versioned setting and migration, so is recorded rather than bypassing the current guard. |
| High | Trading allowance, pensions/Gift Aid and other reliefs, loss relief, capital allowances, savings/dividends, student loans, NI age/special-occupation exemptions and mixed Class 1/4 maximum adjustments are excluded. | Reconcile the planning estimate with HMRC/accountant figures for any applicable item. Do not enter a tax credit to simulate a relief. | Prioritise applicable omissions using the owner's tax profile; trading allowance must be an alternative to expenses, not an additional deduction. |
| Medium | Forecast/work/invoice/receipt distinctions and manual invoice allocations still require financial knowledge. | Work from Home's next action and one source workspace. Use sample mode to practise matching. | Observe the five tasks before adding more automation; improve the first unclear transition and explain suggested matches before saving. |
| Medium | Forms remain long and routine dates/miles/eligibility must be supplied each time. | Reuse saved work/journey defaults; use source/date history filters. | Add named presets only where observation establishes repeated entry; preserve fresh actuals and eligibility. |
| Medium | Earmarks are saved labels, balances require refresh, and committed bills/available-to-spend cash are not calculated. | Refresh explicitly and compare earmarks with current cash and external commitments. | Design stored balance freshness, commitments and backup coverage before displaying an available-to-spend figure. |
| Later | Conservative suggestions require consistent confirmed history and stable references; some real payers will never get a suggestion. | Choose the source manually and confirm the evidence. | Broader rules need conflict detection, clear criteria and preview; retain explicit save. |

The audit found no discrepancy in the tested supported arithmetic or current
rates. The jurisdiction/location guard is an actual conservative scope issue,
and the excluded features remain gaps for complete tax/payment planning.
No production calculation or financial schema was changed by this audit.

## Official evidence

- [Income tax rates and allowance taper](https://www.gov.uk/income-tax-rates).
- [Finance Act 2026, section 2: earnings rates](https://www.legislation.gov.uk/ukpga/2026/11/pdfs/ukpga_20260011_en.pdf).
- [Self-employed NI](https://www.gov.uk/self-employed-national-insurance-rates) and [2026 NI threshold regulations](https://www.legislation.gov.uk/uksi/2026/231/made/data.html).
- [Business mileage](https://www.gov.uk/simpler-income-tax-simplified-expenses/vehicles) and [employee mileage/reimbursement](https://www.gov.uk/tax-relief-for-employees/vehicles-you-use-for-work).
- [Taxation (Energy and Vehicles) Act 2026, section 2](https://www.legislation.gov.uk/ukpga/2026/26/pdfs/ukpga_20260026_en.pdf): mileage increase effective for 2026–27 onwards.
- [Original simplified-expenses provisions, Finance Act 2013 Schedule 5, sections 94D–94F](https://www.legislation.gov.uk/ukpga/2013/29/pdfs/ukpga_20130029_en.pdf): combined trade mileage limit, with rates since amended.
- [Associated employments](https://www.gov.uk/hmrc-internal-manuals/employment-income-manual/eim31280) and [vehicle method/eligibility](https://www.gov.uk/hmrc-internal-manuals/business-income-manual/bim75005).
- [CIS credits](https://www.gov.uk/what-you-must-do-as-a-cis-subcontractor/pay-tax-and-claim-back-deductions), [payments on account](https://www.gov.uk/understand-self-assessment-bill/payments-on-account), [trading allowance versus expenses](https://www.gov.uk/guidance/tax-free-allowances-on-property-and-trading-income), and [Scottish tax status](https://www.gov.uk/scottish-income-tax/who-pays).

## Verification

Final full PostgreSQL backend run: 556 tests, 552 passed and four optional backup
integration checks skipped. All 21 browser scripts passed in disposable fixtures,
with mocked bank traffic. Ruff F, Bandit and `git diff --check` passed. Official
publication checks and independent comparisons passed as described above.
Optional backup integration checks require separate isolated CREATEDB credentials
and remain outside this run. No working financial data, owner setting, deployment
or production calculation was changed.
