"""Deterministic financial validation engine (Python Decimal only).

The engine recomputes every relationship it can find on the document and
reports, for each check, the extracted value, the recalculated value and the
difference. It NEVER modifies extracted values. When a check fails it tries to
explain *why* (tax-inclusive prices, tax applying to a subset of items,
rounding) and proposes suggestions only when the expected value is a plausible
OCR misreading of the printed value (digit confusion, a currency symbol read as
a digit, a lost decimal point). Suggestions are applied only by a human.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from itertools import combinations

from app.schemas.extraction import (
    ExtractedField,
    ExtractionResult,
    LineItem,
    Suggestion,
    TaxLine,
    ValidationCheck,
    ValidationReport,
)

from .normalize.money import dec, minor_units, quantize

CONFUSIONS: dict[str, str] = {
    "0": "869",
    "1": "74",
    "2": "7",
    "3": "858",
    "4": "19",
    "5": "638",
    "6": "580",
    "7": "12",
    "8": "30695",
    "9": "840",
}


def _s(v: Decimal | None, places: int) -> str | None:
    if v is None:
        return None
    q = quantize(v, max(places, 0))
    return f"{q:f}"


def _digit_variants(value: Decimal) -> dict[Decimal, str]:
    """Plausible OCR misreadings of ``value`` -> reason."""
    out: dict[Decimal, str] = {}
    sign = -1 if value < 0 else 1
    s = f"{abs(value):f}"
    int_part = s.split(".")[0]
    # A currency symbol (₹, $, €) misread as a leading digit: "76,726.00" / "86,726.00" for "₹6,726.00".
    if len(int_part) > 1 and s[0] in "7218934":
        try:
            out[Decimal(s[1:]) * sign] = f"leading '{s[0]}' may be a misread currency symbol"
        except Exception:  # noqa: BLE001
            pass
    for i, ch in enumerate(s):
        if ch.isdigit():
            for alt in CONFUSIONS.get(ch, ""):
                cand = s[:i] + alt + s[i + 1 :]
                try:
                    out.setdefault(Decimal(cand) * sign, f"digit '{ch}' may be misread (OCR confusion with '{alt}')")
                except Exception:  # noqa: BLE001
                    continue
    # Lost decimal point: 450000 printed as 4500.00
    if "." not in s and len(s) > 2:
        out.setdefault(Decimal(s) / 100 * sign, "decimal point may be missing")
    # Missing minus sign
    out.setdefault(-value, "sign may be misread")
    out.pop(value, None)
    return out


class _Engine:
    def __init__(self, ex: ExtractionResult):
        self.ex = ex
        self.mu = minor_units(ex.currency.code) if ex.currency.code else 2
        self.unit = Decimal(1).scaleb(-self.mu)
        self.half = self.unit / 2
        self.checks: list[ValidationCheck] = []
        self.reasons: list[str] = []
        self.prices_include_tax: bool | None = ex.prices_include_tax
        self._n = 0
        self.field_index: dict[str, ExtractedField] = {}
        for li in ex.line_items:
            for k, f in li.fields().items():
                self.field_index[f"line_items.{li.id}.{k}"] = f
        for t in ex.taxes:
            for k, f in t.fields().items():
                self.field_index[f"taxes.{t.id}.{k}"] = f
        for k, f in ex.summary.items():
            self.field_index[f"summary.{k}"] = f
        for k, f in ex.header.items():
            self.field_index[f"header.{k}"] = f

    # ------------------------------------------------------------------ helpers
    def v(self, f: ExtractedField | None) -> Decimal | None:
        if f is None or f.value is None:
            return None
        return dec(f.value)

    def fmt(self, v: Decimal | None) -> str | None:
        return _s(v, self.mu)

    def add(
        self,
        category: str,
        label: str,
        status: str,
        message: str,
        fields: list[str],
        expected: Decimal | None = None,
        extracted: Decimal | None = None,
        suggestions: list[Suggestion] | None = None,
    ) -> ValidationCheck:
        self._n += 1
        diff = None
        if expected is not None and extracted is not None:
            diff = self.fmt(extracted - expected)
        chk = ValidationCheck(
            id=f"c{self._n}",
            category=category,  # type: ignore[arg-type]
            label=label,
            expected=self.fmt(expected) if expected is not None else None,
            extracted=self.fmt(extracted) if extracted is not None else None,
            difference=diff,
            status=status,  # type: ignore[arg-type]
            message=message,
            fields=fields,
            suggestions=suggestions or [],
        )
        self.checks.append(chk)
        if category in ("consistency", "format") or status == "skipped":
            return chk
        validated = status in ("pass", "explained", "warning")
        if not validated and suggestions:
            # a plausible single-field OCR fix exists: blame that field, not its neighbours
            blamed = {s.path for s in suggestions}
        else:
            blamed = set(fields)
        for p in fields:
            f = self.field_index.get(p)
            if f is None:
                continue
            if not validated:
                # a value confirmed by an independent passing check is not the culprit
                if p in blamed and f.validated is not True:
                    f.validated = False
            elif f.validated is None:
                f.validated = True
        return chk

    def suggest(self, solvers: dict[str, Callable[[], Decimal | None]]) -> list[Suggestion]:
        """For each field, compute the value that would satisfy the check and keep it
        only if it is a plausible OCR misreading of the printed value."""
        out: list[Suggestion] = []
        for path, solve in solvers.items():
            f = self.field_index.get(path)
            if f is None or f.value is None:
                continue
            cur = dec(f.value)
            if cur is None:
                continue
            try:
                need = solve()
            except Exception:  # noqa: BLE001
                need = None
            if need is None:
                continue
            need_q = quantize(need, max(self.mu, 0))
            for cand, why in _digit_variants(cur).items():
                if abs(cand - need_q) <= self.half:
                    out.append(Suggestion(path=path, current=f.value, suggested=self.fmt(need_q) or "", reason=why))
                    break
        return out

    # ------------------------------------------------------------- line checks
    def line_checks(self) -> None:
        for li in self.ex.line_items:
            base = f"line_items.{li.id}"
            q, r, a = self.v(li.quantity), self.v(li.rate), self.v(li.amount)
            d = self.v(li.discount)
            tr, ta, lt = self.v(li.tax_rate), self.v(li.tax_amount), self.v(li.line_total)
            name = (li.name.value or f"Item {li.index + 1}")[:40]
            computed: Decimal | None = None
            if q is not None and r is not None:
                gross = q * r
                disc = Decimal(0)
                if d is not None:
                    disc = gross * d / 100 if li.discount.kind == "percent" else abs(d)
                computed = gross - disc
                li.computed_amount = self.fmt(computed)
            if computed is not None and a is not None:
                diff = a - computed
                tol_round = max(self.half, abs(q or 0) * self.half) + self.half
                fields = [f"{base}.quantity", f"{base}.rate", f"{base}.amount"] + ([f"{base}.discount"] if d is not None else [])
                if abs(diff) <= self.half:
                    self.add("line", f"{name}: qty × rate", "pass", "Quantity × rate matches the line amount.", fields, computed, a)
                elif abs(diff) <= tol_round:
                    self.add("line", f"{name}: qty × rate", "warning",
                             "Differs by less than the rounding of the displayed rate.", fields, computed, a)
                elif tr is not None and abs(a - quantize(computed * (1 + tr / 100), self.mu)) <= self.unit:
                    self.add("line", f"{name}: qty × rate", "explained",
                             f"Line amount includes {tr}% tax.", fields + [f"{base}.tax_rate"], computed, a)
                else:
                    sugg = self.suggest({
                        f"{base}.amount": lambda c=computed: c,
                        f"{base}.rate": lambda q=q, a=a: (a / q) if q else None,
                        f"{base}.quantity": lambda r=r, a=a: (a / r) if r else None,
                    })
                    self.add("line", f"{name}: qty × rate", "fail",
                             f"Quantity × rate = {self.fmt(computed)} but the line shows {self.fmt(a)}.",
                             fields, computed, a, sugg)
            elif a is None and computed is None and (r is not None or q is not None):
                pass
            # line-level tax
            tax_base = a if a is not None else computed
            if tr is not None and ta is not None and tax_base is not None:
                exp = quantize(tax_base * tr / 100, self.mu)
                st = "pass" if abs(ta - exp) <= self.unit else "fail"
                self.add("tax", f"{name}: line tax", st,
                         "Line tax matches rate × amount." if st == "pass" else f"{tr}% of {self.fmt(tax_base)} is {self.fmt(exp)}.",
                         [f"{base}.tax_rate", f"{base}.tax_amount", f"{base}.amount"], exp, ta)
            if lt is not None and tax_base is not None:
                if ta is not None:
                    exp = tax_base + ta
                elif tr is not None:
                    exp = quantize(tax_base * (1 + tr / 100), self.mu)
                else:
                    exp = tax_base
                st = "pass" if abs(lt - exp) <= self.unit else "fail"
                self.add("line", f"{name}: line total", st,
                         "Line total is consistent." if st == "pass" else f"Expected line total {self.fmt(exp)}.",
                         [f"{base}.line_total", f"{base}.amount"], exp, lt)

    # --------------------------------------------------------------- subtotal
    def item_amounts(self) -> list[tuple[LineItem, Decimal]]:
        out = []
        for li in self.ex.line_items:
            a = self.v(li.amount)
            if a is None and li.computed_amount is not None:
                a = dec(li.computed_amount)
            if a is not None:
                out.append((li, a))
        return out

    def subtotal_check(self) -> Decimal | None:
        sub = self.v(self.ex.summary.get("subtotal"))
        amounts = self.item_amounts()
        if not amounts:
            return sub
        total_items = sum((a for _, a in amounts), Decimal(0))
        missing = len(self.ex.line_items) - len(amounts)
        if sub is None:
            return total_items if missing == 0 else None
        fields = ["summary.subtotal"] + [f"line_items.{li.id}.amount" for li, _ in amounts]
        tol = max(self.unit, self.half * len(amounts))
        if missing:
            self.add("subtotal", "Sum of line items", "skipped",
                     f"{missing} line item(s) have no amount; subtotal cannot be fully verified.", fields, total_items, sub)
            return sub
        if abs(sub - total_items) <= self.half:
            self.add("subtotal", "Sum of line items = subtotal", "pass", "Line items add up to the subtotal.", fields, total_items, sub)
        elif abs(sub - total_items) <= tol:
            self.add("subtotal", "Sum of line items = subtotal", "warning", "Small rounding difference.", fields, total_items, sub)
        else:
            # tax-inclusive line amounts?
            line_tax = sum((self.v(li.tax_amount) or Decimal(0) for li, _ in amounts), Decimal(0))
            if line_tax and abs(sub - (total_items - line_tax)) <= tol:
                self.add("subtotal", "Sum of line items = subtotal", "explained",
                         "Line amounts include tax; subtotal is before tax.", fields, total_items - line_tax, sub)
                return sub
            solvers: dict[str, Callable[[], Decimal | None]] = {"summary.subtotal": lambda: total_items}
            for li, a in amounts:
                solvers[f"line_items.{li.id}.amount"] = lambda a=a: sub - (total_items - a)
            sugg = self.suggest(solvers)
            # A line whose own qty×rate fails while subtotal works with computed amounts.
            computed_total = sum((dec(li.computed_amount) or self.v(li.amount) or Decimal(0)) for li in self.ex.line_items)
            if not sugg and abs(computed_total - sub) <= tol:
                for li in self.ex.line_items:
                    if li.computed_amount and li.amount.value and dec(li.computed_amount) != dec(li.amount.value):
                        sugg.append(Suggestion(path=f"line_items.{li.id}.amount", current=li.amount.value,
                                               suggested=self.fmt(dec(li.computed_amount)) or "",
                                               reason="qty × rate reconciles with the subtotal"))
            self.add("subtotal", "Sum of line items = subtotal", "fail",
                     f"Line items add up to {self.fmt(total_items)} but the subtotal shows {self.fmt(sub)}.",
                     fields, total_items, sub, sugg)
        return sub

    # -------------------------------------------------------------------- tax
    def _subset_for_tax(self, rate: Decimal, amount: Decimal, tax: TaxLine) -> list[LineItem] | None:
        items = self.item_amounts()
        if not items or len(items) > 18:
            return None
        tol = self.unit
        matches: list[tuple[LineItem, ...]] = []
        for size in range(1, len(items)):
            for combo in combinations(items, size):
                base = sum((a for _, a in combo), Decimal(0))
                if abs(quantize(base * rate / 100, self.mu) - amount) <= tol:
                    matches.append(tuple(li for li, _ in combo))
            if matches:
                break
        if len(matches) == 1:
            return list(matches[0])
        return None

    def tax_checks(self, sub: Decimal | None) -> Decimal:
        disc = self.v(self.ex.summary.get("discount_total"))
        taxable = self.v(self.ex.summary.get("taxable_amount"))
        base_fields = []
        if taxable is not None:
            base = taxable
            base_fields = ["summary.taxable_amount"]
        elif sub is not None:
            base = sub - (abs(disc) if disc is not None else Decimal(0))
            base_fields = ["summary.subtotal"] + (["summary.discount_total"] if disc is not None else [])
        else:
            base = None
        tax_sum = Decimal(0)
        types = {t.tax_type for t in self.ex.taxes if t.tax_type}
        for t in self.ex.taxes:
            amt = self.v(t.amount)
            rate = self.v(t.rate)
            own_base = self.v(t.base)
            label = t.label.value or t.tax_type or "Tax"
            if amt is not None:
                tax_sum += amt
            if amt is None or rate is None:
                continue
            b = own_base if own_base is not None else base
            fields = [f"taxes.{t.id}.amount", f"taxes.{t.id}.rate"] + ([f"taxes.{t.id}.base"] if own_base is not None else base_fields)
            if b is None:
                continue
            n_items = max(1, len(self.ex.line_items))
            tol = max(self.unit, self.half * n_items)
            exp = quantize(b * rate / 100, self.mu)
            if abs(amt - exp) <= tol:
                t.inclusive = False
                self.add("tax", f"{label}", "pass", f"{rate}% of {self.fmt(b)} = {self.fmt(exp)}.", fields, exp, amt)
                continue
            exp_incl = quantize(b * rate / (100 + rate), self.mu)
            if abs(amt - exp_incl) <= tol:
                t.inclusive = True
                self.prices_include_tax = True
                self.add("tax", f"{label}", "explained",
                         f"Matches {rate}% tax already included in {self.fmt(b)} (tax-inclusive prices).", fields, exp_incl, amt)
                continue
            subset = self._subset_for_tax(rate, amt, t) if own_base is None else None
            if subset:
                t.applies_to = [li.id for li in subset]
                names = ", ".join((li.name.value or f"item {li.index + 1}")[:30] for li in subset)
                sb = sum((self.v(li.amount) or dec(li.computed_amount) or Decimal(0)) for li in subset)
                self.add("tax", f"{label}", "explained",
                         f"{rate}% applies only to: {names} ({self.fmt(sb)}) — not the whole subtotal.",
                         fields + [f"line_items.{li.id}.amount" for li in subset], quantize(sb * rate / 100, self.mu), amt)
                continue
            sugg = self.suggest({f"taxes.{t.id}.amount": lambda e=exp: e})
            self.add("tax", f"{label}", "fail",
                     f"{rate}% of {self.fmt(b)} is {self.fmt(exp)}, but {self.fmt(amt)} is printed.", fields, exp, amt, sugg)
        # Consistency of Indian GST components.
        rates = {t.tax_type: self.v(t.rate) for t in self.ex.taxes if t.tax_type in ("CGST", "SGST", "UTGST")}
        if "CGST" in rates and ("SGST" in rates or "UTGST" in rates):
            other = rates.get("SGST", rates.get("UTGST"))
            if rates["CGST"] is not None and other is not None and rates["CGST"] != other:
                self.add("consistency", "CGST/SGST rates", "warning", "CGST and SGST/UTGST rates normally match.", [])
        if ("CGST" in types or "SGST" in types) and "IGST" in types:
            self.add("consistency", "GST type", "warning",
                     "Both intra-state (CGST/SGST) and inter-state (IGST) taxes appear — verify the place of supply.", [])
        tax_total = self.v(self.ex.summary.get("tax_total"))
        if tax_total is not None and self.ex.taxes and any(self.v(t.amount) is not None for t in self.ex.taxes):
            st = "pass" if abs(tax_total - tax_sum) <= self.unit else "fail"
            self.add("tax", "Sum of taxes = total tax", st,
                     "Tax lines add up to the total tax." if st == "pass" else "Tax lines do not add up to the total tax.",
                     ["summary.tax_total"] + [f"taxes.{t.id}.amount" for t in self.ex.taxes], tax_sum, tax_total)
        if not self.ex.taxes and tax_total is not None:
            return tax_total
        return tax_sum

    # ------------------------------------------------------------------ total
    def total_check(self, sub: Decimal | None, tax_sum: Decimal) -> Decimal | None:
        s = self.ex.summary
        gt = self.v(s.get("grand_total"))
        if sub is None:
            return None
        disc = self.v(s.get("discount_total"))
        ship = self.v(s.get("shipping"))
        other = self.v(s.get("other_charges"))
        rnd = self.v(s.get("rounding"))
        parts = sub - (abs(disc) if disc is not None else Decimal(0)) + (ship or 0) + (other or 0) + (rnd or 0)
        excl_total = parts + tax_sum
        incl_total = parts
        computed = incl_total if self.prices_include_tax else excl_total
        if gt is None:
            return computed
        fields = ["summary.grand_total", "summary.subtotal"] + [
            f"summary.{k}" for k in ("discount_total", "shipping", "other_charges", "rounding", "tax_total") if self.v(s.get(k)) is not None
        ] + [f"taxes.{t.id}.amount" for t in self.ex.taxes if self.v(t.amount) is not None]
        if abs(gt - computed) <= self.unit:
            self.add("total", "Grand total", "pass", "Subtotal, discounts, taxes and charges add up to the grand total.", fields, computed, gt)
            return computed
        if not self.prices_include_tax and tax_sum and abs(gt - incl_total) <= self.unit:
            self.prices_include_tax = True
            self.add("total", "Grand total", "explained", "Total equals the subtotal: prices are tax-inclusive.", fields, incl_total, gt)
            return incl_total
        if disc is not None and abs(gt - (excl_total + abs(disc))) <= self.unit:
            self.add("total", "Grand total", "explained", "Discount is already reflected in the subtotal.", fields, excl_total + abs(disc), gt)
            return excl_total + abs(disc)
        diff = gt - computed
        if rnd is None and abs(diff) < 1 and self.mu > 0:
            self.add("total", "Grand total", "warning",
                     f"Total differs by {self.fmt(diff)} — consistent with rounding to whole units.", fields, computed, gt)
            return computed
        solvers: dict[str, Callable[[], Decimal | None]] = {
            "summary.grand_total": lambda: computed,
            "summary.subtotal": lambda: gt - (computed - sub),
        }
        for t in self.ex.taxes:
            a = self.v(t.amount)
            if a is not None:
                solvers[f"taxes.{t.id}.amount"] = lambda a=a: a + diff
        if rnd is not None:
            solvers["summary.rounding"] = lambda: rnd + diff
        sugg = self.suggest(solvers)
        self.reasons.append(f"Grand total differs from the recalculated total by {self.fmt(diff)}.")
        self.add("total", "Grand total", "fail",
                 f"Recalculated total is {self.fmt(computed)} but the document shows {self.fmt(gt)}.", fields, computed, gt, sugg)
        return computed

    def balance_check(self) -> None:
        s = self.ex.summary
        gt, paid, bal = self.v(s.get("grand_total")), self.v(s.get("amount_paid")), self.v(s.get("balance_due"))
        if gt is None or bal is None:
            return
        if paid is None:
            if abs(gt - bal) > self.unit:
                self.add("balance", "Balance due", "warning",
                         "Balance due differs from the total but no payment line was found.", ["summary.balance_due", "summary.grand_total"], gt, bal)
            else:
                self.add("balance", "Balance due", "pass", "Balance due equals the total.", ["summary.balance_due", "summary.grand_total"], gt, bal)
            return
        exp = gt - abs(paid)
        fields = ["summary.grand_total", "summary.amount_paid", "summary.balance_due"]
        if abs(bal - exp) <= self.unit:
            self.add("balance", "Total − paid = balance", "pass", "Payment and balance reconcile.", fields, exp, bal)
            return
        sugg = self.suggest({
            "summary.amount_paid": lambda: (gt - bal) * (-1 if paid < 0 else 1),
            "summary.balance_due": lambda: exp,
            "summary.grand_total": lambda: bal + abs(paid),
        })
        self.add("balance", "Total − paid = balance", "fail",
                 f"Total − paid = {self.fmt(exp)} but balance shows {self.fmt(bal)}.", fields, exp, bal, sugg)

    def consistency_checks(self) -> None:
        seen: dict[tuple, LineItem] = {}
        for li in self.ex.line_items:
            key = ((li.name.value or "").strip().lower(), li.quantity.value, li.rate.value, li.amount.value)
            if key[0] and key in seen:
                self.add("consistency", "Possible duplicate row", "warning",
                         f"'{li.name.value}' appears twice with identical values (both rows kept).",
                         [f"line_items.{li.id}.name", f"line_items.{seen[key].id}.name"])
            else:
                seen[key] = li
        d1 = self.ex.header.get("document_date")
        d2 = self.ex.header.get("due_date")
        if d1 and d2 and d1.value and d2.value and len(d1.value) == 10 and len(d2.value) == 10 and d2.value < d1.value:
            self.add("consistency", "Due date", "warning", "Due date is before the document date.",
                     ["header.document_date", "header.due_date"])

    # -------------------------------------------------------------------- run
    def run(self) -> ValidationReport:
        for f in self.field_index.values():
            f.validated = None
        self.line_checks()
        sub = self.subtotal_check()
        tax_sum = self.tax_checks(sub)
        computed_total = self.total_check(sub, tax_sum)
        self.balance_check()
        self.consistency_checks()

        gt = self.v(self.ex.summary.get("grand_total"))
        statuses = {c.status for c in self.checks}
        has_money = bool(self.item_amounts()) or gt is not None
        if self.ex.document_type.value == "price_list" and gt is None:
            status = "not_applicable"
        elif not has_money:
            status = "incomplete"
        elif "fail" in statuses:
            status = "fail"
        elif "warning" in statuses:
            status = "warning"
        elif gt is None or not any(c.category in ("total", "subtotal") and c.status in ("pass", "explained") for c in self.checks):
            status = "incomplete"
        else:
            status = "pass"
        for c in self.checks:
            if c.status == "fail":
                self.reasons.append(c.message)
            for sg in c.suggestions:
                self.reasons.append(f"{sg.path}: {sg.reason} (expected {sg.suggested}).")
        computed_sub = None
        amts = self.item_amounts()
        if amts:
            computed_sub = sum((a for _, a in amts), Decimal(0))
        paid = self.v(self.ex.summary.get("amount_paid"))
        computed_balance = (computed_total - abs(paid)) if (computed_total is not None and paid is not None) else None
        return ValidationReport(
            status=status,  # type: ignore[arg-type]
            extracted_total=self.fmt(gt),
            recalculated_total=self.fmt(computed_total),
            difference=self.fmt(gt - computed_total) if (gt is not None and computed_total is not None) else None,
            prices_include_tax=self.prices_include_tax,
            computed={
                "subtotal": self.fmt(computed_sub),
                "tax_total": self.fmt(tax_sum) if self.ex.taxes else None,
                "grand_total": self.fmt(computed_total),
                "balance_due": self.fmt(computed_balance),
            },
            checks=self.checks,
            reasons=list(dict.fromkeys(self.reasons)),
        )


def validate(ex: ExtractionResult) -> ValidationReport:
    engine = _Engine(ex)
    report = engine.run()
    ex.prices_include_tax = report.prices_include_tax
    ex.validation = report
    return report
