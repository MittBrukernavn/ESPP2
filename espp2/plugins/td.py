import csv
import io
import codecs
from decimal import Decimal
import dateutil.parser as dt
from pydantic import parse_obj_as
from espp2.fmv import FMV
from espp2.datamodels import Transactions, Entry, EntryTypeEnum, Amount

def fixup_date(datestr):
    '''Fixup date'''
    d =  dt.parse(datestr)
    return d.strftime('%Y-%m-%d')

currency_converter = FMV()
def fixup_price(datestr, currency, pricestr, change_sign=False):
    '''Fixup price.'''
    price = Decimal(pricestr)
    # price = Decimal(pricestr.replace('$', '').replace(',', ''))
    if change_sign:
        price = price * -1
    exchange_rate = currency_converter.get_currency(currency, datestr)
    return {'currency': currency, "value": price, 'nok_exchange_rate': exchange_rate, 'nok_value': price * exchange_rate }

def fixup_number(numberstr):
    '''Convert string to number.'''
    try:
        return Decimal(numberstr)
    except ValueError:
        return ""

def td_csv_import(fd):
    '''Parse TD Ameritrade CSV file.'''
    print('TD CSV Import', type(fd))
    data = []

    # Fastapi passes in binary file and CLI passes in a TextIOWrapper
    if isinstance(fd, io.TextIOWrapper):
        reader = csv.reader(fd)
    else:
        reader = csv.reader(codecs.iterdecode(fd,'utf-8'))

    header = next(reader)
    assert header == ['DATE', 'TRANSACTION ID', 'DESCRIPTION', 'QUANTITY', 'SYMBOL', 'PRICE', 'COMMISSION', 'AMOUNT', 'REG FEE', 'SHORT-TERM RDM FEE', 'FUND REDEMPTION FEE', ' DEFERRED SALES CHARGE']
    field = lambda x: header.index(x)
    data = []
    try:
        while True:
            row = next(reader)
            if row[0] == '***END OF FILE***':
                continue
            if row[0] == 'DATE':
                continue
            data.append({header[v].upper(): k for v, k in enumerate(row)})
    except StopIteration:
        pass
    return data

def action_to_type(value):
    if value.startswith('Bought') or value.startswith('TRANSFER OF SECURITY'):
        return 'BUY'
    if value.startswith('Sold'):
        return 'SELL'
    if value.startswith('ORDINARY DIVIDEND'):
        return 'DIVIDEND'
    if value.startswith('QUALIFIED DIVIDEND'):
        return 'DIVIDEND'
    if value.startswith('W-8 WITHHOLDING'):
        return 'TAX'
    if value.startswith('BACKUP WITHHOLDING'):
        return 'TAX'
    if value.startswith('CLIENT REQUESTED ELECTRONIC FUNDING DISBURSEMENT'):
        return 'WIRE'
    if value.startswith('FREE BALANCE INTEREST'):
        return 'INTEREST'
    if value.startswith('REBATE'):
        return 'REBATE'
    if value.startswith('WIRE INCOMING'):
        return 'DEPOSIT'
    if value.startswith('OFF-CYCLE INTEREST'):
        return 'INTEREST'
    if value.startswith('WIRE OUTGOING'):
        return 'WIRE'
    if value.startswith('DISBURSEMENT'):
        return None
    raise Exception(f'Unknown transaction entry {value}')

