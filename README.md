# thermoelectric-ledger

**Signed heat-flow accounting with withheld exergy and efficiency.**

[![CI](https://github.com/sparkainlp-x/thermoelectric-ledger/actions/workflows/ci.yml/badge.svg)](https://github.com/sparkainlp-x/thermoelectric-ledger/actions/workflows/ci.yml)
![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)
![Python 3.10–3.12](https://img.shields.io/badge/python-3.10%E2%80%933.12-blue.svg)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.23241554.svg)](https://doi.org/10.5281/zenodo.23241554)

Research software from Spark AI NLP (Fredericton, New Brunswick, Canada).
`thermoelectric_ledger.py` is a single-file, standard-library-only, **read-only**
ledger. It takes an explicit JSON record of time-stamped thermoelectric readings
and writes an audit report to stdout. It performs no device I/O, controls no
hardware, and never modifies its input.

> **Evidence label.** The only data shipped in this repository
> (`examples/synthetic_demo_run.json`) is **SYNTHETIC**: hand-typed numbers that
> exercise the code. It is not a measurement. Reports built from it carry the
> flag `SYNTHETIC_DEMO_NOT_MEASUREMENT` and never receive an efficiency figure.

## What it does

The ledger keeps three layers separate:

1. **Numeric computation.** Signed trapezoidal integrals of electrical power
   (V·I) and hot-side heat flow over the actual timestamps, each with a
   conservative absolute uncertainty bound.
2. **Physical admissibility.** Every per-sample uncertainty-adjusted hot-side
   heat-flow lower bound must be strictly positive, and the forward temperature
   gradient must be resolved by the calibration bounds, before a Carnot/exergy
   ceiling is computed.
3. **Claim eligibility.** A thermal efficiency is reported only for
   `data_kind: "measured"` records whose net heat input stays positive after
   its own uncertainty bound.

Concretely:

- **Signed readings stay signed.** Negative heat flow (heat leaving the hot
  face) and negative electrical power (power absorbed from the load) are kept
  and integrated as-is. Nothing is clipped.
- **Signed heat is always integrated for audit**, even when every downstream
  claim is withheld. A negative heat integral is reported; `ceiling_J` stays
  `null`.
- **Every lower bound is exposed.** `hot_side_heat.sample_lower_bounds_W` lists
  `Q̇_i − u(Q̇)` for every sample; `hot_side_heat.samples[i]` repeats it with a
  per-sample `lower_bound_strictly_positive` verdict, and
  `all_sample_lower_bounds_strictly_positive` summarises the run. One failing
  sample rejects the whole run.
- **Strict > 0 rule.** A lower bound is admissible only if it is finite and
  strictly greater than zero. Zero **and negative zero** (`-0.0`, which IEEE 754
  arithmetic can produce) are rejected, as is any negative value.
- **Carnot/exergy ceiling** `Σ (1 − T_cold/T_hot)·Q̇_hot dt` is computed only
  when every lower bound is strictly positive **and** every sample satisfies
  `T_hot − u(T_hot) > T_cold + u(T_cold)`. The Carnot factor itself rejects
  `T ≤ 0 K` and `T_hot ≤ T_cold`.
- **Accounting check is always computed once a ceiling exists**, including for
  synthetic data, so an impossible accounting (electrical lower bound above the
  exergy upper bound) is always flagged, never hidden behind a "synthetic" label.
- **Inputs are never mutated.** The input document is deep-copied; validated
  samples are never written to.
- **OES residual sidecar.** Optional per-sample OES residual fields are validated
  and logged. `RESIDUAL_ENERGY_J = 0.0` is an invariant: a residual is
  bookkeeping and contributes zero energy to any integral.

## What it refuses to claim

- It is **not** a measurement campaign, and this repository contains no
  measurements.
- It is **not** a thermoelectric generator, controller, or driver.
- It does **not** claim that any device delivers more energy than it absorbs.
  Its job is the opposite: to flag records whose accounting would imply that.
- It does **not** turn a synthetic record into evidence. `synthetic_demo`
  records never receive an efficiency figure.
- It does **not** report a ceiling or efficiency when a precondition is
  unresolved; it reports `null` with a structured reason instead.
- It is **not** a clinical or computing-hardware product, and makes no claims
  in those areas.
- Measurement validity always depends on the instruments, calibration, and
  protocol behind the input. The ledger cannot certify those.

## Quick start

```bash
python3 thermoelectric_ledger.py examples/synthetic_demo_run.json
python3 thermoelectric_ledger.py examples/synthetic_demo_run.json --require-measured   # exits 2: synthetic
python3 -m pip install pytest && python3 -m pytest
```

Python 3.10 or newer; no runtime dependencies. Exit code `0` on success, `2` on
any input, validation, or I/O error (message on stderr).

## Input contract (schema 1.0)

Top-level keys: `schema_version` (`"1.0"`), `data_kind` (`"measured"` or
`"synthetic_demo"`), `run_id`, `metadata`, `samples`, optional `description`.
Unknown keys are rejected. See `examples/synthetic_demo_run.json` for a complete
record.

- `metadata.units` must match exactly (s, K, V, A, W, dimensionless).
- Sign conventions must be declared: positive V·I is power delivered to the
  load; positive heat flow enters the module hot face.
- `metadata.heat_flow_status`: `measured` / `not_measured` for measured data;
  `illustrative` / `not_measured` for synthetic data.
- `metadata.calibration_uncertainty_bounds` holds conservative absolute bounds
  for `timestamp_s`, `voltage_V`, `current_A`, `hot_temperature_K`,
  `cold_temperature_K`, `hot_heat_flow_W`.
- `samples`: at least two, strictly increasing timestamps, absolute
  temperatures above 0 K (also after subtracting their bound).

### `bounds["hot_heat_flow_W"]` is one scalar

`calibration_uncertainty_bounds.hot_heat_flow_W` is a **single scalar** applied
to **every** sample (`hot_side_heat.uncertainty_scope =
"single_scalar_calibration_bound_applied_to_every_sample"`). Per-sample
heat-flow uncertainty is not part of schema 1.0; it would be a new schema
version, not a silent upgrade.

## Output: gates, reasons, and priority order

`carnot_exergy_ceiling.reason` is the first failing gate in this fixed order
(also emitted as `reason_priority`). Heat-flow reasons win over gradient
reasons, and only one reason is emitted:

| # | `reason` | meaning |
|---|----------|---------|
| 1 | `no_measured_heat_input` | heat flow not supplied |
| 2 | `hot_heat_flow_lower_bound_not_strictly_positive_at_every_sample` | some `Q̇_i − u(Q̇) ≤ 0` (incl. `-0.0`) |
| 3 | `equal_temperature_faces` | `T_hot = T_cold` at every sample |
| 4 | `reversed_temperature_gradient` | `T_hot < T_cold` at every sample |
| 5 | `mixed_or_crossing_temperature_gradient` | gradient not forward for the entire run |
| 6 | `temperature_uncertainty_overlaps_zero_gradient` | forward, but not resolved by the bounds |

`comparison_status` is derived from the same value (`"not_assessable_" + reason`),
so the two cannot drift. When all gates pass, `reason` is `null`, the ceiling is
computed, and `comparison_status` is the accounting result:

- `impossible_energy_accounting` — electrical lower bound exceeds exergy upper bound
- `point_estimate_exceeds_ceiling_within_uncertainty`
- `no_measured_exceedance_of_uncertainty_adjusted_ceiling`

The same values appear in `carnot_exergy_ceiling.accounting_check`.

### `thermal_efficiency`

Status values, in evaluation order: the gate-derived `not_reported_*` status;
`not_reported_synthetic_data`; `not_reported_positive_measured_heat_input_not_resolved`;
`not_reported_impossible_energy_accounting`;
`not_reported_positive_electrical_output_not_resolved`;
`reported_from_measured_heat_input`.

**The efficiency `uncertainty_bound` is a worst-case bound, not a k=2 expanded
uncertainty.** It is the half-width `max(η − η_low, η_high − η)` of the interval
`η_low = (E − u_E)/(Q + u_Q)`, `η_high = (E + u_E)/(Q − u_Q)` built from the
conservative absolute bounds, and is labelled
`uncertainty_bound_type = "worst_case_interval_half_width_not_k2_expanded_uncertainty"`.
The interval ends are reported as `interval_lower` / `interval_upper`. Do not
read it as a GUM coverage interval.

## Related work

- [residual-gradient-workbench](https://github.com/sparkainlp-x/residual-gradient-workbench)
  (DOI [10.5281/zenodo.23187280](https://doi.org/10.5281/zenodo.23187280)) — a
  separate browser-based synthetic Thevenin illustration. Related only; it is
  not this ledger, shares no code with it, and is not evidence for it.

## Citation

See [`CITATION.cff`](CITATION.cff). Archived on Zenodo: concept DOI
[10.5281/zenodo.23241554](https://doi.org/10.5281/zenodo.23241554) (all versions); v0.1.0:
[10.5281/zenodo.23241555](https://doi.org/10.5281/zenodo.23241555).

Jean-François Brisson, Spark AI NLP, Fredericton, New Brunswick, Canada —
ORCID [0009-0000-9778-5374](https://orcid.org/0009-0000-9778-5374).

## License

[MIT](LICENSE) © 2026 Jean-François Brisson / Spark AI NLP.
