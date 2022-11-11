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


class PancakePrediction(ContractConnectivity):
    def __init__(self, abi_name, address, config, provider="https://bsc-dataseed.binance.org:443", logging=False,
                 live=False, claim=False):
        super(PancakePrediction, self).__init__(abi_name=abi_name, address=address, provider=provider)
        config_file = config_parser.parse(config)
        self.cl = ChainlinkConnectivity(abi_name='chainlink_bnb_usd_pricefeed.abi',
                                        address=address_dict['chainlink_bnb_usd_address'])
        # Params
        self.win_probability = float(config_file['params']['win_probability'])
        self.bet_threshold = float(config_file['params']['bet_threshold'])
        self.min_bet_odds = float(config_file['params']['min_bet_odds'])
        self.min_bet_size = float(config_file['params']['min_bet_size'])
        self.max_bet_size = float(config_file['params']['max_bet_size'])
        self.default_bet_size = float(config_file['params']['default_bet_size'])
        self.min_pool_size = float(config_file['params']['min_pool_size'])
        self.min_balance = float(config_file['params']['min_balance'])
        logger.Logger.log_message(
            'params: win prob: %s, bet thres: %s, min bet odds: %s, default bet size: %s, min bet: %s, max bet: %s, '
            'min pool: %s, min balance: %s' % (
                str(self.win_probability), str(self.bet_threshold), str(self.min_bet_odds), str(self.default_bet_size),
                str(self.min_bet_size), str(self.max_bet_size), str(self.min_pool_size), str(self.min_balance)))

        # Execution Price
        self.gas_price = float(config_file['execution']['gas_price'])
        self.gas = int(config_file['execution']['gas'])

        # Execution Details
        self.blocks_away = int(config_file['execution']['blocks_away'])
        self.execution_block = int(config_file['execution']['execution_block'])
        logger.Logger.log_message('execution: gas price: %s, gas: %s, blocks away: %s, execution block: %s' % (
            str(self.gas_price), str(self.gas), str(self.blocks_away), str(self.execution_block)))

        self.nonce = self.w3.eth.getTransactionCount(wallet[0])

        # Triggers for real time betting
        self.balance = self.get_balance()
        self.live = live
        self.claim = claim
        logger.Logger.log_message('status: live: %s, claim: %s' % (str(self.live), str(self.claim)))

        self.logging = logging
        if self.logging:
            self.logger = logger.Logger(log_name=config_file['logging']['log_name'])
            self.status_logger = logger.Logger(log_name=config_file['logging']['status_log_name'])

    def current_epoch(self):
        resp = self.contract.functions.currentEpoch().call()
        return resp

    def round_details(self, epoch):
        keys = ['epoch', 'start_block', 'lock_block', 'end_block', 'lock_price', 'close_price', 'total_amount',
                'bull_amount', 'bear_amount', 'reward_base_cal_amount', 'reward_amount', 'oracle_called']
        resp = self.contract.functions.rounds(epoch).call()
        resp = dict(zip(keys, resp))
        for ether in ['total_amount', 'bull_amount', 'bear_amount',
                      'reward_base_cal_amount', 'reward_amount']:
            resp[ether] = float(self.w3.fromWei(resp[ether], 'ether'))
        for price in ['lock_price', 'close_price']:
            resp[price] = float(self.w3.fromWei(resp[price], 'gwei') * 10)
        return resp

    def paused(self):
        resp = self.contract.functions.paused().call()
        return resp

    def min_bet_amount(self):
        resp = self.w3.fromWei(self.contract.functions.minBetAmount().call(), 'ether')
        return resp

    def _get_tx_params(self, value=0):
        """Get generic transaction parameters."""
        resp = {"from": wallet[0],
                "value": value,
                "gas": self.gas,
                "gasPrice": self.w3.toWei(self.gas_price, 'gwei'),
                "nonce": max(self.w3.eth.getTransactionCount(wallet[0]), self.nonce)
                }
        return resp

    def _build_and_send_tx(self, function, tx_params=None):
        """
        Build and send a transaction
        Pass tx_param dict to update the base tx_params
        """
        base_tx_params = self._get_tx_params()
        if tx_params is not None:
            base_tx_params.update(tx_params)
        tx = function.buildTransaction(base_tx_params)
        self.logger.log_message('building tx: %s' % tx)
        signed_txn = self.w3.eth.account.sign_transaction(tx, private_key=wallet[1])
        try:
            return self.w3.eth.sendRawTransaction(signed_txn.rawTransaction)
        finally:
            self.nonce += 1

    def place_bet(self, bet_size, direction):
        if bet_size > 0:
            bet_function = {'bull': self.contract.functions.betBull, 'bear': self.contract.functions.betBear}
            return self._build_and_send_tx(bet_function[direction](), tx_params={'value': self.w3.toWei(bet_size, 'ether')})
        else:
            self.logger.log_message('bet size smaller than 0')
            return None

    def claim_rewards(self, epoch):
        if not self.contract.functions.claimable(epoch, wallet[0]).call():
            self.logger.log_message('epoch %s is not claimable' % epoch)
        return self._build_and_send_tx(self.contract.functions.claim(epoch))

    def transaction_receipt(self, tx_hash):
        return self.w3.eth.wait_for_transaction_receipt(tx_hash)

    def current_round_details(self):
        epoch = self.current_epoch()
        resp = self.round_details(epoch)
        kelly = self.kelly_calculator(resp)
        print('Round %s' % str(epoch))
        print('Blocks Left: %s' % str(resp['lock_block'] - self.get_latest_block()))
        print('bull odds: %s | Kelly: %s' % (str(resp['total_amount'] / resp['bull_amount']), str(kelly[0])))
        print('bear odds: %s | Kelly: %s' % (str(resp['total_amount'] / resp['bear_amount']), str(kelly[1])))

    def kelly_calculator(self, resp, half_kelly=True):
        win_probability = self.win_probability
        try:
            bear_odds = float(resp['total_amount'] / resp['bear_amount'])
            bull_odds = float(resp['total_amount'] / resp['bull_amount'])
            bull_kelly = win_probability - ((1 - win_probability) / (bull_odds - 1))
            bear_kelly = win_probability - ((1 - win_probability) / (bear_odds - 1))
            if half_kelly:
                bull_kelly /= 2
                bear_kelly /= 2
            return tuple([bull_kelly, bear_kelly])
        # Return negative value if divide by zero
        except ZeroDivisionError:
            return tuple([-1, -1])

    def cross_chain_price(self):
        binance_price = float(binance_client.get_last_price('BNBUSDT')['price'])
        chainlink_price = float(self.cl.latest_round_data()['answer'])
        return tuple([binance_price, chainlink_price])

    def round_trigger(self, resp):
        """
        Will trigger trading when 20 block away from close
        if close block - current block < 5 then will not execute
        """
        paused = self.paused()
        current_block = self.get_latest_block()

        block_requirement = self.blocks_away >= (resp['lock_block'] - current_block) >= self.execution_block

        balance_requirement = self.balance >= self.min_balance

        return block_requirement and not paused and balance_requirement

    # Calculate odds prerequisite
    def odds_trigger(self, resp, epoch, direction):
        if direction is None:
            return False
        # Pool Size
        if float(resp['total_amount']) < self.min_pool_size:
            self.logger.log_message(
                'pool size: %s, min pool size %s unmet' % (str(float(resp['total_amount'])), str(self.min_pool_size)))
            return False
        try:
            odds = float(resp['total_amount'] / resp['%s_amount' % direction])
        except ZeroDivisionError:
            return False
        self.logger.log_message('%s odds for epoch %s: %s' % (direction, str(epoch), str(round(odds, 2))))
        if odds >= self.min_bet_odds:
            return True
        else:
            return False

    # bet Size as percentage
    def bet_sizing(self, direction, resp, kelly=False):
        if kelly:
            size = self.kelly_calculator(resp=resp)
            if direction == 'bull':
                return size[0]
            elif direction == 'bear':
                return size[1]
        else:
            return self.default_bet_size

    def bet_trigger(self):
        price_tuple = self.cross_chain_price()
        premium = (price_tuple[0] - price_tuple[1]) / price_tuple[1]
        logger.Logger.log_message('bet trigger: premium: %s, binance: %s, chainlink: %s' %
                                  (str('{0:.4%}'.format(premium)), str(price_tuple[0]), str(price_tuple[1])))
        self.status_logger.log_info('~'.join(['premium', str(premium)]))
        if abs(premium) > self.bet_threshold:
            return 'bull' if premium > 0 else 'bear'
        else:
            return None

    def start(self):
        epoch = self.current_epoch()
        placed = False
        while True:
            try:
                # Check played current round or not
                current_epoch = self.current_epoch()
                if current_epoch > epoch:
                    self.logger.log_message('=== entering round: %s ===' % str(current_epoch))
                    placed = False
                    if self.claim:
                        self.claim_round(current_epoch-2)
                    epoch = current_epoch
                    self.status_logger.log_info('~'.join(['epoch', str(epoch)]))
                resp = self.round_details(epoch=current_epoch)
                if self.round_trigger(resp=resp):
                    if not placed:
                        # Conditions to trade
                        direction = self.bet_trigger()
                        odds_requirement = self.odds_trigger(epoch=current_epoch, direction=direction, resp=resp)
                        if direction is not None and odds_requirement:
                            # print(self.bet_sizing(direction=direction, resp=resp, kelly=True))
                            bet_size = 0.2
                            bet_size = min(max(bet_size, self.min_bet_size), self.max_bet_size)
                            if self.live:
                                tx_hash = self.place_bet(bet_size=bet_size, direction=direction)
                                receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)
                                if receipt['status'] == 1:
                                    self.status_logger.log_info('~'.join(['bet_receipt', str(receipt)]))
                                elif receipt['status'] == 0:
                                    self.status_logger.log_info('~'.join(['bet_receipt_error', str(receipt)]))
                            self.blast_prediction(direction=direction, epoch=current_epoch, bet_size=bet_size)
                            self.logger.log_info('~'.join(['bet', str(current_epoch), direction, str(bet_size)]))
                            sound.play_mario_pipe()
                            placed = True

            except Exception as e:
                self.status_logger.log_warning('~'.join(['error', str(e)]))
                self.logger.log_message('error occurred: %s ' % str(e))
            time.sleep(0.5)

    def update_balance(self):
        while True:
            try:
                self.balance = self.get_balance()
            except:
                pass
            time.sleep(3600)

    def claim_round(self, epoch):
        try:
            round_details = self.round_details(epoch)
            diff = float(round_details['close_price']) - float(round_details['lock_price'])
            result = 'bull' if diff > 0 else 'bear'

            df = logger.all_logs_parser('pancake_bnb_prediction.log',
                                        ['datetime', 'type', 'level', 'action', 'epoch', 'direction', 'bet_size'])
            filtered_df = df[df['epoch'] == epoch].reset_index(drop=True)
            bet_direction = filtered_df['direction'][0]

            win = result == bet_direction
            self.logger.log_message('epoch: %s, win: %s' % (str(epoch), str(win)))
            if win:
                tx_hash = self.claim_rewards(epoch=epoch)
                receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)
                if receipt['status'] == 1:
                    tg_message_bot.tg_send(
                        ':party_popper: claimed: epoch: %s, receipt: %s' % (str(epoch), str(tx_hash)), with_emoji=True)
                    sound.play_mario_coin()
                elif receipt['status'] == 0:
                    pass
        except Exception as e:
            self.logger.log_message('epoch: %s, not participated, exception: %s' % (str(epoch), str(e)))

    # Send bet details to telegram
    def blast_prediction(self, direction, epoch, bet_size):
        if self.live:
            msg = ':tophat: Project Prophet (PROD) \n\n'
        else:
            msg = ':tophat: Project Prophet (TEST) \n\n'
        msg += ':globe_with_meridians: Current Epoch: %s' % str(epoch)
        if direction == 'bull':
            msg += '\n:crystal_ball: Prediction: Bull :cow:'
        elif direction == 'bear':
            msg += '\n:crystal_ball: Prediction: Bear :bear:'
        msg += '\n:money_bag: Bet Size: %s' % str(bet_size)

        tg_message_bot.tg_send(msg, with_emoji=True)

    def pcs_prediction_status(self, heartbeat=3600):
        while True:
            msg = ''
            if self.paused():
                msg += ':pancakes: PCS Prediction Status \n\nPCS is paused :red_circle: '
            else:
                msg += ':pancakes: PCS Prediction Status \n\nPCS is alive :green_circle: '
            details = self.round_details(self.current_epoch())
            msg += '\n\n:globe_with_meridians: Current Epoch: %s' % str(details['epoch'])
            msg += '\n:locked: Lock Block: %s' % str(details['lock_block'])
            msg += '\n:timer_clock: Close Block: %s' % str(details['end_block'])
            msg += '\n\n :large_orange_diamond: BSC Block: %s' % str(self.get_latest_block())
            tg_message_bot.tg_send(msg, with_emoji=True, disable_notification=True)

            time.sleep(heartbeat)


