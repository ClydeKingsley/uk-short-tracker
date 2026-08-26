# Chart contract

- **Question:** How has a selected company's publicly disclosed UK net short position changed over time, and what did its market price do over the same dates?
- **Primary comparison:** disclosed short-position percentage over time; price is contextual rather than a second-axis causal claim.
- **Upper chart:** step-after trend, percent of issued share capital. A value begins at its effective `date` and holds only until `interval_end` or the next linked state. Legacy and ANSP are separate traces.
- **Lower chart:** daily market-price line in the provider-reported currency, with the latest available observation highlighted.
- **Shared interaction:** one time domain, linked crosshair, linked drag/wheel zoom, and 1M / 3M / 6M / YTD / 1Y / 3Y / 5Y / MAX presets.
- **ANSP event axis:** for each ISIN, order historic states by `became_historical_date`. The first state starts at the later of its constituent `position_date` and its RSL scope start; every later historic state and current state starts when the previous state became historical. A regressed raw `position_date` never moves a later state backwards.
- **Initial scope:** FCA's first ANSP publication represented positions at midnight on 9 July 2026. Treat the initial RSL cohort (including `date_added = 13 July`) as in scope from 9 July; shares added after first publication enter on their actual RSL `date_added`.
- **Publication/regime marker:** 13 July 2026, labelled as the first ANSP publication and disclosure-methodology change. It remains a vertical annotation, not a replacement for the 9 July position/effective date.
- **Trace boundary:** never connect the legacy reconstruction to ANSP, including when both traces contain a 9 July observation. They are different measurements, not two segments of one metric.
- **Missing current:** end the final historic step at its `interval_end` and leave a visible gap. Missing current is unknown public state, never 0%.
- **Tooltip/audit:** show effective `date`, raw FCA `position_date`, `became_historical_date`/`interval_end`, `chart_date_basis`, and `first_published_on` without relabelling one as another.
- **Palette:** hard two-root cap. Blue solid line for FCA ANSP, amber dashed/stepped line for legacy disclosed aggregate, neutral charcoal for price and guides.
- **Non-colour distinction:** solid versus dashed line, separate labels, and the regime annotation.
- **Default scale:** short-position y-axis includes zero because absolute magnitude matters; price y-axis uses an honest auto range labelled with currency.
- **Data sufficiency:** a line is rendered when at least two observations exist; sparse cases show exact event points and a visible coverage warning instead of implying a smooth trend.
- **Final QA surface:** the local application in installed Chrome/Edge at laptop and narrow/mobile widths.
