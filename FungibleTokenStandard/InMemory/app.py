from flask import Flask, request, jsonify
from ecdsa import VerifyingKey, SECP256k1, BadSignatureError
from zellular import Zellular , get_operators
from threading import Thread
from uuid import uuid4
import base64
import requests
import time
import json
import random
import hashlib
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_secret_key'



#-------------------------------------
# Fetch the list of operators and extract their socket URLs
def get_operator_urls():
    operators = get_operators()
    sockets = [op["socket"] for op in operators.values() if "socket" in op]
    return sockets

#-------------------------------------
# Initialize the BASE_URLS dynamically from the fetched operators
BASE_URLS = get_operator_urls()
APP_NAME = "APP_NAME"
GENESIS_ADDRESS = "your_genesis_public_key_base64"  # Replace with actual base64-encoded public key
TOKEN_NAME = "ZellularToken"
TOKEN_SYMBOL = "ZTK"
TOKEN_DECIMALS = 18
TOTAL_SUPPLY = 1000000000 * (10 ** TOKEN_DECIMALS)  # 1 billion tokens
BALANCES_FILE = 'balances_dump.json'
VARIABLES_FILE = 'variables_dump.json'
#-------------------------------------

zellular = Zellular(APP_NAME, BASE_URLS[0])

# Initialize balances and variables as dictionaries
balances = {}
variables = {}


# Function to check if files exist and load them
def load_files():
    # Load balances file if it exists
    if os.path.exists(BALANCES_FILE):
        with open(BALANCES_FILE, 'r') as f:
            global balances
            balances = json.load(f)
            print("Balances loaded from file.")
    else:
        # Initialize genesis address with total supply if file doesn't exist
        balances[GENESIS_ADDRESS] = TOTAL_SUPPLY
        print("Balances file not found, created with genesis address.")

    # Load variables file if it exists
    if os.path.exists(VARIABLES_FILE):
        with open(VARIABLES_FILE, 'r') as f:
            global variables
            variables = json.load(f)
            print("Variables loaded from file.")
    else:
        # Initialize default variables
        variables["last_process_indexes"] = 0
        print("Variables file not found, initialized with default values.")

# Function to initialize the system
@app.before_request
def initialize():
    load_files()


# Function to verify signatures
def verify(tx):
    message = ','.join([tx[key] for key in ['recipient', 'amount']]).encode('utf-8')
    try:
        public_key = base64.b64decode(tx['public_key'])
        signature = base64.b64decode(tx['signature'])
        vk = VerifyingKey.from_string(public_key, curve=SECP256k1)
        vk.verify(signature, message)
    except (BadSignatureError, ValueError):
        return False
    return True


# Function to calculate the hash of a file
def calculate_file_hash(filepath):
    hasher = hashlib.sha256()
    with open(filepath, 'rb') as f:
        buf = f.read()
        hasher.update(buf)
    return hasher.hexdigest()


# Function to fetch the latest balances file from a random URL
def fetch_latest_balances():
    selected_base_url = random.choice(BASE_URLS)
    try:
        response = requests.get(f"{selected_base_url}/balances_dump.json")
        if response.status_code == 200:
            return response.json()
        else:
            print(f"Failed to fetch balances from {selected_base_url}")
            return None
    except Exception as e:
        print(f"Error fetching balances: {e}")
        return None


# Function to replay transactions to update balances
def replay_transactions(latest_transactions):
    for tx in latest_transactions:
        if tx["operation"] == "transfer":
            _transfer(tx)
        else:
            print("Invalid transaction", tx)


# ERC-20 like token details
@app.route('/info', methods=['GET'])
def info():
    return jsonify({
        "name": TOKEN_NAME,
        "symbol": TOKEN_SYMBOL,
        "total_supply": TOTAL_SUPPLY,
        "decimals": TOKEN_DECIMALS
    })


# Retrieve balance of an address
@app.route('/balance_of', methods=['GET'])
def balance_of():
    public_key = request.args.get('public_key')
    balance = balances.get(public_key, 0)
    return jsonify({"balance": balance})


# Token transfer endpoint
@app.route('/transfer', methods=['POST'])
def transfer():
    # Verify signature
    if not verify(request.form):
        return jsonify({"message": "Invalid signature"}), 403

    # Randomly select a base URL from the list for this transaction
    selected_base_url = random.choice(BASE_URLS)
    zellular.base_url = selected_base_url  # Update the Zellular instance with the selected URL

    # Add the tx to Zellular sequencer
    tx = {
        "operation": "transfer",
        "tx_id": str(uuid4()),  # Unique tx ID
        "public_key": request.form['public_key'],
        "recipient": request.form['recipient'],
        "amount": int(request.form['amount']),
    }
    txs = [tx]
    zellular.send(txs)

    return {'success': True}


# Process finalized txs from the Zellular sequencer
def process_txs():
    while True:
        for batch, index in zellular.batches(after=int(variables.get("last_process_indexes", 0))):
            txs = json.loads(batch)
            for i, tx in enumerate(txs):
                if tx["operation"] == "transfer":
                    _transfer(tx)  # Process each tx from the sequencer
                else:
                    print("Invalid transaction", tx)
            # Update last processed index
            variables["last_process_indexes"] = index
        time.sleep(1)  # Adjust the interval if necessary


def _transfer(tx):
    if not verify(tx):
        print("Invalid signature for transaction")
        return
    
    sender_public_key = tx['public_key']
    recipient_public_key = tx['recipient']
    amount = int(tx['amount'])

    sender_balance = balances.get(sender_public_key, 0)
    recipient_balance = balances.get(recipient_public_key, 0)

    if sender_balance < amount:
        print(f"Error: insufficient funds for {sender_public_key}")
        return

    # Update balances
    balances[sender_public_key] = sender_balance - amount
    balances[recipient_public_key] = recipient_balance + amount


# Periodic JSON dump function based on height(txs count in this case) divisible by 100000
def dump_json_on_height():
    while True:
        # Get the current height from variables
        current_height = variables.get("height", 0)

        if current_height % 100000 == 0:
            # Dump balances and variables to a JSON file
            with open(BALANCES_FILE, 'w') as f:
                json.dump(balances, f)
            with open(VARIABLES_FILE, 'w') as f:
                json.dump(variables, f)
            print(f"Data dumped to JSON files at height {current_height}.")

        # Increment height for testing purposes (this should reflect the actual network height)
        variables["height"] = current_height + 1
        time.sleep(1)  # Adjust the interval if necessary


if __name__ == '__main__':
    # Start the transaction processing thread
    Thread(target=process_txs, daemon=True).start()
    # Start the periodic JSON dump thread based on height
    Thread(target=dump_json_on_height, daemon=True).start()
    # Run the Flask app
    app.run(debug=True)