def result_analysis(engine: PancakePrediction):
    df = logger.all_logs_parser('pancake_bnb_prediction.log',
                                ['datetime', 'type', 'level', 'action', 'epoch', 'direction', 'bet_size'])
    df_detail = []
    for row in df.iterrows():
        epoch = row[1]['epoch']
        detail = dict(engine.round_details(epoch))
        detail_dict = {**detail, **dict(row[1])}
        detail_dict['result'] = 'bull' if detail_dict['close_price'] > detail_dict['lock_price'] else 'bear'

        detail_dict['bull_odds'] = detail_dict['total_amount'] / detail_dict['bull_amount']
        detail_dict['bear_odds'] = detail_dict['total_amount'] / detail_dict['bear_amount']

        df_detail.append(detail_dict)

    df = pd.DataFrame(df_detail)
    df = df[df['oracle_called']]
    df['win'] = df['direction'] == df['result']

    return df


class BookKeeper:
    def __init__(self, config='pancake_book.ini'):
        self.config_file = config_parser.parse(config)
        self.web3_client = ContractConnectivity(abi_name='pancake_bnb_prediction.abi',
                                                address=address_dict['pancake_bnb_prediction_address'])

    # show initial committed capital
    def show_capital(self, blast=False):
        msg = '=== Initial Capital Contribution ==='
        pool = 0
        for item in list(self.config_file['capital'].keys()):
            pool += float(self.config_file['capital'][item])
        for item in list(self.config_file['capital'].keys()):
            msg += '\n%s: %s BNB | Allocation: %s' % (item, str(round(float(self.config_file['capital'][item]), 4)),
                                                      str('{0:.4%}'.format(
                                                          float(self.config_file['capital'][item]) / pool)))
        if blast:
            tg_message_bot.tg_send(msg)
        return msg

    # show account pnl
    def account_pnl(self, blast=False):
        msg = '=== PnL Status (balance subject to withdrawal fees) ==='
        wallet_balance = self.web3_client.get_balance()
        pool = 0
        for item in list(self.config_file['capital'].keys()):
            if item != 'unallocated':
                pool += float(self.config_file['capital'][item])
        for item in list(self.config_file['capital'].keys()):
            if item != 'unallocated':
                user_balance = (wallet_balance - float(self.config_file['capital']['unallocated'])) * float(
                    self.config_file['capital'][item]) / pool
                msg += '\n%s: Balance: %s BNB | PnL: %s' % (item, str(round(user_balance, 4)), '{0:.4%}'.format(float(
                    (user_balance - float(self.config_file['capital'][item])) / float(
                        self.config_file['capital'][item]))))
        if blast:
            tg_message_bot.tg_send(msg)
        return msg

    def eod_recap(self):
        pass


if __name__ == '__main__':
    pp = PancakePrediction(abi_name='pancake_bnb_prediction.abi',
                           config='pancake_bnb_prediction.ini',
                           address=address_dict['pancake_bnb_prediction_address'],
                           logging=True,
                           live=True,
                           claim=True)

    _thread.start_new_thread(pp.start, ())
    _thread.start_new_thread(pp.update_balance, ())
