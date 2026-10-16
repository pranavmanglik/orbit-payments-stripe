# Stripe adapter security

- Secrets are required, represented as `SecretStr`, and never loaded from implicit defaults.
- Never expose the secret key or webhook secret to client code. Never log them or the Stripe SDK's
  raw error text.
- Verify webhook signatures against the exact raw request body and a positive timestamp tolerance;
  zero disables Stripe SDK timestamp-age validation and is rejected by `StripeConfig`.
- Treat PaymentIntent client secrets as sensitive, user-scoped values. Send them only to the
  authenticated customer who owns that payment flow.
- Use Stripe-hosted/tokenized payment collection. The adapter never accepts cardholder data.
- A successful create call is not settlement or fulfillment. Authorize fulfillment only after
  verified provider state and application business checks.

This integration does not establish PCI compliance. See the repository [security policy](../../SECURITY.md)
to report a vulnerability.
