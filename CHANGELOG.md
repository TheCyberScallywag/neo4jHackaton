# Changelog

## 0.2.0 (unreleased): Data Volunteer Network hackathon entry

- ADDED: multi-organisation volunteer network: identity, organisation opt-in and switching,
  skills/cause profile with offers and exclusions, project suggestions with scoped review and
  publication, explainable Relate-backed matching and coverage gaps.
- ADDED: SQLite transactional store with an outbox projection to an embedded Relate store
  (`relate-db==1.0.1`), bundled read-only `data/demo.rl`, operator CLI and Streamlit console.
- ADDED: Ionic mobile companion on a bearer-token JSON API.
- ADDED: optional AuraDB push and match-parity check.
- REFACTOR: replaced the template's FastUI UI, JWT API, Neo4j server and docs pipeline.

## 0.1.0

- Template baseline (sgaffald), recorded as git tree `ecfee3ce58cd966c4ccaf375e5811969da9aece7`.
