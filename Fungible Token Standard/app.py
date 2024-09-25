from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from ecdsa import VerifyingKey, SECP256k1, BadSignatureError
import base64
import requests
import zellular
from threading import Thread
import time
import json

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///token_transfers.db'
app.config['SECRET_KEY'] = 'your_secret_key'
db = SQLAlchemy(app)
base_url = "http://5.161.230.186:6001"
app_name = "token_transfer"
genesis_address = "your_genesis_public_key_base64"  # Replace with actual base64-encoded public key
TOKEN_NAME = "ZellularToken"
TOKEN_SYMBOL = "ZTK"
TOKEN_DECIMALS = 18
TOTAL_SUPPLY = 1000000000 * (10 ** TOKEN_DECIMALS)  # 1 billion tokens

# Token balance model using int to represent whole tokens in smallest unit (like "wei")
class Balance(db.Model):
    public_key = db.Column(db.String(500), primary_key=True)
    amount = db.Column(db.Integer, nullable=False)

@app.before_first_request
def create_tables():
    db.create_all()
    # Initialize one address with 1 billion tokens
    if not Balance.query.filter_by(public_key=genesis_address).first():
        genesis_balance = Balance(public_key=genesis_address, amount=TOTAL_SUPPLY)
        db.session.add(genesis_balance)
        db.session.commit()

# ERC-20 like token details
@app.route('/token_info', methods=['GET'])
def token_info():
    return jsonify({
        "name": TOKEN_NAME,
        "symbol": TOKEN_SYMBOL,
        "total_supply": TOTAL_SUPPLY,
        "decimals": TOKEN_DECIMALS
    })

# Retrieve balance of an address
@app.route('/balanceOf', methods=['GET'])
def balance_of():
    public_key = request.args.get('public_key')
    balance = Balance.query.filter_by(public_key=public_key).first()
    if balance:
        return jsonify({"balance": balance.amount})
    else:
        return jsonify({"balance": 0})

# Function to verify signatures
def verify_transaction(transaction):
    message = ','.join([transaction[key] for key in ['recipient', 'amount']]).encode('utf-8')
    try:
        public_key = base64.b64decode(transaction['public_key'])
        signature = base64.b64decode(transaction['signature'])
        vk = VerifyingKey.from_string(public_key, curve=SECP256k1)
        vk.verify(signature, message)
    except (BadSignatureError, ValueError):
        return False
    return True

# Token transfer endpoint
@app.route('/transfer', methods=['POST'])
def transfer_tokens():
    # Verify signature
    if not verify_transaction(request.form):
        return jsonify({"message": "Invalid signature"}), 403
    
    sender_public_key = request.form['public_key']
    recipient_public_key = request.form['recipient']
    amount = int(request.form['amount'])  # Using int to represent whole tokens (smallest unit like "wei")
    
    sender_balance = Balance.query.filter_by(public_key=sender_public_key).first()
    recipient_balance = Balance.query.filter_by(public_key=recipient_public_key).first()

    if not sender_balance or sender_balance.amount < amount:
        return jsonify({"message": "Insufficient balance"}), 403

    # Add the transaction to Zellular sequencer
    tx = {
        "operation": "transfer",
        "tx_id": str(uuid4()),  # Unique transaction ID
        "public_key": sender_public_key,
        "recipient": recipient_public_key,
        "amount": amount,
        "timestamp": int(time.time())
    }
    txs = [tx]
    zresponse = requests.put(f"{base_url}/node/{app_name}/batches", json=txs)

    # Verify the response from Zellular
    if zresponse.status_code != 200:
        return jsonify({"message": "Error submitting transaction to Zellular"}), 500
    
    return {'success': True}

# Process finalized transactions from the Zellular sequencer
def process_loop():
    verifier = zellular.Verifier(app_name, base_url)
    for batch, index in verifier.batches(after=0):
        txs = json.loads(batch)
        for i, tx in enumerate(txs):
            process_transfer(tx)  # Process each transaction from the sequencer

def process_transfer(transaction):
    sender_public_key = transaction['public_key']
    recipient_public_key = transaction['recipient']
    amount = int(transaction['amount'])

    if not verify_transaction(transaction):
        return jsonify({"message": "Invalid signature"}), 403

    sender_balance = Balance.query.filter_by(public_key=sender_public_key).first()
    recipient_balance = Balance.query.filter_by(public_key=recipient_public_key).first()

    if not sender_balance or sender_balance.amount < amount:
        print(f"Error: insufficient funds for {sender_public_key}")
        return

    # Update balances
    sender_balance.amount -= amount
    if recipient_balance:
        recipient_balance.amount += amount
    else:
        new_recipient_balance = Balance(public_key=recipient_public_key, amount=amount)
        db.session.add(new_recipient_balance)

    db.session.commit()

if __name__ == '__main__':
    Thread(target=process_loop).start()
    app.run(debug=True)
