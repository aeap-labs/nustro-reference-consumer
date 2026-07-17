"""
Consumer — Nustro Reference Consumer Agent (Sprint 4)

Demonstrates the full AEA/P payment flow with optional dispute filing:

  Phase 1: Mutual authentication with Provider
  Phase 2: Receive 402 with NustroSettlement payment instructions
  Phase 3: Approve ERC-20 + call NustroSettlement.pay() on-chain
  Phase 4: Retry service call with payment proof
  Phase 6: Confirm PoP task (automatic) or file a dispute (optional)

Endpoints:
  GET  /health  — health check
  POST /run     — trigger full interaction with Provider
  GET  /run     — same, for quick testing

Optional body fields:
  query:               research query (default: 'What are the key principles...')
  confirm_outcome:     confirmed | partial | rejected (default: confirmed)
  confirm_score:       0.0-1.0 score if outcome is partial
  dispute:             true to file a dispute instead of confirming (default: false)
  dispute_reason:      NON_DELIVERY | PARTIAL_DELIVERY | QUALITY | FRAUD | OTHER
  dispute_description: description of the issue
  resolution_sought:   what the consumer wants (default: 'Full refund')
"""

import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'shared'))

from flask import Flask, request, jsonify, send_file
import requests as http_requests
from aeap_client import AEAPClient
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

app = Flask(__name__)

# ── Configuration ──────────────────────────────────────────────────────────────

CONSUMER_DID      = os.environ.get('CONSUMER_DID', '')
PROVIDER_DID      = os.environ.get('PROVIDER_DID', '')
PROVIDER_BASE_URL = os.environ.get('PROVIDER_BASE_URL', 'http://localhost:5001')
OPERATOR_URL      = os.environ.get('OPERATOR_URL', 'https://api.nustro.ai')

# Build the client from keys/ at startup IF a DID + key files are present.
# Otherwise stay unconfigured until POST /configure supplies an identity — so
# the app boots for a UI-driven demo with no .env.
def _build_client(agent_did, key_path, cert_path, operator_url):
    try:
        if agent_did and os.path.exists(key_path) and os.path.exists(cert_path):
            return AEAPClient(agent_did=agent_did, private_key_path=key_path,
                              certificate_path=cert_path, operator_url=operator_url)
    except Exception as e:
        print(f"[CONFIG] startup identity not loaded: {e}", flush=True)
    return None

client = _build_client(
    CONSUMER_DID,
    os.path.join(os.path.dirname(__file__), 'keys', 'private_key.pem'),
    os.path.join(os.path.dirname(__file__), 'keys', 'certificate.jwt'),
    OPERATOR_URL,
)

# Minimal ERC-20 ABI
ERC20_ABI = json.loads('[{"inputs":[{"name":"spender","type":"address"},{"name":"amount","type":"uint256"}],"name":"approve","outputs":[{"name":"","type":"bool"}],"stateMutability":"nonpayable","type":"function"},{"inputs":[{"name":"account","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"},{"inputs":[{"name":"owner","type":"address"},{"name":"spender","type":"address"}],"name":"allowance","outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"}]')

# Minimal NustroSettlement ABI
SETTLEMENT_ABI = json.loads('[{"inputs":[{"name":"token","type":"address"},{"name":"amount","type":"uint256"},{"name":"providerDidHash","type":"bytes32"},{"name":"consumerDidHash","type":"bytes32"}],"name":"pay","outputs":[],"stateMutability":"nonpayable","type":"function"}]')

RPC_URLS = {
    'base-sepolia': os.environ.get('BASE_SEPOLIA_RPC', ''),
    'base':         os.environ.get('BASE_MAINNET_RPC', ''),
}


