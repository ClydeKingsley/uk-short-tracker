# Chart contract

- **Question:** How has a selected company's publicly disclosed UK net short position changed over time, and what did its market price do over the same dates?
- **Primary comparison:** disclosed short-position percentage over time; price is contextual rather than a second-axis causal claim.
- **Upper chart:** step-line trend, percent of issued share capital. Legacy and ANSP are separate traces.
- **Lower chart:** daily market-price line in the provider-reported currency, with the latest available observation highlighted.
- **Shared interaction:** one time domain, linked crosshair, linked drag/wheel zoom, and 1M / 3M / 6M / YTD / 1Y / 3Y / 5Y / MAX presets.
- **Regime marker:** 13 July 2026, labelled as a disclosure-methodology change.
- **Palette:** hard two-root cap. Blue solid line for FCA ANSP, amber dashed/stepped line for legacy disclosed aggregate, neutral charcoal for price and guides.
- **Non-colour distinction:** solid versus dashed line, separate labels, and the regime annotation.
- **Default scale:** short-position y-axis includes zero because absolute magnitude matters; price y-axis uses an honest auto range labelled with currency.
- **Data sufficiency:** a line is rendered when at least two observations exist; sparse cases show exact event points and a visible coverage warning instead of implying a smooth trend.
- **Final QA surface:** the local application in installed Chrome/Edge at laptop and narrow/mobile widths.
