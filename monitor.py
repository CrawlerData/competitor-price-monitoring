#!/usr/bin/env python3
"""Competitor price monitoring on Google Shopping.

Reads your catalog (SKU, Google Shopping product_id, your price), fetches every seller for each
product, compares your price with the cheapest competitor and flags undercuts. Optional alerts by
Slack webhook or email.

Uses the CrawlerData API (https://crawlerdata.com). Set CRAWLERDATA_API_KEY first.

    python monitor.py
    python monitor.py --catalog catalog.csv --my-store "My Shop" --compare price --tolerance-pct 1
    python monitor.py --slack-webhook https://hooks.slack.com/services/...
"""
import argparse
import csv
import os
import smtplib
import sys
from datetime import datetime, timezone
from email.message import EmailMessage

import requests

from crawlerdata_client import CrawlerData, CrawlerDataError

REPORT_FIELDS = ["checked_at", "sku", "label", "product_id", "country", "my_price", "currency", "status",
                 "cheapest_competitor", "competitor_price", "gap", "gap_pct", "competitors_below",
                 "competitors", "job_id", "credits"]
UNDERCUT_FIELDS = ["checked_at", "sku", "label", "country", "my_price", "merchant", "offer_title", "price",
                   "shipping", "total_price", "currency", "availability", "below_by"]


def value(m):
    return m["value"] if m else None


def split_words(s):
    return [w.strip().lower() for w in (s or "").replace(",", ";").split(";") if w.strip()]


def competitor_offers(result, row, my_stores):
    include, exclude = split_words(row.get("include")), split_words(row.get("exclude"))
    offers = []
    for s in result.get("sellers") or []:
        title = (s.get("offer_title") or "").lower()
        merchant = s.get("merchant") or ""
        if any(store in merchant.lower() for store in my_stores):
            continue  # that's you
        if include and not all(w in title for w in include):
            continue
        if any(w in title for w in exclude):
            continue
        offers.append({
            "merchant": merchant, "offer_title": s.get("offer_title"),
            "price": value(s.get("price")), "shipping": value(s.get("shipping")),
            "total_price": value(s.get("total_price")), "currency": (s.get("price") or {}).get("currency"),
            "availability": s.get("availability"),
        })
    return offers


def analyse(row, offers, compare, tol_abs, tol_pct):
    my_price = float(row["my_price"])
    threshold = my_price - max(tol_abs, my_price * tol_pct / 100)
    priced = sorted((o for o in offers if o[compare] is not None), key=lambda o: o[compare])
    below = [o for o in priced if o[compare] < threshold]
    best = priced[0] if priced else None
    if not best:
        status = "no competitors"
    elif below:
        status = "UNDERCUT"
    elif best[compare] <= my_price:
        status = "matched"
    else:
        status = "cheapest"
    gap = round(my_price - best[compare], 2) if best else None
    return {
        "status": status,
        "cheapest_competitor": best["merchant"] if best else None,
        "competitor_price": best[compare] if best else None,
        "currency": best["currency"] if best else None,
        "gap": gap,
        "gap_pct": round(100 * gap / my_price, 1) if best else None,
        "competitors_below": len(below),
        "competitors": len(priced),
    }, below


def alert_text(undercuts):
    lines = [f"Google Shopping: {len(undercuts)} product(s) undercut by competitors"]
    for r in undercuts:
        lines.append(f"- {r['label']} ({r['sku']}, {r['country']}): you {r['my_price']} vs "
                     f"{r['cheapest_competitor']} {r['competitor_price']} {r['currency'] or ''} "
                     f"({r['competitors_below']} seller(s) below you)")
    return "\n".join(lines)


def send_slack(url, text):
    res = requests.post(url, json={"text": text}, timeout=30)
    res.raise_for_status()


def send_email(text):
    """SMTP settings come from env: SMTP_HOST, SMTP_PORT (587), SMTP_USER, SMTP_PASSWORD, ALERT_EMAIL_FROM, ALERT_EMAIL_TO."""
    msg = EmailMessage()
    msg["Subject"] = text.splitlines()[0]
    msg["From"] = os.environ.get("ALERT_EMAIL_FROM") or os.environ["SMTP_USER"]
    msg["To"] = os.environ["ALERT_EMAIL_TO"]
    msg.set_content(text)
    with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", "587")), timeout=30) as smtp:
        smtp.starttls()
        if os.environ.get("SMTP_USER"):
            smtp.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
        smtp.send_message(msg)