def _execute_payment(payment_method: dict) -> dict:
    """Execute blockchain payment via NustroSettlement.pay()."""
    private_key = os.environ.get('CONSUMER_WALLET_PRIVATE_KEY', '')
    if not private_key:
        return {
            'success':   False,
            'tx_hash':   None,
            'message':   'CONSUMER_WALLET_PRIVATE_KEY not configured.',
            'simulated': True,
        }

    network = payment_method.get('network', 'base-sepolia')
    rpc_url = RPC_URLS.get(network, '')
    if not rpc_url:
        return {'success': False, 'tx_hash': None,
                'message': f"No RPC configured for network: {network}"}

    try:
        from web3 import Web3
        w3       = Web3(Web3.HTTPProvider(rpc_url))
        account  = w3.eth.account.from_key(private_key)
        chain_id = int(payment_method.get('chain_id', 84532))

        contract_addr     = Web3.to_checksum_address(payment_method['contract'])
        token_addr        = Web3.to_checksum_address(payment_method['token'])
        amount            = int(payment_method['amount'])
        provider_did_hash = bytes.fromhex(payment_method['provider_did_hash'].lstrip('0x'))
        consumer_did_hash = Web3.keccak(text=CONSUMER_DID)

        token_contract = w3.eth.contract(address=token_addr, abi=ERC20_ABI)
        balance = token_contract.functions.balanceOf(account.address).call()
        if balance < amount:
            return {'success': False, 'tx_hash': None,
                    'message': f"Insufficient USDC. Have: {balance/1e6:.2f}, Need: {amount/1e6:.2f}"}

        nonce        = w3.eth.get_transaction_count(account.address)
        max_priority = w3.to_wei(2, 'gwei')
        max_fee      = w3.to_wei(50, 'gwei')

        # Only approve if current allowance < amount (MAX_UINT256 set once, never reset)
        MAX_UINT256 = 2**256 - 1
        current_allowance = token_contract.functions.allowance(
            account.address, contract_addr
        ).call()

        if current_allowance < amount:
            approve_tx = token_contract.functions.approve(
                contract_addr, MAX_UINT256
            ).build_transaction({'chainId': chain_id, 'from': account.address,
                                  'nonce': nonce, 'maxFeePerGas': max_fee, 'maxPriorityFeePerGas': max_priority})
            approve_tx['gas'] = w3.eth.estimate_gas(approve_tx)
            signed_approve = account.sign_transaction(approve_tx)
            approve_hash = w3.eth.send_raw_transaction(signed_approve.raw_transaction)
            w3.eth.wait_for_transaction_receipt(approve_hash, timeout=60)
            nonce = w3.eth.get_transaction_count(account.address)  # refresh after approve

        # Pay
        settlement = w3.eth.contract(address=contract_addr, abi=SETTLEMENT_ABI)
        pay_tx = settlement.functions.pay(
            token_addr, amount, provider_did_hash, consumer_did_hash
        ).build_transaction({'chainId': chain_id, 'from': account.address,
                              'nonce': nonce, 'maxFeePerGas': max_fee, 'maxPriorityFeePerGas': max_priority})
        pay_tx['gas'] = w3.eth.estimate_gas(pay_tx)
        signed_pay  = account.sign_transaction(pay_tx)
        pay_hash    = w3.eth.send_raw_transaction(signed_pay.raw_transaction)
        receipt     = w3.eth.wait_for_transaction_receipt(pay_hash, timeout=60)

        if receipt['status'] != 1:
            return {'success': False, 'tx_hash': pay_hash.hex(),
                    'message': 'NustroSettlement.pay() reverted.'}

        return {'success': True, 'tx_hash': pay_hash.hex(),
                'network': network, 'amount': amount / 1e6,
                'message': 'Payment confirmed on-chain.'}

    except Exception as e:
        return {'success': False, 'tx_hash': None,
                'message': f"Payment execution failed: {str(e)}"}


def _confirm_task(task_id: str, data: dict) -> dict:
    """
    Confirm a PoP task via Nustro Operator.
    Called from Step 9 (default behavior — no dispute requested).
    """
    if client is None:
        return {'success': False, 'message': 'Agent identity not configured (POST /configure).'}

    outcome = data.get('confirm_outcome', 'confirmed')
    score   = data.get('confirm_score', None)
    note    = data.get('confirm_note', 'Service delivered as expected.')

    payload = {'outcome': outcome, 'note': note}
    if outcome == 'partial' and score is not None:
        payload['score'] = score

    # Agent-authenticated: we confirm AS the consumer agent (certificate +
    # request-bound proof), not with a management key (Operator Ref v1.2 §4.5).
    # Sign over the exact bytes we send, so serialize the body ourselves.
    path = f"/v1/tasks/{task_id}/confirm"
    body = json.dumps(payload).encode('utf-8')
    headers = client.operator_request_headers('POST', path, body=body)
    headers['Content-Type'] = 'application/json'

    try:
        resp = http_requests.post(
            f"{OPERATOR_URL}{path}",
            headers=headers,
            data=body,
            timeout=10,
        )
        result = resp.json()
        if resp.status_code == 200:
            return {'success': True, **result}
        else:
            return {'success': False,
                    'message': result.get('message', 'Confirmation failed'),
                    'error':   result.get('error')}
    except Exception as e:
        return {'success': False, 'message': str(e)}


