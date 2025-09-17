# Roadmap

A pragmatic, milestone-based plan to evolve the app from position monitoring to robust multi-wallet yield tracking.

---

## v0.1 — MVP (Current)
- [x] Multi-wallet input (one address per line)
- [x] Per-wallet **detail** and **consolidated** views
- [x] Strict per-wallet positions via `marketPositions` (no pool totals)
- [x] **Supply = Collateral** (display convention), **Borrow-only** toggle
- [x] Current **Borrow Rate** per market (robust GraphQL + fallback)
- [x] Optional **USD recompute** via DefiLlama + **decimals** normalization
- [x] Chain filter (Ethereum, Base, Arbitrum)
- [x] Whitelist filter + basic debug panel

---

## v0.2 — Correctness & Resilience
- [ ] Harden GraphQL schema handling (introspection cache, graceful fallbacks)
- [ ] Better outlier detection (USD sanity thresholds, per-market overrides)
- [ ] Clear error surfaces (per-wallet + per-market debug)
- [ ] Deterministic rounding & formatting across tables and KPIs

**Acceptance criteria**
- Zero crashes on partial API failures; affected rows show “N/A” with a tooltip.

---

## v0.3 — UX & Observability
- [ ] Sticky sidebar state; URL-based state (shareable views)
- [ ] Column chooser & sorting presets (e.g., by **LTV** desc)
- [ ] Tooltips for key metrics (Supply=Collateral, LTV formula, rate source)
- [ ] Compact/expanded table density

**Acceptance criteria**
- Users can save/share a link that restores filters, chains, wallets.

---

## v0.4 — Yield Tracking Foundations
- [ ] Persist daily snapshots of: `borrowUsd`, `supplyUsd`, `borrowRate`
- [ ] Time-series charts per wallet & consolidated
- [ ] Rolling metrics (7/30-day average rates, utilization)

**Acceptance criteria**
- Line charts render for any wallet with ≥ 2 snapshots.

---

## v0.5 — Fees & PnL
- [ ] **Fees paid** (historic interest) per wallet & per market  
      – integrate tx history (preferred: Zapper) and tag **repay** events  
      – compute interest component vs principal
- [ ] PnL primitives (Realized interest, Net carry = Supply yield − Borrow cost)
- [ ] CSV exports (detail & consolidated, with timestamps)

**Acceptance criteria**
- For a known wallet, fees paid ≈ sum of interest across repay events within tolerance.

---

## v0.6 — Alerts & Notifications
- [ ] Threshold alerts: **LTV**, **rate changes**, **USD exposure** deltas
- [ ] Digest vs realtime modes (per wallet, per market)
- [ ] Webhooks/notifications (email, Slack/Discord/Telegram)

**Acceptance criteria**
- Users can set an LTV threshold and receive an alert with marketKey context.

---

## v0.7 — Integrations & Data Quality
- [ ] Optional Dune/Flipside adapters (for cross-checks & custom queries)
- [ ] Price oracle fallbacks (CoinGecko; on-chain oracle for blue-chips)
- [ ] Symbol/address normalization registry (edge markets, PT/YT, LP)

**Acceptance criteria**
- When DefiLlama misses a price, app auto-falls back and logs the source.

---

## v0.8 — Coverage Expansion
- [ ] Additional chains (Optimism, Polygon, etc.) where applicable
- [ ] Additional protocols (e.g., Pendle PT/YT positions) behind toggles
- [ ] Protocol-specific enrichments (implied APY, maturity, discount)

**Acceptance criteria**
- New protocol data appears side-by-side with Morpho in a unified schema.

---

## v0.9 — Performance & Infra
- [ ] Request coalescing & caching (per marketKey, per day)
- [ ] Batched queries; adaptive rate-limit backoff
- [ ] Optional server mode (Docker) + environment config templates

**Acceptance criteria**
- Consolidated view for 25+ wallets loads within target SLA on cold start.

---

## v1.0 — Production Hardening
- [ ] Tests (unit + integration), schema contracts, snapshot tests
- [ ] SLOs & monitoring (health checks, error budgets)
- [ ] Docs: quickstart, config, data sources, limitations
- [ ] Security review (secrets handling, minimal scopes)

**Acceptance criteria**
- Green test suite, documented runbook, and reproducible Docker image.

---

## Stretch Goals
- [ ] Wallet tagging & grouping (teams, strategies)
- [ ] Role-based views (read-only links for stakeholders)
- [ ] Scenario tools (what-if: rate shocks, collateral haircuts)
- [ ] Portfolio “risk score” & VaR-style heuristics

---

## Non-Goals (for now)
- On-chain execution (rebalancing, liquidation bots)  
- Derivative pricing beyond basic PT/YT implied rates

---

## Notes
- This app is **informational**; verify on-chain before acting.
- Data heterogeneity across protocols is expected; we normalize progressively.
