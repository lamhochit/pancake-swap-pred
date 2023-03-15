# 🥞PancakeSwap Prediction Betting

## Getting Started
PancakeSwap runs a prediction game where users can bet on the next 5 minute price move's direction. The price feed uses Chainlink's oracle, and we discovered that most of the time there is a lagging relationship between Binance vs Chainlink.

- We define a premium threshold, when Binance's price for Cake deviates too much from Chainlink's last price, it is likely that Chainlink will take some time to catch up. Thus we will be quite certain about the next's prediction's direction.
- Fires a transaction when it closes in 4 blocks
- Claims reward when next round is more than 50 blocks away

Start the betting bot in `pancake_prediction.py`
```python3
import _thread
import time
import pandas as pd
import os
import datetime as dt
from connect.web3_client import ContractConnectivity
from connect.web3_client import ChainlinkConnectivity
from connect.web3_client import address_dict, wallet
from core import root
from utils import sound
from core import binance_client
from utils import logger
from utils import config_parser
from tg_bot import tg_message_bot


pp = PancakePrediction(abi_name='pancake_bnb_prediction.abi',
                           config='pancake_bnb_prediction.ini',
                           address=address_dict['pancake_bnb_prediction_address'],
                           logging=True,
                           live=True,
                           claim=True)

_thread.start_new_thread(pp.start, ())
_thread.start_new_thread(pp.update_balance, ())
```

## Web3 Client

The contract is placed under the `assets` folder
- When a betting event is triggered, the bot calls the contract and place a bet

## Position Sizing
Kelly Criterion is used to size the bet. Either a historical winning rate can be assigned or a 50% winning rate can be assigned to be more conservative.
