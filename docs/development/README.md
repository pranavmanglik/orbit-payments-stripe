# Stripe adapter development

Install the capability checkout before this adapter in the Orbit workspace:

```bash
python -m pip install -e ../orbit-workers -e ../orbit-payments
python -m pip install -e '.[dev]'
pytest
ruff check .
mypy
```

Unit tests must use deterministic fakes and synthetic signed payloads. Stripe test-mode checks are
separate integration gates and must never use live keys in automated CI.

The current local unit matrix passes on CPython 3.11, 3.12, 3.13, and 3.14. It validates SDK-facing
adapter behavior with an injected fake client; it does not verify Stripe network access, account
configuration, webhook delivery, or provider-side settlement.
