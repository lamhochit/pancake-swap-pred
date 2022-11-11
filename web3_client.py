import datetime as dt
import os
import json
import time
import pandas as pd
import _thread
import pytz
from core import root
from utils import logger
from web3 import Web3
from emoji import emojize
from core import binance_client
from utils import config_parser
from tg_bot import tg_message_bot
from web3.middleware import geth_poa_middleware

wallet = binance_client.read_keys('metamask.txt')

address_dict = {'contract_address': '0x0e09fabb73bd3ade0a17ecc321fd13a19e81ce82',
                'pancake_bnb_prediction_address': '0x516ffd7D1e0Ca40b1879935B2De87cb20Fc1124b',
                'chainlink_bnb_usdt_address': '0xD5c40f5144848Bd4EF08a9605d860e727b991513',
                'chainlink_bnb_usd_address': '0x0567F2323251f0Aab15c8dFb1967E4e8A7D42aeE',
                'address': '0x0E09FaBB73Bd3Ade0a17ECC321fD13a19e81cE82'}


def _load_abi(abi_name) -> str:
    path = os.path.join(root.ROOT_DIR, 'connect/assets/', abi_name)
    with open(path) as f:
        abi: str = json.load(f)
    return abi


class ContractConnectivity:
    def __init__(self, abi_name, address, provider="https://bsc-dataseed.binance.org:443"):
        self.w3 = Web3(Web3.HTTPProvider(provider))
        self.w3.middleware_onion.inject(geth_poa_middleware, layer=0)
        self.contract = self._load_contact(abi_name=abi_name, address=address)

    def _load_contact(self, abi_name, address):
        return self.w3.eth.contract(address=address, abi=_load_abi(abi_name))

    def get_balance(self):
        resp = self.w3.eth.get_balance(wallet[0])
        return float(self.w3.fromWei(resp, 'ether'))

    def get_latest_block(self):
        return self.w3.eth.get_block_number()

    def get_block_timestamp(self, block_number):
        return dt.datetime.fromtimestamp(self.w3.eth.get_block(block_number).timestamp)

    def show_all_functions(self):
        return self.contract.all_functions()


class ChainlinkConnectivity(ContractConnectivity):
    def __init__(self, abi_name, address, provider="https://bsc-dataseed.binance.org:443", logging=False):
        super(ChainlinkConnectivity, self).__init__(abi_name=abi_name, address=address, provider=provider)

    def latest_round_data(self):
        resp = self.contract.functions.latestRoundData().call()
        keys = ['round_id', 'answer', 'started_at', 'update_at', 'answered_in_round']
        resp = dict(zip(keys, resp))
        resp['started_at'] = dt.datetime.fromtimestamp(resp['started_at'])
        resp['update_at'] = dt.datetime.fromtimestamp(resp['update_at'])
        resp['answer'] = self.w3.fromWei(resp['answer'], 'gwei') * 10
        return resp
