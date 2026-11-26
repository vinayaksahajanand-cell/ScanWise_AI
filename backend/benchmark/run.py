"""Run the extraction benchmark and report measured accuracy.

Usage:
  python -m benchmark.run --data benchmark/data [--variants pdf,scan,photo] [--llm off|always] [--limit N]

Metrics (per variant):
  CER / WER                 character / word error rate of OCR text vs. the strings drawn
  header field accuracy     invoice no., dates, vendor, customer
  item / rate / qty / unit / amount / category accuracy   (on matched rows)
  row association           rows where qty, rate and amount all land in the right row
  tax accuracy, total accuracy, currency accuracy
  missed rows / false-positive rows
  reconciliation            documents whose arithmetic checks out after extraction
  silent error rate         wrong values NOT flagged for review  (the number that matters)
  review rate               share of values a person is asked to check
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import time
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path

from rapidfuzz import fuzz, process
from rapidfuzz.distance import Levenshtein

from app.config import get_settings
from app.pipeline.normalize.units import normalize_unit
from app.pipeline.orchestrator import PageSource, Pipeline

FLAGGED = {"needs_review", "unreadable", "mismatch"}


def D(v) -> Decimal | None:
    try:
        return Decimal(str(v)) if v not in (None, "") else None
    except InvalidOperation:
        return None


def norm(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def cer_wer(truth: list[str], texts: list[str]) -> tuple[int, int, int, int]:
    cands = [t for t in texts if t.strip()]
    chars = edits = words = wedits = 0
    for s in truth:
        if not cands:
            chars += len(s)
            edits += len(s)
            continue
        best = process.extractOne(s, cands, scorer=Levenshtein.distance)
        cand = best[0]
        chars += len(s)
        edits += min(best[1], len(s))
        sw, cw = s.split(), cand.split()
        words += len(sw)
        wedits += min(Levenshtein.distance(sw, cw), len(sw))
    return chars, edits, words, wedits


class Tally:
    def __init__(self, verbose: bool = False):
        self.ok = defaultdict(int)
        self.n = defaultdict(int)
        self.flag_stats = defaultdict(int)
        self.verbose = verbose
        self.doc = ""

    def field(self, name: str, correct: bool, status: str | None = None, got=None, want=None):
        self.n[name] += 1
        self.ok[name] += int(correct)
        if ":" in name:  # e.g. header:document_date also counts towards "header"
            group = name.split(":")[0]
            self.n[group] += 1
            self.ok[group] += int(correct)
        if status is not None:
            flagged = status in FLAGGED
            self.flag_stats["values"] += 1
            self.flag_stats["flagged"] += int(flagged)
            if not correct and not flagged:
                self.flag_stats["silent_errors"] += 1
            if not correct and flagged:
                self.flag_stats["caught_errors"] += 1
        if self.verbose and not correct:
            tag = "SILENT" if status is not None and status not in FLAGGED else ("flagged" if status else "")
            print(f"    {self.doc} {name}: got {got!r} want {want!r} [{status}] {tag}")

    def pct(self, name: str) -> str:
        return f"{100 * self.ok[name] / self.n[name]:.1f}%" if self.n[name] else "—"


def evaluate(truth: dict, ex, tokens: list[str], t: Tally, cer_acc: list[int]):
    t.doc = truth["id"]
    c, e, w, we = cer_wer(truth["text_strings"], tokens)
    for i, v in enumerate((c, e, w, we)):
        cer_acc[i] += v
    h = ex.header
    for key, gt in (("document_number", truth["document_number"]), ("document_date", truth["document_date"]),
                    ("due_date", truth["due_date"]), ("vendor_name", truth["vendor_name"]), ("customer_name", truth["customer_name"])):
        f = h.get(key)
        t.field("header:" + key, norm(f.value if f else None) == norm(gt), f.status if f and f.value else None, f.value if f else None, gt)
    t.field("currency", ex.currency.code == truth["currency"], "needs_review" if ex.currency.ambiguous else "high_confidence", ex.currency.code, truth["currency"])

    gt_rows = truth["items"]
    got = list(ex.line_items)
    used: set[int] = set()
    for r in gt_rows:
        best_j, best_s = None, 0.0
        for j, li in enumerate(got):
            if j in used:
                continue
            s = fuzz.token_set_ratio(norm(r["name"]), norm(li.name.value))
            if s > best_s:
                best_j, best_s = j, s
        if best_j is None or best_s < 80:
            t.field("rows_found", False, None, [li.name.value for li in got], r["name"])
            continue
        used.add(best_j)
        li = got[best_j]
        t.field("rows_found", True)
        t.field("item_name", norm(li.name.value) == norm(r["name"]), li.name.status, li.name.value, r["name"])
        q_ok = D(li.quantity.value) == D(r["quantity"])
        r_ok = D(li.rate.value) == D(r["rate"])
        a_ok = D(li.amount.value) == D(r["amount"])
        t.field("quantity", q_ok, li.quantity.status, li.quantity.value, r["quantity"])
        t.field("rate", r_ok, li.rate.status, li.rate.value, r["rate"])
        t.field("amount", a_ok, li.amount.status, li.amount.value, r["amount"])
        gu = normalize_unit(r["unit"])
        t.field("unit", (li.unit.value or "") == (gu.code if gu else ""), li.unit.status if li.unit.value else None)
        t.field("category", li.category.value == r["category"])
        t.field("row_association", q_ok and r_ok and a_ok)
    t.n["false_positive_rows"] += len(got)
    t.ok["false_positive_rows"] += len(got) - len(used)

    for gtax in truth["taxes"]:
        hit = any(D(x.rate.value) == D(gtax["rate"]) and D(x.amount.value) == D(gtax["amount"]) for x in ex.taxes)
        st = next((x.amount.status for x in ex.taxes if D(x.amount.value) == D(gtax["amount"])), None)
        t.field("tax", hit, st, [(x.label.value, x.rate.value, x.amount.value) for x in ex.taxes], gtax)
    for key in ("subtotal", "grand_total"):
        f = ex.summary.get(key)
        t.field("totals", D(f.value if f else None) == D(truth[key]), f.status if f and f.value else None, f.value if f else None, truth[key])
    t.field("reconciled", bool(ex.validation and ex.validation.status in ("pass", "warning")), None, ex.validation.status if ex.validation else None, "pass")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="benchmark/data")
    ap.add_argument("--variants", default="pdf,scan,photo")
    ap.add_argument("--llm", default="off")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default="benchmark/results")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()
    s = get_settings()
    docs = sorted(p for p in Path(a.data).iterdir() if (p / "truth.json").exists())
    if a.limit:
        docs = docs[: a.limit]
    report: dict[str, dict] = {}
    for variant in a.variants.split(","):
        t = Tally(a.verbose)
        cer_acc = [0, 0, 0, 0]
        times: list[float] = []
        for d in docs:
            truth = json.loads((d / "truth.json").read_text(encoding="utf-8"))
            if variant == "pdf":
                src = PageSource(1, "pdf", d / "doc.pdf", pdf_index=0)
            else:
                src = PageSource(1, "image", d / ("scan.png" if variant == "scan" else "photo.jpg"))
            t0 = time.perf_counter()
            res = Pipeline(s, llm_mode=a.llm).run([src])
            times.append(time.perf_counter() - t0)
            evaluate(truth, res.extraction, [tok.text for tok in res.pages[0].tokens if not tok.noise], t, cer_acc)
        fs = t.flag_stats
        report[variant] = {
            "documents": len(docs),
            "cer": f"{100 * cer_acc[1] / max(1, cer_acc[0]):.2f}%",
            "wer": f"{100 * cer_acc[3] / max(1, cer_acc[2]):.2f}%",
            **{k: t.pct(k) for k in ("header", "currency", "rows_found", "item_name", "quantity", "rate", "unit", "amount", "category",
                                     "row_association", "tax", "totals", "reconciled")},
            "false_positive_rows": f"{100 * t.ok['false_positive_rows'] / max(1, t.n['false_positive_rows']):.1f}%",
            "review_rate": f"{100 * fs['flagged'] / max(1, fs['values']):.1f}%",
            "silent_error_rate": f"{100 * fs['silent_errors'] / max(1, fs['values']):.2f}%",
            "errors_caught": f"{fs['caught_errors']}/{fs['caught_errors'] + fs['silent_errors']}",
            "avg_seconds": f"{statistics.mean(times):.1f}",
        }
        print(f"[{variant}] done: {report[variant]}")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "latest.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    keys = list(next(iter(report.values())).keys())
    lines = ["| Metric | " + " | ".join(report) + " |", "|---|" + "---:|" * len(report)]
    for k in keys:
        lines.append(f"| {k.replace('_', ' ')} | " + " | ".join(str(report[v][k]) for v in report) + " |")
    md = f"# Benchmark results (llm={a.llm})\n\nSynthetic dataset generated by `benchmark/generate.py` — see README.\n\n" + "\n".join(lines) + "\n"
    (out / "latest.md").write_text(md, encoding="utf-8")
    print(md)


if __name__ == "__main__":
    main()