def read(raw_data, logger):
    '''Main entry point of plugin. Return normalized Python data structure.'''

    key_conv = {'DATE': 'date',
                'SYMBOL': 'symbol',
                'QUANTITY': 'qty',
                'PRICE': 'price',
                'COMMISSION': 'fee',
                'AMOUNT': 'amount',
                'DESCRIPTION': 'type',
                'TRANSACTION ID': 'transaction_id'
                }

    pricefields = ['amount', 'fee', 'price']
    numberfields = ['qty']

    csv_data = td_csv_import(raw_data)
    trans = []
    for e in csv_data:
        r : dict
        r = {}
        action = e['DESCRIPTION']
        d = dt.parse(e['DATE'])
        if action.startswith('Bought'):
            # {'DATE': '05/03/2017', 'TRANSACTION ID': '16801444321',
            #  'DESCRIPTION': 'Bought 10 SPY @ 237.9', 'QUANTITY': '10',
            #  'SYMBOL': 'SPY', 'PRICE': '237.90', 'COMMISSION': '6.95',
            #  'AMOUNT': '-2385.95', 'REG FEE': '', 'SHORT-TERM RDM FEE': '',
            #  'FUND REDEMPTION FEE': '', ' DEFERRED SALES CHARGE': ''}
            t = EntryTypeEnum.BUY

            qty = Decimal(e['QUANTITY'])
            price = fixup_price(d, "USD", e['PRICE'])
            if e['COMMISSION'] != '':
                fee = fixup_price(d, "USD", e['COMMISSION'])
            else:
                fee = None
            r = {'type': t, 'date': d, 'qty': qty, 'symbol': e['SYMBOL'],
                 'description': action,
                 'purchase_price': price, 'fee': fee}

        elif action.startswith('TRANSFER OF SECURITY OR OPTION IN'):
            t = EntryTypeEnum.DEPOSIT
            qty = Decimal(e['QUANTITY'])
            if e['PRICE'] != '':
                price = fixup_price(d, "USD", e['PRICE'])
            else:
                price = fixup_price(d, "USD", "0")
            if e['COMMISSION'] != '':
                fee = fixup_price(d, "USD", e['COMMISSION'])
            else:
                fee = None
            r = {'type': t, 'date': d, 'qty': qty, 'symbol': e['SYMBOL'],
                 'description': action,
                 'purchase_price': price, 'fee': fee}

        elif action.startswith('Sold'):
            # {'DATE': '09/21/2021', 'TRANSACTION ID': '37504205925', 
            # 'DESCRIPTION': 'Sold 130 SPY @ 433.1', 'QUANTITY': '130',
            #  'SYMBOL': 'SPY', 'PRICE': '433.10', 'COMMISSION': '0.00', 
            # 'AMOUNT': '56302.69', 'REG FEE': '0.31', 'SHORT-TERM RDM FEE': '', 
            # 'FUND REDEMPTION FEE': '', ' DEFERRED SALES CHARGE': ''}
            t = EntryTypeEnum.SELL
            qty = -Decimal(e['QUANTITY'])
            amount = fixup_price(d, "USD", e['AMOUNT'])
            r = {'type': t, 'date': d, 'qty': qty, 'amount': amount,
                 'symbol': e['SYMBOL'], 'description': action}

        elif action.startswith('ORDINARY DIVIDEND') or action.startswith('QUALIFIED DIVIDEND'):
            # {'DATE': '01/31/2017', 'TRANSACTION ID': '16284920138',
            #  'DESCRIPTION': 'ORDINARY DIVIDEND (SPY)',
            #  'QUANTITY': '', 'SYMBOL': 'SPY', 'PRICE': '',
            #  'COMMISSION': '', 'AMOUNT': '398.68', 'REG FEE': '',
            #  'SHORT-TERM RDM FEE': '', 'FUND REDEMPTION FEE': '', 
            # ' DEFERRED SALES CHARGE': ''}

            t = EntryTypeEnum.DIVIDEND

            amount = fixup_price(d, "USD", e['AMOUNT'])
            r = {'type': t, 'date': d, 'amount': amount, 'symbol': e['SYMBOL']}

        elif action.startswith('W-8 WITHHOLDING') or action.startswith('BACKUP WITHHOLDING'):
            t = EntryTypeEnum.TAX
            amount = fixup_price(d, "USD", e['AMOUNT'])
            r = {'type': t, 'date': d, 'amount': amount,
                 'symbol': e['SYMBOL'], 'description': action}

        elif action.startswith('CLIENT REQUESTED ELECTRONIC FUNDING DISBURSEMENT') or action.startswith('WIRE OUTGOING'):
            # {'DATE': '09/23/2021', 'TRANSACTION ID': '37555188264',
            #  'DESCRIPTION': 'CLIENT REQUESTED ELECTRONIC FUNDING DISBURSEMENT (FUNDS NOW)', 
            # 'QUANTITY': '', 'SYMBOL': '', 'PRICE': '', 'COMMISSION': '',
            #  'AMOUNT': '-56330.26', 'REG FEE': '',
            #  'SHORT-TERM RDM FEE': '', 'FUND REDEMPTION FEE': '', 
            # ' DEFERRED SALES CHARGE': ''}

            t = EntryTypeEnum.WIRE
            amount = fixup_price(d, "USD", e['AMOUNT'])
            r = {'type': t, 'date': d, 'amount': amount,
                 'description': action}

        elif action.startswith('FREE BALANCE INTEREST'):
            # return 'INTEREST'
            continue
        elif action.startswith('REBATE'):
            # return 'REBATE'
            continue
        elif action.startswith('WIRE INCOMING'):
            # return 'DEPOSIT'
            continue
        elif action.startswith('OFF-CYCLE INTEREST'):
            # return 'INTEREST'
            continue
        elif action.startswith('DISBURSEMENT'):
            # return None
            continue
        else:
            raise Exception(f'Unknown transaction entry {value}')

        trans.append(parse_obj_as(Entry, r))

        # if e['Activity'] == 'Opening Balance' or e['Activity'].startswith('Release'):
        #     # Seems like a BUY entry
        #     t = EntryTypeEnum.DEPOSIT
        #     qty = Decimal(e['Number of Shares'])
        #     book_value, currency = morgan_price(e['Book Value'])
        #     purchase_price = fixup_price2(d, currency, book_value / qty)
        #     r = {'type': t, 'date': d, 'qty': qty, 'symbol': symbol,
        #          'description': e['Activity'],
        #          'purchase_price': purchase_price, }
    t = Transactions(transactions=trans)
    # print(t.json(indent=4))
    return t

    # newlist = []
    # for csv_item in csv_data:
    #     newv = {}
    #     action = action_to_type(csv_item['DESCRIPTION'])
    #     if not action:
    #         continue
    #     for k, data_item in csv_item.items():
    #         newkey = key_conv.get(k, k)
    #         if not data_item:
    #             continue
    #         if newkey == 'date':
    #             newv[newkey] = fixup_date(data_item)
    #         # elif newkey in pricefields:
    #         #     newv[newkey] = fixup_price(data_item)
    #         elif newkey in numberfields:
    #             newv[newkey] = fixup_number(data_item)
    #         elif newkey == 'type':
    #             newv[newkey] = action_to_type(data_item)
    #             newv['description'] = data_item
    #         else:
    #             newv[newkey] = data_item

    #     for pricefield in pricefields:
    #         if pricefield in newv:
    #             if action == 'SELL' and pricefield == 'fee':
    #                 newv[pricefield] = fixup_price(
    #                     newv['date'], 'USD', newv[pricefield], change_sign=True)
    #             else:
    #                 newv[pricefield] = fixup_price(
    #                     newv['date'], 'USD', newv[pricefield])
    #     if action == 'SELL':
    #         newv['qty'] = newv['qty'] * -1
    #     elif action == 'BUY':
    #         print('NEWV', newv)
    #         try:
    #             newv['purchase_price'] = newv.pop('price')
    #         except KeyError:
    #             newv['purchase_price'] = Decimal('0')
    #         # newv.pop('amount')

    #     newlist.append(newv)

    # return sorted(newlist, key=lambda d: d['date'])
