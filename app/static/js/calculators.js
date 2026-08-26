/* Stamp duty / LBTT / LTT, mortgage repayment and gross yield.
 *
 * Lives here rather than inline in the report template because the
 * standalone calculator pages under /tools use the same maths, and
 * tax bands must have exactly one home. Two copies would drift the
 * first time a Budget moved a threshold, and the wrong one would
 * keep answering.
 *
 * Every element is looked up defensively: the script loads on pages
 * that carry only some of these controls, and on the report it sits
 * inside a dialog that may never be opened.
 */
(function () {
    const priceInput = document.getElementById('calc-price');
    if (!priceInput) return;

    const buyerType = document.getElementById('calc-buyer-type');
    const region = document.getElementById('calc-region');
    const sdltResult = document.getElementById('calc-sdlt-result');
    const sdltRate = document.getElementById('calc-sdlt-rate');
    const depositPct = document.getElementById('calc-deposit-pct');
    const rateInput = document.getElementById('calc-rate');
    const termInput = document.getElementById('calc-term');
    const mortgageResult = document.getElementById('calc-mortgage-result');
    const loanAmountEl = document.getElementById('calc-loan-amount');
    const rentInput = document.getElementById('calc-rent');
    const yieldResult = document.getElementById('calc-yield-result');

    // Marginal tax bands: [upper bound (Infinity for the top band), rate].
    // Rates as of April 2025 - always changeable by a future Budget.
    const BANDS = {
        'england-ni': {
            standard: [[125000, 0], [250000, 0.02], [925000, 0.05], [1500000, 0.10], [Infinity, 0.12]],
            ftb: [[300000, 0], [500000, 0.05]],  // above 500k: no relief, falls back to standard
            ftbCeiling: 500000,
            surcharge: 0.05,
        },
        scotland: {
            standard: [[145000, 0], [250000, 0.02], [325000, 0.05], [750000, 0.10], [Infinity, 0.12]],
            ftb: [[175000, 0], [250000, 0.02], [325000, 0.05], [750000, 0.10], [Infinity, 0.12]],
            ftbCeiling: Infinity,
            surcharge: 0.08,
        },
        wales: {
            standard: [[225000, 0], [400000, 0.06], [750000, 0.075], [1500000, 0.10], [Infinity, 0.12]],
            ftb: null,  // no first-time-buyer relief in Wales
            ftbCeiling: 0,
            surcharge: 0.05,
        },
    };

    function marginalTax(price, bands) {
        let tax = 0;
        let lower = 0;
        for (const [upper, rate] of bands) {
            if (price <= lower) break;
            const taxableInBand = Math.min(price, upper) - lower;
            tax += taxableInBand * rate;
            lower = upper;
        }
        return tax;
    }

    function calcTransactionTax() {
        const price = Math.max(0, Number(priceInput.value) || 0);
        const table = BANDS[region.value];
        const type = buyerType.value;

        let bands = table.standard;
        if (type === 'ftb' && table.ftb && price <= table.ftbCeiling) {
            bands = table.ftb;
        }
        let tax = marginalTax(price, bands);

        if (type === 'additional') {
            tax += price * table.surcharge;
        }

        sdltResult.textContent = '£' + Math.round(tax).toLocaleString('en-GB');
        sdltRate.textContent = price > 0 ? (tax / price * 100).toFixed(1) + '%' : '0%';
    }

    function calcMortgage() {
        const price = Math.max(0, Number(priceInput.value) || 0);
        const deposit = price * (Math.min(100, Math.max(0, Number(depositPct.value) || 0)) / 100);
        const loan = Math.max(0, price - deposit);
        const annualRate = Math.max(0, Number(rateInput.value) || 0) / 100;
        const months = Math.max(1, Number(termInput.value) || 1) * 12;
        const monthlyRate = annualRate / 12;

        let monthly;
        if (monthlyRate === 0) {
            monthly = loan / months;
        } else {
            monthly = loan * (monthlyRate * Math.pow(1 + monthlyRate, months)) / (Math.pow(1 + monthlyRate, months) - 1);
        }

        loanAmountEl.textContent = Math.round(loan).toLocaleString('en-GB');
        mortgageResult.textContent = '£' + Math.round(monthly).toLocaleString('en-GB') + '/mo';
    }

    function calcYield() {
        const price = Math.max(0, Number(priceInput.value) || 0);
        const rent = Math.max(0, Number(rentInput.value) || 0);
        if (price <= 0 || rent <= 0) {
            yieldResult.textContent = ' — ';
            return;
        }
        const annualRent = rent * 12;
        yieldResult.textContent = (annualRent / price * 100).toFixed(2) + '%';
    }

    function recalcAll() {
        calcTransactionTax();
        calcMortgage();
        calcYield();
    }

    [priceInput, buyerType, region, depositPct, rateInput, termInput, rentInput].forEach(function (el) {
        el.addEventListener('input', recalcAll);
    });

    const countryToRegion = { 'Scotland': 'scotland', 'Wales': 'wales' };
    // The page may preselect a nation (a report knows where the
    // property is); a standalone calculator simply defaults.
    region.value = countryToRegion[window.UKI_CALC_COUNTRY || ''] || 'england-ni';

    recalcAll();
})();
