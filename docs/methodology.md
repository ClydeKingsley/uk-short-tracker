# UK Short Tracker methodology

## Measurement object

This application charts **publicly disclosed net short positions** in shares covered by the UK short-selling regime. It does not estimate total market short interest, securities-lending utilisation, or transaction-level short-sale volume.

The application is informational and read-only. It has no broker connection and cannot place orders.

## Authoritative short-position sources

- FCA notification and disclosure hub: <https://www.fca.org.uk/markets/short-selling/notification-disclosure-net-short-positions>
- Legacy named disclosures: <https://www.fca.org.uk/publication/data/short-positions-daily-update.xlsx>
- Current ANSP CSV: <https://www.fca.org.uk/publication/documents/aggregated-current-net-short-positions.csv>
- Historic ANSP CSV: <https://www.fca.org.uk/publication/documents/aggregated-historic-net-short-positions.csv>
- Combined ANSP XLSX: <https://www.fca.org.uk/publication/documents/aggregated-net-short-positions.xlsx>
- Reportable Shares List: <https://www.fca.org.uk/publication/documents/uk-reportable-shares-list.csv>

Every downloaded source is retained as a dated raw snapshot with its URL, fetch time, HTTP metadata where available, byte size, and SHA-256. Imports are idempotent. A changed hash is treated as a new source revision rather than silently overwriting the audit trail.

## Regime A: named public disclosures through 10 July 2026

The legacy workbook contains disclosure events by position holder, issuer, ISIN, percentage, and position date. It is not a daily snapshot table.

For each issuer and position date, the tracker applies all holder events for that date and reconstructs the publicly visible aggregate as follows:

1. A reported holder position of at least 0.50% becomes that holder's active disclosed contribution.
2. A reported value below 0.50%, including a zero or closing notification, removes that holder from the active disclosed aggregate from that event date.
3. The issuer-level value is the sum of active disclosed holder contributions after all same-day events are applied.
4. An end-of-day point is stored after every issuer event date, even when
   offsetting holder changes leave the issuer total unchanged. This preserves
   the event-day audit trail without inventing observations on non-event days.

This series is labelled **Legacy disclosed aggregate (holders at or above 0.50%)**. It cannot recover positions below the former public threshold.

## Regime B: anonymised ANSP from 13 July 2026

From 13 July 2026, the FCA stopped publishing new holder identities and began publishing an issuer-level Aggregated Net Short Position (ANSP). The ANSP is built from notified positions at or above the 0.20% base reporting threshold. It is published on a working-day T+2 basis and may be revised for late, corrected, or verified notifications.

The tracker stores the FCA-provided ANSP as its own series and does not attempt to infer the anonymous constituents. Pre-commencement position dates carried into the first ANSP are not treated as a pre-2026 aggregate history. The displayed ANSP coverage is clamped to the transition observation used by the FCA and visibly marked at the regime boundary.

## Current short-position ranking

The current ranking is a view of the **currently activated `ansp_current` snapshot only**. It never blends legacy named disclosures, historic ANSP rows, an unactivated import, or market-price data into the ranking value.

Each ranked row retains the reportable-share/ISIN grain of the FCA current ANSP source. A shared internal issuer identifier is only a navigation aid: rows for different ISINs or share classes must not be summed, maximised, or deduplicated into an invented issuer-level ranking. Clicking a row opens that security's detail view, where the regime-separated short-position history and the price history are presented as two charts.

The canonical global order is aggregate net short position percentage descending. Equal percentages are resolved deterministically by company name (case-insensitive, then exact source text), ISIN, and FCA source row number. The 1-based `rank` records that global raw order and is not renumbered by search, pagination, Top-N display selection, or an alternative UI sort.

[`SSR 6.3.1G`](https://handbook.fca.org.uk/handbook/ssr6/ssr6s3) defines `Position date` as the most recent position date in a notification included in the calculation of the relevant aggregate net short position. It also makes clear that a subsequently submitted notification can contain a less recent position date. Consequently, a legitimately current ANSP snapshot may contain an old `position_date`; that alone is not evidence of a failed download or a stale FCA file. Snapshot retrieval, check, import, and activation timestamps are separate provenance fields and are the correct evidence for download freshness.

The ranking is not a complete-market short-interest league table. ANSP aggregates only net short positions that meet the FCA reporting requirements and are included in its calculation, normally individual positions at or above the 0.20% base reporting threshold. Sub-threshold, exempt, and otherwise undisclosed positions are absent; omission from the ranking does not mean zero short interest.

## Why the two lines must not be treated as one homogeneous metric

The former public series omits each holder below 0.50%; the ANSP series includes notified holders from 0.20%. A level change around July 2026 can therefore be caused by the disclosure methodology, not by new short selling. The chart uses different colour and line style, a vertical regime marker, separate legend labels, and a visible methodology note.

## Time-series interpretation

Both FCA datasets are threshold-event data. Between two disclosure events, the latest disclosed value is shown as a horizontal step. That is a display of the latest public information, not evidence that the true position was unchanged every day.

The new ANSP `Position date` is the latest position date among notifications included in that aggregate. It is not necessarily a uniform as-of date for every anonymous constituent.

## Price data

Price data is optional enrichment and is kept separate from FCA data. The default no-key provider is a best-effort Yahoo Finance adapter with a manual-symbol override and a replaceable provider interface. Prices can be delayed, adjusted, incomplete, rate-limited, or unavailable. The UI always shows provider, currency, and last observation time and never substitutes price data for FCA disclosure data.
