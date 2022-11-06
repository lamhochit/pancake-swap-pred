import json
import time
import sys
import os
import logging

from web3 import Web3

from config import private_key, address

class Prediction:
    
    # bet params
    min_balance_size = 0        # min bnb balance
    gas_fee_reserve = 0.1       # bnb reserved for gas fee
    bull_win_rate = 0.50        # bull win rate
    min_prize_pool = 25         # min prize pool size allowed
    min_bet_size = 0.05         # min bet_size
    kelly_cap = 0.3             # max kelly
    balance_override = 0.2      # balance override (0 = no override)

    # execution params
    execution_block = 4         # transaction is fired when lock block <= n block away
    gas_price = 5
    gas = 200000

    # logger
    logger = logging.getLogger(__name__)

    def __init__(
        self,
        address,
        private_key,
        bsc="https://bsc-dataseed.binance.org/",
        contract_address = '0x516ffd7D1e0Ca40b1879935B2De87cb20Fc1124b'
    ):
        self.w3 = Web3(Web3.HTTPProvider(bsc))
        if not self.w3.isConnected():
            raise Exception('Web3 not connected!')
        self.contract = self._load_contract(abi_name='prediction', address=contract_address)
        self.address = address
        self.private_key = private_key
        self.nonce = self.w3.eth.getTransactionCount(self.address)

    def _load_contract(self, abi_name, address):
        return self.w3.eth.contract(address=address, abi=self._load_abi(abi_name))

    @staticmethod
    def _load_abi(name: str) -> str:
        path = f'{os.path.dirname(os.path.abspath(__file__))}/assets/'
        with open(os.path.abspath(path + f'{name}.abi')) as f:
            abi: str = json.load(f)
        return abi

    def _get_tx_params(self, value=0):
        """Get generic transaction parameters."""
        return {
            "from": self.address,
            "value": value,
            "gas": self.gas,
            "gasPrice": self.gas_price,
            "nonce": max(self.nonce, self.w3.eth.getTransactionCount(self.address))
        }

    def _build_and_send_tx(self, function, tx_params=None):
        """Build and send a transaction."""
        if tx_params is None:
            tx_params = self._get_tx_params()
        tx = function.buildTransaction(tx_params)
        signed_txn = self.w3.eth.account.sign_transaction(tx, private_key=self.private_key)
        try:
            return self.w3.eth.sendRawTransaction(signed_txn.rawTransaction)
        finally:
            self.logger.debug(f"nonce: {tx_params['nonce']}")
            self.nonce = tx_params["nonce"] + 1

    def place_bet(self, bet_size, direction):
        if bet_size is None or direction is None:
            return None
        bet_functions = {'BULL': self.contract.functions.BetBull, 'BEAR': self.contract.functions.BetBear}
        return self._build_and_send_tx(bet_functions[direction](bet_size))

    def claim_rewards(self, epoch, gas=120000, gas_price=5):
        if epoch < 0 or not self.contract.functions.claimable(epoch, self.address).call():
            return None
        
        return self._build_and_send_tx(
            self.contract.functions.claim(epoch),
            self._get_tx_params() | {
                'gas': gas,
                'gasPrice': self.w3.toWei(gas_price, 'gwei')
            }
        )

    def compute_kelly(self, bull_odd, bear_odd):
        bull_kelly = (self.bull_win_rate*bull_odd-1)/(bull_odd-1)
        bear_kelly = ((1-self.bull_win_rate)*bear_odd-1)/(bear_odd-1)
        return bull_kelly, bear_kelly

    def start(self):
        
        curr_epoch = self.contract.functions.currentEpoch().call()
        prev_epoch = curr_epoch-1

        while True:

            curr_epoch = self.contract.functions.currentEpoch().call()
            bet_on = False if curr_epoch != prev_epoch else bet_on
            balance = self.balance_override if self.balance_override > 0 else float(
                self.w3.fromWei(self.w3.eth.get_balance(self.address), 'ether')) - self.gas_fee_reserve

            if balance < self.min_balance_size:
                sys.exit(f'Balance should not be less than {self.min_balance_size}')

            rounds = self.contract.functions.rounds(curr_epoch).call()
            blocks_away = rounds[2]-self.w3.eth.block_number
            bull_amount, bear_amount = rounds[7], rounds[8]
            total_amount = rounds[6]
            
            if blocks_away > 50:
                tx_hash = self.claim_rewards(prev_epoch-1)
                if receipt := self.w3.eth.wait_for_transaction_receipt(tx_hash):
                    self.logger.info(f"Claim status: {receipt['status']}")

            if bull_amount > 0 and bear_amount > 0:
                bull_odd = (total_amount-self.gas*self.gas_price/2)/bull_amount
                bear_odd = (total_amount-self.gas*self.gas_price/2)/bear_amount
                
                bull_kelly, bear_kelly = self.compute_kelly(bull_odd=bull_odd,bear_odd=bear_odd)
                prize_pool = float(self.w3.fromWei(total_amount, 'ether'))
                
                self.logger.info(f'Round: {curr_epoch} | Blocks Away: {blocks_away} | Bull Odds: {bull_odd:.3f} | Bull Kelly: {bull_kelly:.0%} | Bear Odds: {bear_odd:.3f} | Bear Kelly: {bear_kelly:.0%} | Prize Pool: {prize_pool:.3f} | Balance: {balance:.3f}')
                direction = 'BULL' if bull_kelly > bear_kelly else 'BEAR'
                bet_size = balance*min(max(bull_kelly, bear_kelly), self.kelly_cap)
                
                if not bet_on and bet_size >= self.min_bet_size and 1 < blocks_away <= self.execution_block and prize_pool > self.min_prize_pool:
                    bet_size = min(bet_size, self.max_bet_size)
                    try:
                        tx_hash = self.place_bet(bet_size, direction)
                        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)
                        if receipt['status'] == 1:
                            bet_on = True
                            self.logger.info(f'A {bet_size:.2f} {direction} BNB Bet is placed!\n')
                        elif receipt['status'] == 0:
                            self.logger.info(f'A {bet_size:.2f} {direction} BNB Bet has not been placed!\n')
                            continue
                    except:
                        continue

            prev_epoch = curr_epoch
            time.sleep(1)


if __name__ == '__main__':
    
    logging.basicConfig(level = logging.INFO, format=('[%(levelname)s] %(message)s'))
    
    Prediction(address, private_key).start()
    
    logging.shutdown()
