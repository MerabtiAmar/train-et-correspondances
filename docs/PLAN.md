# Plan (condensed from docs/plan-complet.pdf, v3, 13 Nov 2026)

Page index of the PDF: p1 summary + critique table · p2-3 positioning, segments, business model · p4 target architecture + phase 0 · p5 phases 1-2 · p6 phases 3-5 · p7 phases 5-6 · p8 calendar + budget · p9 deferred costs + risks · p10 KPIs, first week, interview guide · p11 sources.

## Product
- Question answered: "Will I make my second train, and how much margin should I take?" Priority: trips with separate tickets or several operators (OUIGO + TER, Transdev TER, cross-border), where a missed connection costs money.
- Outputs: per-train probability of delay classes and cancellation; connection success probability; recommended margin.
- Moat (data is not one, ODbL forces sharing): calibrated and publicly verified probabilities, multi-operator connections, structured works/strike alerts via NLP, B2B API, SEO pages per train.
- Public B2C stays free (audience). Revenue target: B2B API (business travel, event organisers, travel insurers). Prices are hypotheses to test, not facts.
- Never: sell tickets, guarantee or compensate a connection (that is regulated insurance), use SNCF branding.

## Budget rule
0 € out of pocket at every stage. Any cost waits for a trigger and is paid by revenue already received. Free stack: Oracle Always Free (collector, later a second instance for API), Hugging Face public dataset (storage), GitHub Actions (nightly jobs only, never a 24/7 server), Cloudflare Pages (static site, commercial use allowed), Kaggle/laptop (compute). Backup: Azure for Students (non-commercial only), laptop.

## Phases (S1 = week of 16 Nov 2026, about 10-12 h/week)
- **P0 S1-S2 Start.** Check Oracle free limits (A1 = 2 OCPU / 12 GB total). Collector in a capped Docker container, no open port: Trip Updates + Alerts every 120 s, static GTFS daily if changed, reduce at source, daily Parquet to public HF dataset then delete local copy. 15 traveller + 5 pro interviews. Waiting-list page. Gate: ≥ 1 in 3 travellers lost money/time on a missed connection in 12 months.
- **P1 S2-S8 Data foundation.** One row per (train run, stop): planned time, observed delay, cancellation. Label = last estimate before passage. Enrich: weather, holidays, school zones, strikes, works (NLP on alert text). Coverage dashboard, schema tests. Cold start priors from monthly punctuality datasets. Gate: ≥ 90 % of planned TGV/IC runs labelled over 4 weeks.
- **P2 S6-S14 Modelling.** Multiclass target [0-5[, [5-15[, [15-30[, [30-60[, ≥ 60 min, cancelled. Horizons: booking (D-90..D-2), eve (D-1), same day (H-2..H-0). Baselines: B0 historical frequency per train shrunk to line then axis (beta prior), B1 monthly route punctuality. Model: LightGBM/CatBoost, hierarchical encodings, temperature or isotonic calibration, conformal intervals. Strict rolling-origin validation, no leakage. Connections: start with independence, backtest on all observed pairs (same station, 5-30 min gap). Gate S14: Brier ≥ 10 % better than B0 and ECE ≤ 3 points; else ship a descriptive connection product and keep working.
- **P3 S12-S18 Static MVP.** Page per train (D-1 forecast + history), connection tester computed in the browser from precomputed delay distributions, .ics calendar subscription per train instead of e-mail alerts. Nightly GitHub Actions build, Cloudflare Pages hosting, cookie-free analytics, transparency page (weekly calibration curve). Legal minimum: legal notice, privacy policy, ODbL attribution, "independent, not affiliated with SNCF".
- **P4 S18-S26 Beta + market test.** Free acquisition (SEO, monthly data study for press and user associations, commuter forums, LinkedIn). 15 B2B interviews, 3 free 2-month pilots: daily probability file + small FastAPI with keys and same-day forecast on a second Oracle Always Free instance behind Cloudflare, never on the family-app VM. Price tests. Gate S26: one letter of intent or converted pilot, or ≥ 3 % of active users click the paid offer; else keep it free as a showcase.
- **P5 S26-S34 Commercialisation.** Student-entrepreneur status (SNEE, free), micro-entreprise registered only when a client is ready to sign. Terms, B2B API contract, GDPR processor agreement if needed. Stripe (no fixed fee), prepaid B2B. Oracle free tier has no stated commercial-use ban: stay on it. First costs, only if covered by revenue: domain, professional liability insurance, paid server only if free limits are reached.
- **P6 S34-S44+ Scale.** E-mail alerts, API scaling, MLOps (weekly calibration drift, monthly retraining, shadow model), coverage: regional TER feeds, Transilien/RER via IDFM, Germany via the open Bahn-Vorhersage dataset.

## KPIs
Data: labelled share of runs. Model: Brier gain vs B0, ECE, connection backtest accuracy. Product: activation, 4-week return. Business: pilots, letters of intent, monthly recurring revenue.

## Main risks
SNCF feed change or outage (also archive SIRI Lite later, alert after 30 min without data). Model not better than baseline (S14 gate). Weak B2C monetisation (B2B first). Free tier changes (portable Docker, data on HF). Collector disturbing the family app (caps, no ports, separate instance for anything public). Owner's limited time (each phase ships something demonstrable).
