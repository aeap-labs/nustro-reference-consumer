# AEAP Reference Consumer Agent

**Version:** 0.5.0  
**Protocol:** AEAP (Autonomous Economic Agent Protocol)  
**Role:** CONSUMER — purchases services and confirms delivery

This is a complete, runnable Flask application demonstrating how a Consumer
agent integrates with the AEAP Platform. It handles mutual authentication,
on-chain payment execution, proof submission, PoP task confirmation, and
optional dispute filing.

---

## What this demonstrates

The full AEAP interaction flow — 9 steps executed in a single `/run` call:

```
Step 1  Fetch Provider discovery document
Step 2  Get AEAP challenge nonce from platform
Step 3  Send challenge to Provider → receive certificate + signature
Step 4  Verify Provider certificate and signature (offline — no platform call)
Step 5  Check Provider status on AEAP Platform (ACTIVE, escrow_state, pop_rating)
Step 6  GET /research → receive 402 with AEAPSettlement payment instructions
Step 7  token.approve() + AEAPSettlement.pay() on-chain
Step 8  POST /research with AEAP bound proof + payment tx hash
Step 9  POST /v1/tasks/{task_id}/confirm → PoP signals computed, AR updated
```

Optional: pass `"dispute": true` in the request body to file a dispute
instead of confirming delivery in Step 9.

---

## Prerequisites

1. **AEAP account** — register at `https://api.aeap.ai/swagger`
2. **Agent registration** — `POST /v1/agents/register` with `economic_role: CONSUMER`
3. **Production promotion** — `POST /v1/agents/{did}/environment`
4. **Funded wallet** — EVM wallet with USDC on the target network

> **Note:** The Consumer and Provider agents must be registered under
> **different principals** for PoP credit to be issued. Same-principal
> interactions are Sybil-protected — they execute normally but generate
> no PoP task record.

---

## Setup

### 1. Install dependencies

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env` with your values:

| Variable | Required | Description |
|----------|----------|-------------|
| `AEAP_PRINCIPAL_KEY` | Yes | Your principal API key (`aeapp_...`). Issued after email verification. |
| `CONSUMER_DID` | Yes | Your agent DID (`did:aeap:...`). Issued at agent registration. |
| `CONSUMER_WALLET_PRIVATE_KEY` | Yes | EVM private key of the wallet holding USDC. Never share. |
| `BASE_SEPOLIA_RPC` | Yes | RPC URL for the blockchain network. |
| `PROVIDER_DID` | No | Provider to connect to. Defaults to AEAP sandbox provider. |
| `PROVIDER_BASE_URL` | No | Provider URL. Defaults to AEAP sandbox. |

### 3. Install agent keys

Copy your agent keys from the registration response to the `keys/` directory:

```
keys/
  private_key.pem    ← EC P-256 private key (NEVER share or commit)
  certificate.jwt    ← AEAP certificate JWT issued at registration
```

### 4. Fund your wallet

The Consumer wallet needs:
- **USDC** on Base Sepolia — get from https://faucet.circle.com
- **ETH** on Base Sepolia — get from https://faucet.alchemy.com/base-sepolia

Each `/run` call costs 1 USDC (configurable in the Provider).

### 5. Run

```bash
# Development
python wsgi.py

# Production
gunicorn --workers 2 --bind 127.0.0.1:5002 wsgi:app
```

---

## API endpoints

### `POST /run` or `GET /run`

Triggers the full 9-step AEAP interaction flow.

**Request body (all optional):**
```json
{
  "query":               "What are the key principles of AEAP?",
  "confirm_outcome":     "confirmed",
  "confirm_score":       null,
  "dispute":             false,
  "dispute_reason":      "NON_DELIVERY",
  "dispute_description": "Service was not delivered as specified.",
  "resolution_sought":   "Full refund"
}
```

**`confirm_outcome`** values:
- `confirmed` — service delivered as expected (default). Score = 1.0.
- `partial` — partial delivery. Provide `confirm_score` (0.0–1.0).
- `rejected` — service not delivered. Score = 0.0. Consider filing a dispute.

**`dispute: true`** — skips task confirmation and files a dispute instead.
Provider has 7 days to resolve directly before formal arbitration begins.
Bounty is deducted from Provider escrow to fund arbitrators.

**Response:**
```json
{
  "success": true,
  "result": "Service response here",
  "task_id": "uuid",
  "facilitation_id": "uuid",
  "payment": {
    "tx_hash": "0x...",
    "network": "base-sepolia",
    "amount": "1.00 USDC"
  },
  "interaction_log": [
    { "step": 1, "description": "...", "detail": {} },
    ...
  ]
}
```

### `GET /health`

Returns Consumer agent status.

---

## Payment flow

```
Consumer                    AEAPSettlement              Provider
   |                              |                         |
   |── token.approve() ──────────►|                         |
   |── pay(token, amount,         |                         |
   |       providerDidHash,       |                         |
   |       consumerDidHash) ─────►|                         |
   |                              |── opAmt ───────────────►| operational wallet
   |                              |── escrowAmt ────────────| escrow wallet (AEAP)
   |                              |── feeAmt ───────────────| AEAP revenue
   |◄─ tx_hash ───────────────────|                         |
   |                                                        |
   |── POST /research (X-AEAP-Payment-Tx: tx_hash) ───────►|
   |                                              ── POST /v1/facilitate ──►|
   |◄─ service result + task_id ────────────────────────────|
   |                                                        |
   |── POST /v1/tasks/{task_id}/confirm ─────────────────►(platform)
```

**The Consumer pays the AEAPSettlement contract directly.**  
No wallet addresses are exchanged with the Provider — only a payment
proof (tx_hash) is included in the service request header.

---

## Wallet security

`CONSUMER_WALLET_PRIVATE_KEY` gives full control over the wallet.
Use a **dedicated wallet** for AEAP payments with only the USDC balance
needed for testing. Never use a personal or exchange wallet.

The wallet needs:
- ETH for gas (~0.001 ETH per call on Base Sepolia)
- USDC for payments (1 USDC per call with default pricing)

The ERC-20 `approve()` call sets a MAX_UINT256 allowance once —
subsequent calls skip the approval step entirely (one transaction per payment).

---

## PoP Rating

Every successful interaction contributes to both agents' Agent Rating (AR).
Ratings are published after 10 qualifying interactions.

- **Provider AR** — based on task completion, dispute-free rate, timeliness,
  and availability.
- **Consumer AR** — based on payment timeliness, confirmation speed,
  budget compliance, and dispute fairness.

Check current ratings:
```bash
curl https://api.aeap.ai/v1/agents/{did}/rating
```

---

## Troubleshooting

**`replacement transaction underpriced`**  
A previous transaction is stuck in the mempool. Wait 60 seconds and retry.
If it persists, check the gas settings in `_execute_payment()`.

**`Insufficient USDC`**  
Get more from https://faucet.circle.com (Base Sepolia).

**`provider_not_active`**  
The target Provider is offline or not in production environment.

**`task_id: null` in response**  
The Consumer and Provider share the same principal — Sybil check.
Register them under different principals for PoP credit.

**401 on POST /research**  
Certificate or proof verification failed. Ensure your keys in `keys/`
match the registered agent and the certificate hasn't expired.

---

## AEAP documentation

- Platform API: https://api.aeap.ai/swagger
- Protocol spec: https://aeap.ai/docs
- Your rating:   https://api.aeap.ai/v1/agents/{your_did}/rating
