## Summary

Describe the user-visible or technical change and why it is needed.

## Verification

- [ ] `python -m unittest discover -s tests -v`
- [ ] `python -m tools.audit_public_tree .`
- [ ] External network access is mocked in tests.
- [ ] Launcher, dependency, path, or packaging changes passed the extracted Windows bundle smoke test.

## Data, language, and safety

- [ ] No real `data/`, database, FCA/Yahoo response, cache, mapping, log, credential, or personal path is included.
- [ ] Chinese and English UI text remain synchronized.
- [ ] Metric changes update methodology, data dictionary, and chart contract as applicable.
- [ ] New dependencies have a stated purpose and reviewed licence.
- [ ] This does not add broker login, financial credentials, order execution, or autonomous trading.