def _file_dispute(facilitation_id: str, data: dict, gross_amt: str) -> dict:
    """
    File a dispute against a facilitation via Nustro Operator.

    Called from Step 9 when data['dispute'] == True.
    The Consumer's principal key is used (owns consumer_did in the facilitation).
    """
    principal_key = os.environ.get('NUSTRO_PRINCIPAL_KEY', '')
    if not principal_key:
        return {'success': False, 'message': 'NUSTRO_PRINCIPAL_KEY not configured.'}

    try:
        resp = http_requests.post(
            f"{OPERATOR_URL}/v1/disputes",
            headers={
                'Nustro-Api-Key': principal_key,
                'Content-Type': 'application/json',
            },
            json={
                'facilitation_id':  facilitation_id,
                'reason':           data.get('dispute_reason', 'NON_DELIVERY'),
                'description':      data.get('dispute_description',
                                             'Service was not delivered as specified.'),
                'resolution_sought': data.get('resolution_sought', 'Full refund'),
                'disputed_amount':  gross_amt or '1.000000',
                'currency':         'USDC',
            },
            timeout=10,
        )
        result = resp.json()
        if resp.status_code == 201:
            return {'success': True, **result}
        else:
            return {'success': False, 'message': result.get('message', 'Filing failed'),
                    'error': result.get('error')}
    except Exception as e:
        return {'success': False, 'message': str(e)}


# ── Run endpoint ──────────────────────────────────────────────────────────────

@app.route('/', methods=['GET'])
def ui():
    """Local console — configure this agent and drive a run from the browser
    (see ui.html)."""
    return send_file(os.path.join(os.path.dirname(__file__), 'ui.html'))


@app.route('/configure', methods=['POST'])
def configure():
    """Set this agent's identity + counterparty at runtime (for the demo UI),
    instead of reading .env at startup.

    Body: consumer_did, provider_did, provider_base_url, private_key (PEM),
    certificate (JWT) — required; operator_url, nustro_principal_key,
    wallet_private_key, base_sepolia_rpc — optional.

    The normal purchase/confirm flow authenticates AS the consumer agent
    (certificate + request-bound proof) — no management key needed. The
    nustro_principal_key is only used to FILE A DISPUTE (a management-surface
    call); leave it blank unless you exercise the dispute path.

    LOCAL / TRUSTED DEMO USE ONLY: this accepts an agent private key over HTTP.
    Never expose it on an untrusted network.
    """
    global CONSUMER_DID, PROVIDER_DID, PROVIDER_BASE_URL, OPERATOR_URL, client, RPC_URLS
    data = request.get_json(silent=True) or {}

    required = ['consumer_did', 'provider_did', 'provider_base_url', 'private_key', 'certificate']
    missing  = [k for k in required if not (data.get(k) or '').strip()]
    if missing:
        return jsonify({'error': 'missing_fields', 'missing': missing}), 400

    operator_url = (data.get('operator_url') or OPERATOR_URL).strip()
    try:
        new_client = AEAPClient(
            agent_did=data['consumer_did'].strip(),
            private_key_pem=data['private_key'],
            certificate_jwt=data['certificate'],
            operator_url=operator_url,
        )
    except Exception as e:
        return jsonify({'error': 'invalid_identity',
                        'message': f'Could not load key/certificate: {e}'}), 400

    CONSUMER_DID      = data['consumer_did'].strip()
    PROVIDER_DID      = data['provider_did'].strip()
    PROVIDER_BASE_URL = data['provider_base_url'].strip()
    OPERATOR_URL      = operator_url
    client            = new_client

    if (data.get('nustro_principal_key') or '').strip():
        os.environ['NUSTRO_PRINCIPAL_KEY'] = data['nustro_principal_key'].strip()
    if (data.get('wallet_private_key') or '').strip():
        os.environ['CONSUMER_WALLET_PRIVATE_KEY'] = data['wallet_private_key'].strip()
    if (data.get('base_sepolia_rpc') or '').strip():
        os.environ['BASE_SEPOLIA_RPC'] = data['base_sepolia_rpc'].strip()
    RPC_URLS = {
        'base-sepolia': os.environ.get('BASE_SEPOLIA_RPC', ''),
        'base':         os.environ.get('BASE_MAINNET_RPC', ''),
    }

    return jsonify({
        'status':            'configured',
        'consumer_did':      CONSUMER_DID,
        'provider_did':      PROVIDER_DID,
        'provider_base_url': PROVIDER_BASE_URL,
        'operator_url':      OPERATOR_URL,
        'wallet_configured': bool(os.environ.get('CONSUMER_WALLET_PRIVATE_KEY')),
        'principal_key_set': bool(os.environ.get('NUSTRO_PRINCIPAL_KEY')),
    }), 200


