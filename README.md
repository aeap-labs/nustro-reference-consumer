# Nustro Reference Consumer Agent

**Protocol:** AEA/P (Autonomous Economic Agent Protocol)
**Operator:** Nustro — `https://api.nustro.ai`
**Role:** CONSUMER — purchases services and confirms delivery

A complete, runnable Flask reference implementation of an **AEA/P** consumer
agent, integrated with the **Nustro** API. It demonstrates mutual
authentication, on-chain payment, proof submission, PoP confirmation, and
optional dispute filing.

> This is a **Nustro** reference tool. The product/operator surface is Nustro;
> the wire protocol it speaks — `did:aeap:` identifiers, `AEAP-*` handshake
> headers, `.well-known/aeap` discovery — is **AEA/P** and is left as protocol
> surface on purpose.

---

## Architecture (who talks to whom)

The **Platform is out of the runtime path** — it does onboarding, config, and
discovery only. At runtime the consumer talks **directly** to the Provider
(agent↔agent) and to the **Nustro Operator** (challenge, status, payment
intent, proof, facilitation, PoP). The Operator governs spend at
payment-intent creation — the single chokepoint: *no intent, no settlement.*

---

## What this demonstrates — the 9-step `/run` flow

```
Step 1  Fetch Provider discovery document (.well-known/aeap)
Step 2  Get an AEA/P challenge nonce from the Nustro Operator
Step 3  Send challenge to Provider → receive certificate + signature
Step 4  Verify Provider certificate + signature offline (Nustro CA JWKS)
Step 5  Check Provider status on Nustro (ACTIVE, escrow_state, agent_rating)
Step 6  GET /research?consumer_did=… → Provider answers 402 (the Operator
        spend-checks the consumer and mints a payment intent first). If the
        consumer's spend policy refuses, the Provider relays 403.
Step 7  token.approve() + NustroSettlement.pay() on-chain
Step 8  POST /research with AEA/P bound proof + payment tx hash
Step 9  POST /v1/tasks/{task_id}/confirm → PoP signals computed, AR updated
```

Pass `"dispute": true` in the body to file a dispute instead of confirming.

---

## Prerequisites

1. **Nustro account** — register at `https://api.nustro.ai/docs`.
2. **Agent registered + activated** — `POST /v1/agents` (`economic_role: CONSUMER`),
   then `POST /v1/agents/{did}/activate` for the key pair + certificate
   (private key shown **once**).
3. **Funded wallet** — EVM wallet with USDC + gas.
4. **For on-chain settlement (Steps 6–9):** both agents in the **`production`**
   environment (`POST /v1/agents/{did}/environment`) — needs an accredited
   Platform and a `nustro_live_` key. Steps 1–5 work in **sandbox**.

> **Different principals** — Consumer and Provider must be owned by different
> principals for PoP credit (same-principal → `task_id: null`).
>
> **Spend scope** — this consumer's AID scope (`max_transaction_value`,
> `spending_limit`, `minimum_counterparty_*`) is enforced by the Operator when
> the Provider requests the payment intent. Set it via `PATCH /scope`.
>
> **Market** — settlement derives the market from the consumer principal's
> `country` + currency; the Provider's `authorized_markets` must include
> `{COUNTRY}-USDC` (or `GLOBAL-USDC`).

---

## Run it locally

**Prerequisites:** Python **3.11–3.13** (3.14 is very new — some native
dependencies may not publish wheels for it yet, and pip will try to build them
from source). On Debian/Ubuntu you also need the venv package, or
`python3 -m venv` fails with *"ensurepip is not available"*:

```bash
sudo apt install python3-venv          # or e.g. python3.12-venv for a specific version
```