def write_csv(path, fields, rows, append):
    new = not (append and os.path.exists(path))
    with open(path, "a" if append else "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser(description="Compare your prices with Google Shopping competitors and flag undercuts.")
    ap.add_argument("-i", "--catalog", default="catalog.csv", help="catalog CSV (default: catalog.csv)")
    ap.add_argument("--my-store", default=os.environ.get("MY_STORE", ""),
                    help="comma-separated merchant names that are you, ignored as competitors")
    ap.add_argument("--compare", choices=["price", "total_price"], default="total_price",
                    help="compare item price or total incl. shipping (default: total_price)")
    ap.add_argument("--tolerance", type=float, default=0.0, help="ignore undercuts smaller than this amount")
    ap.add_argument("--tolerance-pct", type=float, default=0.0, help="ignore undercuts smaller than this percent")
    ap.add_argument("--report", default="price_report.csv", help="per-product report CSV (overwritten)")
    ap.add_argument("--undercuts", default="undercuts.csv", help="per-offer undercut log CSV (appended)")
    ap.add_argument("--slack-webhook", default=os.environ.get("SLACK_WEBHOOK_URL"),
                    help="Slack incoming webhook URL (or env SLACK_WEBHOOK_URL)")
    ap.add_argument("--email", action="store_true", help="email the alert (SMTP_* and ALERT_EMAIL_* env vars)")
    args = ap.parse_args()

    try:
        client = CrawlerData()
    except CrawlerDataError as exc:
        sys.exit(str(exc))

    with open(args.catalog, newline="", encoding="utf-8") as f:
        catalog = [r for r in csv.DictReader(f) if (r.get("product_id") or "").strip()]
    my_stores = split_words(args.my_store)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    report, undercut_offers = [], []

    for row in catalog:
        row["country"] = (row.get("country") or "us").strip()
        label = row.get("label") or row["product_id"]
        try:
            result = client.live("google-shopping-product", {"product_id": row["product_id"], "country": row["country"]})
        except CrawlerDataError as exc:
            if exc.status in (401, 402):
                sys.exit(str(exc))
            print(f"[skip] {label}: {exc}", file=sys.stderr)
            continue
        if result.get("error"):
            print(f"[skip] {label}: {result['error']}", file=sys.stderr)
            continue
        offers = competitor_offers(result, row, my_stores)
        summary, below = analyse(row, offers, args.compare, args.tolerance, args.tolerance_pct)
        rec = {"checked_at": now, "sku": row.get("sku"), "label": label, "product_id": row["product_id"],
               "country": row["country"], "my_price": float(row["my_price"]), "job_id": client.last_job["id"],
               "credits": client.last_job["credits"], **summary}
        report.append(rec)
        for o in below:
            undercut_offers.append({"checked_at": now, "sku": row.get("sku"), "label": label,
                                    "country": row["country"], "my_price": rec["my_price"], **o,
                                    "below_by": round(rec["my_price"] - o[args.compare], 2)})
        print(f"{rec['status']:<15} {label} ({row['country']}): you {rec['my_price']}, cheapest competitor "
              f"{rec['cheapest_competitor']} {rec['competitor_price']} ({rec['competitors']} competitors, "
              f"{rec['competitors_below']} below you)")

    write_csv(args.report, REPORT_FIELDS, report, append=False)
    write_csv(args.undercuts, UNDERCUT_FIELDS, undercut_offers, append=True)
    print(f"\nReport: {args.report}  Undercut offers: {len(undercut_offers)} appended to {args.undercuts}")
    print(f"Credits used: {client.credits_used}")

    undercut = [r for r in report if r["status"] == "UNDERCUT"]
    if undercut:
        text = alert_text(undercut)
        print()
        print(text)
        if args.slack_webhook:
            try:
                send_slack(args.slack_webhook, text)
                print("Slack alert sent")
            except requests.RequestException as exc:
                print(f"Slack alert failed: {exc}", file=sys.stderr)
        if args.email:
            try:
                send_email(text)
                print("Email alert sent")
            except (OSError, KeyError, smtplib.SMTPException) as exc:
                print(f"Email alert failed: {exc!r}", file=sys.stderr)


if __name__ == "__main__":
    main()