@app.route('/run', methods=['GET', 'POST'])
def run():
    """
    Trigger a full AEA/P interaction with the Provider.

    Optional body:
      query:               research query
      dispute:             true to file a dispute after service (default: false)
      dispute_reason:      NON_DELIVERY | PARTIAL_DELIVERY | QUALITY | FRAUD | OTHER
      dispute_description: description of the issue
      resolution_sought:   what you want (default: 'Full refund')
    """
    if client is None:
        return jsonify({'error': 'not_configured',
                        'message': 'No agent identity loaded. POST /configure first.'}), 409

    data  = request.get_json(silent=True) or {}
    query = data.get('query', 'What are the key principles of AEA/P protocol?')

    log = []
    def step(n, description, detail=None):
        entry = {'step': n, 'description': description}
        if detail:
            entry['detail'] = detail
        log.append(entry)
        print(f"[STEP {n}] {description}", flush=True)

    # ── Step 1: Discovery ─────────────────────────────────────────────────────
    step(1, 'Fetch Provider discovery document',
         f"GET {PROVIDER_BASE_URL}/.well-known/aeap")
    try:
        discovery_resp = http_requests.get(
            f"{PROVIDER_BASE_URL}/.well-known/aeap", timeout=5)
        discovery = discovery_resp.json()
    except Exception as e:
        return jsonify({'error': 'discovery_failed', 'log': log, 'detail': str(e)}), 500
    step(1, 'Discovery document received', {
        'agent_id': discovery.get('agent_id'),
        'economic_role': discovery.get('economic_role'),
    })

    # ── Step 2: Challenge nonce ───────────────────────────────────────────────
    step(2, 'Get challenge nonce from Nustro Operator')
    try:
        nonce_resp = http_requests.get(f"{OPERATOR_URL}/v1/verify/challenge", timeout=5)
        nonce = nonce_resp.json()['nonce']
    except Exception as e:
        return jsonify({'error': 'challenge_failed', 'log': log, 'detail': str(e)}), 500
    step(2, 'Nonce received', {'nonce': nonce[:16] + '...'})

    # ── Step 3: Challenge Provider ────────────────────────────────────────────
    step(3, 'Send challenge to Provider')
    try:
        challenge_resp = http_requests.post(
            discovery['challenge_endpoint'], json={'nonce': nonce}, timeout=5)
        challenge_data = challenge_resp.json()
    except Exception as e:
        return jsonify({'error': 'challenge_response_failed', 'log': log, 'detail': str(e)}), 500
    step(3, 'Provider challenge response received', {
        'agent_id': challenge_data.get('agent_id'),
        'has_certificate': bool(challenge_data.get('certificate')),
    })

    # ── Step 4: Verify Provider ───────────────────────────────────────────────
    step(4, 'Verify Provider certificate and challenge response')
    verification = client.verify_certificate_and_response(
        certificate_jwt=challenge_data['certificate'],
        challenge_response=challenge_data['challenge_response'],
        timestamp=challenge_data['timestamp'],
        nonce=nonce,
    )
    if not verification['verified']:
        return jsonify({'error': 'provider_verification_failed',
                        'reason': verification.get('reason'), 'log': log}), 401
    step(4, 'Provider identity verified', {
        'agent_id': verification.get('agent_id'),
        'cert_tier': verification.get('cert_tier'),
    })

    # ── Step 5: Check Provider status ─────────────────────────────────────────
    step(5, 'Check Provider status on Nustro Operator')
    provider_status = client._get_status(PROVIDER_DID)
    if not provider_status or provider_status.get('status') != 'ACTIVE':
        return jsonify({'error': 'provider_not_active', 'log': log}), 503
    step(5, 'Provider status verified', {
        'status':       provider_status.get('status'),
        'environment':  provider_status.get('environment'),
        'escrow_state': provider_status.get('escrow_state'),
        'agent_rating': provider_status.get('agent_rating'),
    })

    # ── Step 6: GET /research → 402 ──────────────────────────────────────────
    # Send our DID so the Operator can spend-check + mint a payment intent.
    step(6, 'Call GET /research — expect 402 Payment Required',
         f"GET {PROVIDER_BASE_URL}/research?consumer_did={CONSUMER_DID}")
    try:
        payment_resp = http_requests.get(
            f"{PROVIDER_BASE_URL}/research",
            params={'consumer_did': CONSUMER_DID}, timeout=5)
        payment_data = payment_resp.json()
    except Exception as e:
        return jsonify({'error': 'payment_request_failed', 'log': log, 'detail': str(e)}), 500

    # The Operator may refuse the payment under our spend policy (relayed by the
    # provider as 403). Surface it — the principal must widen its spend scope.
    if payment_resp.status_code == 403 and payment_data.get('error') == 'spend_policy_violation':
        step(6, 'Payment refused by spend policy', payment_data.get('detail'))
        return jsonify({'error': 'spend_policy_violation',
                        'detail': payment_data.get('detail'), 'log': log}), 403

    if payment_resp.status_code != 402:
        # Carry the Provider's own explanation through — it usually IS the
        # diagnosis (e.g. its 503 says the Operator rejected the intent request
        # because the Provider has no principal key). Swallowing it leaves the
        # caller staring at a bare 'expected_402'.
        step(6, 'Provider did not return 402', {
            'status_code': payment_resp.status_code,
            'error':       payment_data.get('error'),
            'message':     payment_data.get('message'),
        })
        return jsonify({'error':       'expected_402',
                        'status_code': payment_resp.status_code,
                        'message':     payment_data.get('message'),
                        'detail':      payment_data.get('error'),
                        'log':         log}), 500

    methods = payment_data.get('methods', [])
    method  = next((m for m in methods if m.get('type') == 'blockchain'), None)
    if not method:
        return jsonify({'error': 'no_blockchain_method', 'log': log}), 500

    step(6, '402 Payment Required received', {
        'market':   payment_data.get('market'),
        'network':  method.get('network'),
        'contract': method.get('contract'),
        'amount':   f"{int(method.get('amount', 0)) / 1e6:.2f} USDC",
        'expires_at': method.get('expires_at'),
    })

    # ── Step 7: ERC-20 approve + NustroSettlement.pay() ─────────────────────────
    step(7, 'Approve ERC-20 + call NustroSettlement.pay()',
         f"token.approve({method.get('contract')}, {method.get('amount')}) "
         f"then NustroSettlement.pay(token, amount, providerDidHash, consumerDidHash)")

    payment_result = _execute_payment(method)

    if payment_result.get('simulated'):
        step(7, 'Payment simulated (no wallet configured)', {
            'message': payment_result['message'],
            'note':    'Add CONSUMER_WALLET_PRIVATE_KEY to .env for real payments',
        })
        tx_hash    = None
        tx_network = method.get('network')
    elif not payment_result.get('success'):
        return jsonify({'error': 'payment_failed',
                        'message': payment_result.get('message'), 'log': log}), 402
    else:
        tx_hash    = payment_result['tx_hash']
        tx_network = method.get('network')
        step(7, 'Payment confirmed on-chain', {
            'tx_hash':  tx_hash,
            'network':  tx_network,
            'amount':   f"{payment_result.get('amount', 0):.2f} USDC",
            'contract': method.get('contract'),
        })

    # ── Step 8: POST /research with AEA/P proof + payment tx ──────────────────
    step(8, 'POST /research with AEA/P bound proof + payment tx hash',
         f"POST {PROVIDER_BASE_URL}/research")

    auth_headers = client.get_auth_headers(callee_did=PROVIDER_DID)
    if tx_hash:
        auth_headers['AEAP-Payment-Tx'] = json.dumps({
            'tx_hash': tx_hash, 'network': tx_network,
        })

    step(8, 'Bound proof generated', {
        'has_certificate': bool(auth_headers.get('AEAP-Certificate')),
        'has_proof':       bool(auth_headers.get('AEAP-Proof')),
        'has_payment_tx':  bool(auth_headers.get('AEAP-Payment-Tx')),
        'timestamp':       auth_headers.get('AEAP-Timestamp'),
    })

    try:
        research_resp = http_requests.post(
            f"{PROVIDER_BASE_URL}/research",
            headers={**auth_headers, 'Content-Type': 'application/json'},
            json={'query': query},
            timeout=30,
        )
        research_data = research_resp.json()
    except Exception as e:
        return jsonify({'error': 'research_call_failed', 'log': log, 'detail': str(e)}), 500

    if research_resp.status_code != 200:
        return jsonify({'error': 'research_failed',
                        'status_code': research_resp.status_code,
                        'detail': research_data, 'log': log}), research_resp.status_code

    step(8, 'Service response received', {
        'interaction_id':  research_data.get('interaction_id'),
        'verified':        research_data.get('verified'),
        'facilitation_id': research_data.get('facilitation_id'),
        'task_id':         research_data.get('task_id'),
    })

    # ── Step 9: Dispute filing (optional) or PoP confirmation (Sprint 4b) ────
    dispute_result = None
    facilitation_id = research_data.get('facilitation_id')
    gross_amt       = research_data.get('gross_amt', '1.000000')

    if data.get('dispute') and facilitation_id:
        step(9, 'Filing dispute against Provider (dispute=true requested)',
             f"POST {OPERATOR_URL}/v1/disputes — facilitation_id={facilitation_id}")

        dispute_result = _file_dispute(facilitation_id, data, gross_amt)

        if dispute_result.get('success'):
            step(9, 'Dispute filed successfully', {
                'dispute_id':       dispute_result.get('dispute_id'),
                'status':           dispute_result.get('status'),
                'bounty_amount':    dispute_result.get('bounty_amount'),
                'pre_arb_deadline': dispute_result.get('pre_arb_deadline'),
                'note':             'Provider has 7 days to resolve directly before formal arbitration.',
            })
        else:
            step(9, 'Dispute filing failed', {
                'error':   dispute_result.get('error'),
                'message': dispute_result.get('message'),
            })
    else:
        # Default: confirm the task (outcome: confirmed)
        task_id_for_confirm = research_data.get('task_id')
        if task_id_for_confirm:
            step(9, 'Confirming PoP task',
                 f"POST {OPERATOR_URL}/v1/tasks/{task_id_for_confirm}/confirm")
            confirm_result = _confirm_task(task_id_for_confirm, data)
            if confirm_result.get('success'):
                step(9, 'Task confirmed — signals computed', {
                    'task_id':        task_id_for_confirm,
                    'status':         confirm_result.get('status'),
                    'provider_r_i':   confirm_result.get('provider_r_i'),
                    'consumer_r_i':   confirm_result.get('consumer_r_i'),
                    'provider_ar':    confirm_result.get('provider_ar'),
                    'consumer_ar':    confirm_result.get('consumer_ar'),
                })
            else:
                step(9, 'Task confirmation failed', {
                    'error':   confirm_result.get('error'),
                    'message': confirm_result.get('message'),
                })
        else:
            step(9, 'No task_id returned — same-principal interaction (sybil check)', {
                'note': 'PoP credit requires different principals for Provider and Consumer.',
            })

    return jsonify({
        'success':         True,
        'query':           query,
        'result':          research_data.get('result'),
        'interaction_id':  research_data.get('interaction_id'),
        'facilitation_id': facilitation_id,
        'task_id':         research_data.get('task_id'),
        'payment': {
            'tx_hash':   tx_hash,
            'network':   tx_network,
            'amount':    f"{int(method.get('amount', 0)) / 1e6:.2f} USDC",
            'simulated': payment_result.get('simulated', False),
        },
        'dispute': dispute_result,
        'agents': {
            'consumer': CONSUMER_DID,
            'provider': PROVIDER_DID,
        },
        'interaction_log': log,
    })


# ── Health check ──────────────────────────────────────────────────────────────

@app.route('/health', methods=['GET'])
def health():
    if client is None:
        return jsonify({'status': 'unconfigured', 'role': 'CONSUMER',
                        'message': 'POST /configure to load an agent identity.'})
    status = client.get_own_status()
    wallet_configured = bool(os.environ.get('CONSUMER_WALLET_PRIVATE_KEY', ''))
    return jsonify({
        'status':            'ok',
        'agent_id':          CONSUMER_DID,
        'role':              'CONSUMER',
        'agent_status':      status.get('status')      if status else 'unknown',
        'environment':       status.get('environment') if status else 'unknown',
        'cert_tier':         status.get('cert_tier')   if status else 'unknown',
        'wallet_configured': wallet_configured,
        'payment_capable':   wallet_configured,
        # Needed for the PoP confirm / dispute calls at the end of a run.
        'principal_key_set': bool(os.environ.get('NUSTRO_PRINCIPAL_KEY', '')),
    })


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5002, debug=False)