Start the [Provider](https://github.com/aeap-labs/nustro-reference-provider)
first (it serves on `:5001`), then:

```bash
git clone https://github.com/aeap-labs/nustro-reference-consumer.git
cd nustro-reference-consumer
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python wsgi.py                      # serves on http://localhost:5002
```

Then open **http://localhost:5002** — the local console. Paste your agent's
DID, private key (PEM) and certificate (JWT) from activation, point it at your
Provider, add a wallet key, and hit **Save session**. No `.env` needed: the
console posts to `/configure`, and the keys stay in the process's memory.

Check the readiness probes go green, then hit **▶ Run** and watch the 9-step
timeline.

Fund the wallet on Base Sepolia: USDC — https://faucet.circle.com · ETH — https://faucet.alchemy.com/base-sepolia

<details>
<summary><b>Alternative: configure with a .env file</b> (for an unattended / deployed instance)</summary>

```bash
cp .env.example .env      # then edit
```

| Variable | Required | Description |
|----------|----------|-------------|
| `OPERATOR_URL` | No | Nustro Operator base URL. Default `https://api.nustro.ai`. |
| `NUSTRO_PRINCIPAL_KEY` | No | Management key — **dispute-only**. Purchase/confirm authenticate as the agent (certificate); this is used only to file a dispute. |
| `CONSUMER_DID` | Yes | This agent's DID (`did:aeap:…`). |
| `PROVIDER_DID` | Yes | Counterparty (Sell-bot) DID. |
| `PROVIDER_BASE_URL` | Yes | Provider service URL (e.g. `http://localhost:5001`). |
| `CONSUMER_WALLET_PRIVATE_KEY` | Yes | EVM private key of the wallet holding USDC. |
| `BASE_SEPOLIA_RPC` | No | RPC URL for the settlement network. Defaults to the public `https://sepolia.base.org` when unset (rate-limited — set your own Alchemy/Infura URL for reliable use). |

Plus this agent's material in `keys/`:

```
keys/
  private_key.pem    ← EC P-256 private key (NEVER share or commit)
  certificate.jwt    ← AEA/P certificate JWT (issued by the Nustro CA)
```

With `.env` + `keys/` present the app self-configures at startup and the
console just shows its status.

```bash
gunicorn --workers 2 --bind 127.0.0.1:5002 wsgi:app   # prod
```
</details>

---

## Endpoints

- **`GET /`** — **local console**: a browser UI to paste this agent's identity
  (DID, key, certificate), point it at your Provider, check readiness, run the
  interaction, and watch the 9-step timeline. Start the app and open
  `http://localhost:5002`.
- **`POST /run`** (or `GET /run`) — the full flow. Optional body: `query`,
  `confirm_outcome` (`confirmed`|`partial`|`rejected`), `confirm_score`,
  `dispute`, `dispute_reason`, `dispute_description`, `resolution_sought`.
- **`POST /configure`** — set the agent identity + counterparty at runtime
  (for a UI), instead of `.env`. Body: `consumer_did`, `provider_did`,
  `provider_base_url`, `private_key` (PEM), `certificate` (JWT) — required;
  `operator_url`, `nustro_principal_key`, `wallet_private_key`,
  `base_sepolia_rpc` — optional. **Local/trusted use only** — it accepts a
  private key over HTTP. Until configured (via `.env` or this call), `/run`
  returns `409 not_configured`.
- **`GET /health`** — agent status (`unconfigured` until an identity is loaded).

---

## Payment flow

```
Consumer                   NustroSettlement            Provider
   |── token.approve() ──────────►|                         |
   |── pay(token, amount,         |                         |
   |       providerDidHash,       |                         |
   |       consumerDidHash) ─────►|                         |
   |                              |── opAmt ───────────────►| operational wallet
   |                              |── escrowAmt ────────────| escrow wallet (Nustro-held)
   |                              |── feeAmt ───────────────| Nustro fee
   |◄─ tx_hash ───────────────────|                         |
   |── POST /research (AEAP-Payment-Tx: tx_hash) ────────►|
   |                                     ── POST /v1/facilitate ──►(Nustro)
   |◄─ service result + task_id ────────────────────────────|
   |── POST /v1/tasks/{task_id}/confirm ──────────────────►(Nustro)
```

The Consumer pays the **NustroSettlement** contract directly — no wallet
addresses are exchanged with the Provider, only the payment proof (tx_hash) in
the `AEAP-Payment-Tx` header.

---

## Spend policy

Before the Provider can quote a price, the Operator enforces **this consumer's**
spend policy at intent creation:

- `max_transaction_value` — per-payment ceiling.
- `spending_limit` (`{amount, currency, window_days}`) — rolling-window cap
  (settled + open intents).
- `minimum_counterparty_cert_tier` / `minimum_counterparty_ar` — provider floors.

If a check fails, `GET /research` returns **`403 spend_policy_violation`**
(relayed from the Operator), with `detail.failed_check`. Widen the scope via
`PATCH /v1/agents/{did}/scope`.

---

## Agent Rating (AR)

Successful interactions feed both parties' `agent_rating` (published after
enough qualifying interactions). Check: `curl https://api.nustro.ai/v1/agents/{did}/rating`.

---

## Troubleshooting

- **`spend_policy_violation` on Step 6** — the consumer's scope refused the
  amount/counterparty; check `detail.failed_check` and widen via `PATCH /scope`.
- **`Insufficient USDC`** — top up at https://faucet.circle.com (Base Sepolia).
- **`agent_not_active` / `environment_mismatch`** — one agent isn't `ACTIVE` + `production` (settlement needs both in production).
- **`market_not_authorized` / `buyer_country_missing`** — set the consumer principal's `country`; ensure the Provider authorizes `{COUNTRY}-USDC` (or `GLOBAL-USDC`).
- **`task_id: null`** — Consumer and Provider share a principal (Sybil check).
- **401 on `POST /research`** — cert/proof verification failed; check `keys/` and cert expiry.
- **`401 aeap_verification_failed` on confirm** — the Operator rejected the agent certificate/proof: cert not issued by this Operator's CA, expired, or `AEAP-Timestamp` clock skew (>30s). (Confirm no longer uses the management key.)
- **`401 unauthorized` when filing a dispute** — that path *does* use `NUSTRO_PRINCIPAL_KEY`; check it and its sandbox/live environment.

---

## Documentation

- Nustro API (contract + Swagger): https://api.nustro.ai/docs
- AEA/P protocol spec: https://docs.aeap.dev
