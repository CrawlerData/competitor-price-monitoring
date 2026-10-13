# Competitor Price Monitoring with Google Shopping (Python)

Compare your prices with every competitor selling the same product on Google Shopping. Put your catalog in a CSV (SKU, Google Shopping product ID, your price), run `monitor.py`, and get a per-product report — cheapest competitor, their price, the gap in money and percent — plus a log of every offer that undercuts you. Optional alerts go to a Slack incoming webhook or by email. Python with one dependency (`requests`).

> **Uses the CrawlerData API — you need an API key** (free trial credits available at https://app.crawlerdata.com/register). Each product check costs 1 credit per 10 sellers returned (minimum 1).

## Features

- Catalog in, report out: `catalog.csv` → `price_report.csv` (one row per SKU) and `undercuts.csv` (one row per undercutting offer, appended each run so you keep a history)
- Status per SKU: `UNDERCUT`, `matched`, `cheapest` or `no competitors`
- Compare on total price including shipping (default) or item price only
- Tolerance in money (`--tolerance 0.50`) or percent (`--tolerance-pct 1`) so a 1-cent difference doesn't page you
- `--my-store` removes your own listing from the competitor list
- Per-product `include` / `exclude` keywords to drop accessories, other sizes and renewed units from the comparison
- Alerts: Slack incoming webhook (`--slack-webhook` or `SLACK_WEBHOOK_URL`) and/or email over SMTP (`--email`)
- Job ID saved with every row, so you can trace a number back to the API response

## Quickstart

```bash
git clone https://github.com/CrawlerData/competitor-price-monitoring.git
cd competitor-price-monitoring
pip install -r requirements.txt

export CRAWLERDATA_API_KEY="your_api_key"        # PowerShell: $env:CRAWLERDATA_API_KEY="your_api_key"

python monitor.py --my-store "Your Store Name"
python monitor.py --compare price --tolerance-pct 1 --slack-webhook "https://hooks.slack.com/services/XXX/YYY/ZZZ"
```

## Input: `catalog.csv`

```csv
sku,product_id,country,my_price,label,include,exclude
SONY-XM5-BLK,11726664398501807381,us,239.99,Sony WH-1000XM5,,renewed;refurbished;case
SAM-BUDS3FE,7580511071886337697,us,89.99,Samsung Galaxy Buds3 FE,,case;renewed
```

| Column | Description |
| --- | --- |
| `sku` | Your own SKU (free text) |
| `product_id` | Google Shopping product ID for the same product (see below) |
| `country` | 2-letter country code; prices come back in that country's currency, so set `my_price` in it too |
| `my_price` | Your price, compared with the competitor's total price (or item price with `--compare price`) |
| `label` | Name shown in reports and alerts |
| `include` / `exclude` | Optional `;`-separated words an offer title must / must not contain |

**Finding the `product_id`:** search Google Shopping by **brand + model** (for example with the [Google Shopping scraper](https://github.com/CrawlerData/google-shopping-scraper) or `python sellers.py --query` in the [sellers scraper](https://github.com/CrawlerData/google-shopping-sellers-scraper)) and copy the `product_id` of your product. Don't search by bare GTIN/UPC: Google Shopping often returns unrelated products for a barcode. One model can have several product IDs (colors, bundles, listings); add a row for each one that is really your product.

## Sample output

Real run on 13 October 2026 with the catalog above. The prices are made up for the demo; Sony is passed as `--my-store` to show how your own listing is excluded:

```text
$ python monitor.py --my-store "Sony" --slack-webhook "$SLACK_WEBHOOK_URL"
UNDERCUT        Sony WH-1000XM5 (us): you 239.99, cheapest competitor Best Buy 228 (9 competitors, 1 below you)
cheapest        Samsung Galaxy Buds3 FE (us): you 89.99, cheapest competitor Walmart - Truly Trending 94.99 (10 competitors, 0 below you)

Report: price_report.csv  Undercut offers: 1 appended to undercuts.csv
Credits used: 2

Google Shopping: 1 product(s) undercut by competitors
- Sony WH-1000XM5 (SONY-XM5-BLK, us): you 239.99 vs Best Buy 228 USD (1 seller(s) below you)
Slack alert sent
```

`price_report.csv` ([sample](examples/sample-price-report.csv)):

```csv
checked_at,sku,label,product_id,country,my_price,currency,status,cheapest_competitor,competitor_price,gap,gap_pct,competitors_below,competitors,job_id,credits
2026-10-13T05:14:51Z,SONY-XM5-BLK,Sony WH-1000XM5,11726664398501807381,us,239.99,USD,UNDERCUT,Best Buy,228,11.99,5.0,1,9,6acdbe4daaddb7eaed95a90b,1
2026-10-13T05:14:51Z,SAM-BUDS3FE,Samsung Galaxy Buds3 FE,7580511071886337697,us,89.99,USD,cheapest,Walmart - Truly Trending,94.99,-5.0,-5.6,0,10,6acdbe68aaddb7eaed95a919,1
```

`undercuts.csv` ([sample](examples/sample-undercuts.csv)):

```csv
checked_at,sku,label,country,my_price,merchant,offer_title,price,shipping,total_price,currency,availability,below_by
2026-10-13T05:14:51Z,SONY-XM5-BLK,Sony WH-1000XM5,us,239.99,Best Buy,Sony - WH-1000XM5 Wireless Noise Cancelling Over-the-Ear Headphones - Black,228,0,228,USD,In Stock,11.99
```

A positive `gap` means the cheapest competitor is below you by that amount; negative means you are the cheapest.

## Alerts

An alert is sent only when at least one SKU is `UNDERCUT`. The message is plain text, so it works in Slack and email:

**Slack:** create an [incoming webhook](https://api.slack.com/messaging/webhooks) and pass its URL with `--slack-webhook` or the `SLACK_WEBHOOK_URL` environment variable. The script posts `{"text": "..."}`. (We tested the payload against a local HTTP endpoint, not a real Slack workspace.)

**Email:** add `--email` and set these environment variables. The script uses Python's `smtplib` with STARTTLS:

```bash
export SMTP_HOST=smtp.example.com SMTP_PORT=587 SMTP_USER=alerts@example.com SMTP_PASSWORD=app-password
export ALERT_EMAIL_TO=pricing@example.com   # optional: ALERT_EMAIL_FROM
```

A failed alert is reported on stderr; the CSV files are written before alerts are sent.

## Cost per run

Each SKU is one Google Shopping sellers request: **1 credit per 10 sellers returned**, rounded up, minimum 1. Both products in the sample run had 10 sellers, so the run cost 2 credits.

Example: 200 SKUs checked once a day with 11-20 sellers each = 200 × 2 × 30 = 12,000 credits a month, or $12 at the smallest pack's $1.00 per 1,000 credits (down to $0.30 per 1,000 on larger packs, see [pricing](https://crawlerdata.com/pricing)). Failed jobs are not charged.

## Options

| Option | Default | Description |
| --- | --- | --- |
| `-i, --catalog FILE` | `catalog.csv` | Your catalog |
| `--my-store a,b` | env `MY_STORE` | Merchant names that are you (case-insensitive substring match) |
| `--compare` | `total_price` | `total_price` (with shipping) or `price` |
| `--tolerance N` | `0` | Ignore undercuts smaller than N (currency units) |
| `--tolerance-pct N` | `0` | Ignore undercuts smaller than N percent of your price |
| `--report FILE` | `price_report.csv` | Per-SKU report, overwritten each run |
| `--undercuts FILE` | `undercuts.csv` | Per-offer log, appended each run |
| `--slack-webhook URL` | env `SLACK_WEBHOOK_URL` | Slack incoming webhook |
| `--email` | off | Send the alert by email (SMTP env vars above) |

Run it on a schedule with cron or Windows Task Scheduler, the same way as the [price tracker](https://github.com/CrawlerData/google-shopping-price-tracker#running-it-on-a-schedule). For large catalogs, switch the client to the `fast` queue and poll (`client.submit()` / `client.wait()`) instead of holding one live connection per product.

## What this does not cover

- Google Shopping only: competitors' own sites, marketplaces outside Google Shopping and in-store prices are not checked.
- Prices are what Google Shopping showed at that moment, for that country. They can differ by region within a country and change at any time.
- Seller `link` is usually not returned, so offers are identified by merchant name and offer title.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| `HTTP 401` | The API key is missing or wrong. Check `echo $CRAWLERDATA_API_KEY` (or `$env:CRAWLERDATA_API_KEY` in PowerShell) and create a key under API Access in the [dashboard](https://app.crawlerdata.com). |
| `HTTP 402` | You are out of credits. Buy a credit pack in the dashboard (from $1.00 down to $0.30 per 1,000 credits, see [pricing](https://crawlerdata.com/pricing)). |
| `HTTP 429` | Rate limit: 100 requests per minute and 20 per second per key. The client waits until `X-RateLimit-Reset` and retries automatically; for big batches, spread requests out. |
| Timeouts | Live requests return when Google has been fetched: a few seconds for search, 30 to 120 seconds for Google Shopping sellers. The client uses a 200 second timeout. |
| `job ... failed` | The HTTP call worked but the job did not (for example Google blocked that attempt). Failed jobs cost 0 credits and the client retries them twice before giving up. |

## Links

- [CrawlerData](https://crawlerdata.com)
- [Competitor price monitoring with CrawlerData](https://crawlerdata.com/solutions/competitor-price-monitoring)
- [Google Shopping Sellers API](https://crawlerdata.com/apis/google-shopping-sellers-api)
- [API docs](https://app.crawlerdata.com/docs)

## More CrawlerData examples

| Repo | What it does |
| --- | --- |
| [google-shopping-scraper](https://github.com/CrawlerData/google-shopping-scraper) | Search Google Shopping, export products to CSV/JSON (Python + Node.js) |
| [google-shopping-sellers-scraper](https://github.com/CrawlerData/google-shopping-sellers-scraper) | Every seller and price for one product, as a comparison table |
| [google-shopping-price-tracker](https://github.com/CrawlerData/google-shopping-price-tracker) | Scheduled price history in SQLite, CSV export and an HTML chart |
| [competitor-price-monitoring](https://github.com/CrawlerData/competitor-price-monitoring) | Your catalog vs Google Shopping sellers, with undercut alerts |
| [map-violation-monitor](https://github.com/CrawlerData/map-violation-monitor) | Flag sellers below MAP with timestamped evidence |
| [google-search-scraper](https://github.com/CrawlerData/google-search-scraper) | Google SERP to JSON/CSV: organic, ads, People Also Ask, AI Overview (Python + Node.js) |
| [google-rank-tracker](https://github.com/CrawlerData/google-rank-tracker) | Daily keyword positions by country and device, with a change report |
| [google-local-business-leads](https://github.com/CrawlerData/google-local-business-leads) | Google Local results to a lead list CSV |
| [google-news-scraper](https://github.com/CrawlerData/google-news-scraper) | Brand and keyword news monitoring to CSV and JSON Feed |
| [google-search-mcp-server](https://github.com/CrawlerData/google-search-mcp-server) | Connect Claude, Cursor or n8n to the CrawlerData MCP server |
| [crawlerdata-examples](https://github.com/CrawlerData/crawlerdata-examples) | Python, Node.js and curl snippets for every endpoint |

## License

MIT. See [LICENSE](LICENSE). Google and Google Shopping are trademarks of Google LLC; this project is not affiliated with Google.
