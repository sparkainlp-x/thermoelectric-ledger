# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project uses [Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-10-06

First public version. Derived from the reviewed draft `thermoelectric_ledger-2.py`
with every correction from the final review applied.

### Added
- Per-sample hot-side heat-flow lower bounds on the report
  (`hot_side_heat.sample_lower_bounds_W`, per-sample `lower_uncertainty_bound_W`
  and `lower_bound_strictly_positive`, `all_sample_lower_bounds_strictly_positive`).
- Documented strict `> 0` rule for lower bounds that rejects zero and `-0.0`
  (`_strictly_positive`, `hot_side_heat.lower_bound_rule`).
- `_exergy_unavailable_reason()` and `carnot_exergy_ceiling.reason`, sharing one
  priority order (`reason_priority`) with `comparison_status`, the exergy segment
  status and the efficiency status. Heat-flow reasons win over gradient reasons.
- `carnot_exergy_ceiling.accounting_check`, always computed once a ceiling exists,
  including for `synthetic_demo` records.
- `thermal_efficiency.uncertainty_bound_type`, `interval_lower`, `interval_upper`.
- `hot_side_heat.uncertainty_bound_W` / `uncertainty_scope` documenting that the
  heat-flow bound is one scalar applied to every sample.
- `--version` CLI flag; `ledger_version` in the report.
- Contract test suite (pytest), synthetic example record, CI on Python 3.10–3.12.

### Changed
- `analyze()` no longer writes `signed_load_power_W` into validated samples; it
  works on a deep copy. `validate_document()` deep-copies its input, so neither
  the caller's document nor the report alias each other.
- `_integrate_signed` is typed `tuple[float, float, list[dict[str, Any]]]` and
  rejects a mismatched uncertainty count, negative/non-finite uncertainties,
  fewer than two samples, and any segment with `dt <= 0`.
- `_carnot_and_bound` rejects `T <= 0 K`, `T_hot <= T_cold`, and a hot-side
  lower temperature bound at or below 0 K before evaluating `1 - T_cold/T_hot`.
- Gradient reasons now use the same fine-grained codes whether or not heat flow
  was supplied; when heat flow is absent the reason is `no_measured_heat_input`.
- Synthetic records that pass every gate now carry the accounting result in
  `comparison_status` instead of `illustrative_only_not_measurement`; the
  efficiency figure is still withheld (`not_reported_synthetic_data`).
- Efficiency is withheld when the accounting check is `impossible_energy_accounting`.
- Runtime `assert` statements replaced with explicit errors.

### Removed
- Redundant `_nonnegative(...) < 0` check on the OES residual (`_nonnegative`
  already raises).

### Unchanged invariants
- Signed readings are never clipped; `RESIDUAL_ENERGY_J = 0.0`; read-only, no
  device I/O, no hardware control.
