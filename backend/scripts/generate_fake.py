#!/usr/bin/env python3
"""Generate a fake beancount ledger for demo/screenshot purposes."""

import random
import uuid
import hashlib
from datetime import date, timedelta
from pathlib import Path

random.seed(42)

def fake_id():
    return str(uuid.uuid4())

def fake_hash():
    return hashlib.sha256(uuid.uuid4().bytes).hexdigest()

def txn(dt, payee, narration, postings, source_account=None, external_id=None):
    """Generate a transaction string.

    Each posting is a tuple: (account, amount, currency)
    or with total price:      (account, amount, currency, total_price_amt, total_price_ccy)
    """
    lines = []
    lines.append(f'{dt} * "{payee}" "{narration}"')
    lines.append(f'  id: "{fake_id()}"')
    lines.append(f'  content_hash: "{fake_hash()}"')
    if source_account:
        lines.append(f'  source_account: "{source_account}"')
    if external_id:
        lines.append(f'  external_id: "{external_id}"')
        lines.append(f'  external_id_type: "OFX"')
    for posting in postings:
        acct, amt, ccy = posting[0], posting[1], posting[2]
        price_str = ""
        if len(posting) >= 5:
            price_str = f" @@ {posting[3]:.2f} {posting[4]}"
        lines.append(f'  {acct:<55s} {amt:.2f} {ccy}{price_str}')
    return "\n".join(lines)

# --- Account definitions ---
ACCOUNTS = """
2015-01-01 open Assets:Liquid:Savings:PinnacleBank:NRE              INR
2015-01-01 open Assets:Liquid:Savings:PinnacleBank:NRO              INR
2015-01-01 open Assets:Transfer

2015-10-01 open Assets:Liquid:Checking:WestCoastBank                USD
2015-10-01 open Assets:Liquid:Savings:WestCoastBank                 USD
2015-10-01 open Assets:Liquid:Checking:ValleyCU                     USD
2015-10-01 open Assets:Liquid:Savings:ValleyCU                      USD

2015-12-31 open Assets:Receivable:Work                              USD
2015-12-31 open Assets:Receivable:Personal                          USD
2015-12-31 open Assets:Investments:Bonds:TreasuryDirect:IBonds      USD
2015-12-31 open Assets:Investments:Brokerage:Vanguard:Individual    USD
2015-12-31 open Assets:Investments:Brokerage:Wealthsimple           USD

2015-12-31 open Liabilities:CreditCards:Citi:DoubleCash             USD
2015-12-31 open Liabilities:CreditCards:Amex:GoldRewards            USD
2015-12-31 open Liabilities:CreditCards:WestCoastBank               USD
2015-12-31 open Liabilities:CreditCards:CapitalFirst                USD

2015-12-31 open Equity:OpeningBalances                              USD,INR
2015-12-31 open Equity:RetainedEarnings                             USD
2015-12-31 open Equity:CurrentEarnings                              USD
2015-12-31 open Equity:Conversions                                  USD
2015-12-31 open Equity:Taxes:Federal                                USD,INR
2015-12-31 open Equity:Taxes:State                                  USD

2015-12-31 open Income:Salary                                       USD
2015-12-31 open Income:Bonus                                        USD
2015-12-31 open Income:Other                                        USD,INR
2015-12-31 open Income:Rewards                                      USD
2015-12-31 open Income:Interest:Savings:WestCoastBank               USD
2015-12-31 open Income:Interest:Savings:ValleyCU                    USD
2015-12-31 open Income:Interest:Checking:WestCoastBank              USD
2015-12-31 open Income:Interest:Checking:ValleyCU                   USD
2015-12-31 open Income:Interest:Savings:PinnacleBank:NRE            INR
2015-12-31 open Income:Interest:Savings:PinnacleBank:NRO            INR

2015-12-31 open Expenses:AutoInsurance
2015-12-31 open Expenses:Internet
2015-12-31 open Expenses:Phone
2015-12-31 open Expenses:CloudServices
2015-12-31 open Expenses:HouseCleaning
2015-12-31 open Expenses:HouseRent
2015-12-31 open Expenses:Groceries
2015-12-31 open Expenses:Gas
2015-12-31 open Expenses:EatingOut
2015-12-31 open Expenses:Entertainment
2015-12-31 open Expenses:Hobbies
2015-12-31 open Expenses:PersonalItems
2015-12-31 open Expenses:Healthcare
2015-12-31 open Expenses:HouseholdItems
2015-12-31 open Expenses:Travel
2015-12-31 open Expenses:Cash
2015-12-31 open Expenses:Donations
2015-12-31 open Expenses:Gifts
2015-12-31 open Expenses:Miscellaneous
2015-12-31 open Expenses:Utilities
2015-12-31 open Expenses:Car
2015-12-31 open Expenses:Car:Lease
2015-12-31 open Expenses:Gadgets
2015-12-31 open Expenses:Parking
2015-12-31 open Expenses:Grooming
2015-12-31 open Expenses:School
2015-12-31 open Expenses:Childcare
2015-12-31 open Expenses:Insurance:Health                           USD
2015-12-31 open Expenses:Insurance:Dental                           USD
2015-12-31 open Expenses:Insurance:Vision                           USD
2015-12-31 open Expenses:Fees:Banking
2015-12-31 open Expenses:Fees:ATM
2015-12-31 open Expenses:Fees:CreditCard
2015-12-31 open Expenses:Unknown

2019-04-22 open Assets:Liquid:Savings:HighYield:HYSA                USD
2019-04-24 open Income:Interest:Savings:HighYield:HYSA              USD
2020-01-01 open Income:Interest:TermDeposits                        USD,INR
2023-01-14 open Assets:Investments:TermDeposits:ValleyCU:CD1        USD
2023-01-14 open Assets:Investments:TermDeposits:ValleyCU:CD2        USD
2023-01-24 open Assets:Investments:TermDeposits:WestCoastBank:CD001 USD
2023-10-14 open Assets:Investments:TermDeposits:ValleyCU:CD3        USD
2024-08-14 open Assets:Investments:TermDeposits:ValleyCU:CD4        USD
2026-01-29 open Assets:Investments:TermDeposits:PinnacleBank:NRE-FD INR
2026-02-03 open Assets:Investments:TermDeposits:PinnacleBank:NRO-FD INR
2026-02-23 open Income:Business:Consulting                          INR
2026-03-01 open Expenses:Business                                   INR
2026-03-06 open Assets:Liquid:Savings:NationalBank                  INR
2026-03-06 open Assets:Liquid:Checking:NationalBank                 INR
2026-03-06 open Income:Interest:Savings:NationalBank                INR
""".strip()

# --- Pad and balance directives ---
PAD_BALANCE = """
2018-12-30 pad Assets:Liquid:Savings:PinnacleBank:NRE Equity:OpeningBalances
2018-12-31 balance Assets:Liquid:Savings:PinnacleBank:NRE              485000.00 INR

2018-12-30 pad Assets:Liquid:Checking:ValleyCU Equity:OpeningBalances
2018-12-30 pad Assets:Liquid:Savings:ValleyCU Equity:OpeningBalances
2018-12-30 pad Assets:Liquid:Checking:WestCoastBank Equity:OpeningBalances
2018-12-31 balance Assets:Liquid:Checking:ValleyCU                     18500.25 USD
2018-12-31 balance Assets:Liquid:Savings:ValleyCU                      550000.00 USD
2018-12-31 balance Assets:Liquid:Checking:WestCoastBank                28750.60 USD

2018-12-31 pad Assets:Liquid:Savings:WestCoastBank Equity:OpeningBalances
2019-01-01 balance Assets:Liquid:Savings:WestCoastBank                 125000.00 USD

2019-04-24 balance Assets:Liquid:Savings:HighYield:HYSA                0.00 USD
""".strip()

# --- Transaction generators ---

# USD grocery stores
GROCERY_STORES = [
    ("Whole Foods Market", "Groceries"),
    ("Trader Joe's", "Weekly groceries"),
    ("Safeway", "Groceries"),
    ("Costco Wholesale", "Bulk groceries"),
    ("Ranch 99 Market", "Asian groceries"),
    ("Sprouts Farmers Market", "Organic produce"),
    ("Target", "Groceries and household"),
    ("Lucky Supermarkets", "Groceries"),
]

RESTAURANTS = [
    ("Chipotle Mexican Grill", "Lunch"),
    ("Panda Express", "Quick dinner"),
    ("DoorDash", "Food delivery"),
    ("Uber Eats", "Dinner delivery"),
    ("In-N-Out Burger", "Burgers"),
    ("Starbucks", "Coffee"),
    ("Philz Coffee", "Morning coffee"),
    ("Thai Basil Restaurant", "Thai dinner"),
    ("Sushi Kuni", "Sushi dinner"),
    ("Mediterranean Grill", "Lunch"),
    ("Pizza My Heart", "Pizza night"),
    ("Blue Bottle Coffee", "Coffee"),
    ("The Boiling Crab", "Seafood dinner"),
    ("Din Tai Fung", "Dim sum"),
]

CLOUD_SERVICES = [
    ("Amazon Web Services", 12.50),
    ("Google Cloud Platform", 8.25),
    ("DigitalOcean", 6.00),
    ("Hetzner Cloud", 5.50),
    ("GitHub Pro", 4.00),
    ("Namecheap", 12.86),
    ("1Password", 4.99),
    ("Notion", 8.00),
    ("Fastmail", 5.00),
]

ENTERTAINMENT = [
    ("Netflix", 15.99),
    ("Spotify Premium", 10.99),
    ("YouTube Premium", 13.99),
    ("Apple TV+", 6.99),
    ("AMC Theatres", None),
    ("Kindle Books", None),
    ("Steam", None),
]

# INR merchants
INR_GROCERY = [
    "Big Bazaar",
    "DMart",
    "Reliance Fresh",
    "Star Bazaar",
    "More Supermarket",
]

INR_RESTAURANTS = [
    ("Shree Krishna Veg", "Thali"),
    ("Mainland China", "Chinese dinner"),
    ("Barbeque Nation", "BBQ dinner"),
    ("Hotel Sagar", "Lunch"),
    ("Vaishali Restaurant", "South Indian"),
    ("Cafe Goodluck", "Breakfast"),
    ("German Bakery", "Coffee and pastries"),
]

# Credit card rotation for USD
USD_CC = [
    "Liabilities:CreditCards:Citi:DoubleCash",
    "Liabilities:CreditCards:Amex:GoldRewards",
]

# Checking accounts for direct debits
USD_CHECKING = [
    "Assets:Liquid:Checking:WestCoastBank",
    "Assets:Liquid:Checking:ValleyCU",
]

def gen_usd_grocery(dt):
    store, narr = random.choice(GROCERY_STORES)
    amt = round(random.uniform(8, 180), 2)
    cc = random.choice(USD_CC)
    return txn(dt, store, narr, [
        (cc, -amt, "USD"),
        ("Expenses:Groceries", amt, "USD"),
    ], source_account=cc)

def gen_usd_restaurant(dt):
    rest, narr = random.choice(RESTAURANTS)
    amt = round(random.uniform(8, 95), 2)
    cc = random.choice(USD_CC)
    return txn(dt, rest, narr, [
        (cc, -amt, "USD"),
        ("Expenses:EatingOut", amt, "USD"),
    ], source_account=cc)

def gen_usd_gas(dt):
    stations = ["Chevron", "Shell", "Arco", "Costco Gas"]
    station = random.choice(stations)
    amt = round(random.uniform(35, 75), 2)
    cc = random.choice(USD_CC)
    return txn(dt, station, "Gas", [
        (cc, -amt, "USD"),
        ("Expenses:Gas", amt, "USD"),
    ], source_account=cc)

def gen_salary(dt):
    """Biweekly salary deposit split across two accounts."""
    txns = []
    # Main paycheck to WestCoastBank
    main_amt = round(random.uniform(5800, 6400), 2)
    txns.append(txn(dt, "Globex Corporation", "Payroll direct deposit", [
        ("Assets:Liquid:Checking:WestCoastBank", main_amt, "USD"),
        ("Income:Salary", -main_amt, "USD"),
    ], source_account="Assets:Liquid:Checking:WestCoastBank"))
    # Secondary split to ValleyCU
    split_amt = round(random.uniform(2800, 3200), 2)
    txns.append(txn(dt, "Globex Corporation", "Payroll direct deposit", [
        ("Assets:Liquid:Checking:ValleyCU", split_amt, "USD"),
        ("Income:Salary", -split_amt, "USD"),
    ], source_account="Assets:Liquid:Checking:ValleyCU"))
    return txns

def gen_rent(dt):
    year = dt.year
    if year <= 2020:
        amt = 3200.00
    elif year <= 2022:
        amt = 3500.00
    elif year <= 2024:
        amt = 3800.00
    else:
        amt = 4200.00
    return txn(dt, f"Check {random.randint(200,999)}", "Monthly rent", [
        ("Expenses:HouseRent", amt, "USD"),
        ("Assets:Liquid:Checking:WestCoastBank", -amt, "USD"),
    ], source_account="Assets:Liquid:Checking:WestCoastBank")

def gen_utilities(dt):
    txns = []
    # Electric
    elec = round(random.uniform(80, 350), 2)
    txns.append(txn(dt, "Pacific Gas & Electric", "", [
        ("Assets:Liquid:Checking:ValleyCU", -elec, "USD"),
        ("Expenses:Utilities", elec, "USD"),
    ], source_account="Assets:Liquid:Checking:ValleyCU"))
    # Water (every other month)
    if dt.month % 2 == 0:
        water = round(random.uniform(60, 180), 2)
        txns.append(txn(dt + timedelta(days=2), "Bay Area Water Company", "", [
            ("Liabilities:CreditCards:Citi:DoubleCash", -water, "USD"),
            ("Expenses:Utilities", water, "USD"),
        ], source_account="Liabilities:CreditCards:Citi:DoubleCash"))
    return txns

def gen_internet(dt):
    return txn(dt, "Comcast Xfinity", "Internet", [
        ("Assets:Liquid:Checking:ValleyCU", -89.99, "USD"),
        ("Expenses:Internet", 89.99, "USD"),
    ], source_account="Assets:Liquid:Checking:ValleyCU")

def gen_phone(dt):
    return txn(dt, "T-Mobile", "Phone plan", [
        ("Assets:Liquid:Checking:ValleyCU", -75.00, "USD"),
        ("Expenses:Phone", 75.00, "USD"),
    ], source_account="Assets:Liquid:Checking:ValleyCU")

def gen_cloud_service(dt):
    svc, amt = random.choice(CLOUD_SERVICES)
    cc = random.choice(USD_CC)
    return txn(dt, svc, "", [
        (cc, -amt, "USD"),
        ("Expenses:CloudServices", amt, "USD"),
    ], source_account=cc)

def gen_entertainment(dt):
    svc, amt = random.choice(ENTERTAINMENT)
    if amt is None:
        amt = round(random.uniform(8, 45), 2)
    cc = random.choice(USD_CC)
    return txn(dt, svc, "", [
        (cc, -amt, "USD"),
        ("Expenses:Entertainment", amt, "USD"),
    ], source_account=cc)

def gen_healthcare(dt):
    providers = [
        ("Kaiser Permanente", "Copay"),
        ("CVS Pharmacy", "Prescription"),
        ("Walgreens", "Medicine"),
        ("Dr. Smith Medical Group", "Office visit"),
    ]
    prov, narr = random.choice(providers)
    amt = round(random.uniform(10, 250), 2)
    cc = random.choice(USD_CC)
    return txn(dt, prov, narr, [
        (cc, -amt, "USD"),
        ("Expenses:Healthcare", amt, "USD"),
    ], source_account=cc)

def gen_travel(dt):
    items = [
        ("United Airlines", "Flight"),
        ("Southwest Airlines", "Flight"),
        ("Lyft", "Ride"),
        ("Uber", "Ride"),
        ("Marriott Hotels", "Hotel stay"),
        ("Airbnb", "Vacation rental"),
        ("Hilton", "Hotel"),
    ]
    item, narr = random.choice(items)
    amt = round(random.uniform(15, 500), 2)
    cc = "Liabilities:CreditCards:Citi:DoubleCash"
    return txn(dt, item, narr, [
        (cc, -amt, "USD"),
        ("Expenses:Travel", amt, "USD"),
    ], source_account=cc)

def gen_auto_insurance(dt):
    amt = round(random.uniform(140, 190), 2)
    return txn(dt, "GEICO", "Auto insurance premium", [
        ("Assets:Liquid:Checking:WestCoastBank", -amt, "USD"),
        ("Expenses:AutoInsurance", amt, "USD"),
    ], source_account="Assets:Liquid:Checking:WestCoastBank")

def gen_health_insurance(dt):
    txns = []
    txns.append(txn(dt, "Blue Shield of California", "Health insurance premium", [
        ("Assets:Liquid:Checking:WestCoastBank", -450.00, "USD"),
        ("Expenses:Insurance:Health", 450.00, "USD"),
    ], source_account="Assets:Liquid:Checking:WestCoastBank"))
    if dt.month % 3 == 0:
        txns.append(txn(dt + timedelta(days=1), "Delta Dental", "Dental insurance", [
            ("Assets:Liquid:Checking:WestCoastBank", -35.00, "USD"),
            ("Expenses:Insurance:Dental", 35.00, "USD"),
        ], source_account="Assets:Liquid:Checking:WestCoastBank"))
        txns.append(txn(dt + timedelta(days=1), "VSP Vision", "Vision insurance", [
            ("Assets:Liquid:Checking:WestCoastBank", -12.00, "USD"),
            ("Expenses:Insurance:Vision", 12.00, "USD"),
        ], source_account="Assets:Liquid:Checking:WestCoastBank"))
    return txns

def gen_cc_payment(dt):
    """Credit card bill payment — weighted by card usage."""
    # Main cards get most payments; secondary cards get occasional smaller payments
    all_cc = [
        ("Liabilities:CreditCards:Citi:DoubleCash", 0.4, (800, 2500)),
        ("Liabilities:CreditCards:Amex:GoldRewards", 0.4, (800, 2500)),
        ("Liabilities:CreditCards:CapitalFirst", 0.1, (200, 600)),
        ("Liabilities:CreditCards:WestCoastBank", 0.1, (100, 400)),
    ]
    r = random.random()
    cumulative = 0
    for cc, weight, (lo, hi) in all_cc:
        cumulative += weight
        if r < cumulative:
            break
    acct = random.choice(USD_CHECKING)
    amt = round(random.uniform(lo, hi), 2)
    return txn(dt, f"Payment to {cc.split(':')[-1]}", "Credit card payment", [
        (cc, amt, "USD"),
        (acct, -amt, "USD"),
    ], source_account=cc)

def gen_transfer_to_hysa(dt):
    amt = round(random.uniform(1000, 4000), 2)
    return txn(dt, "Transfer to High Yield Savings", "", [
        ("Assets:Liquid:Savings:HighYield:HYSA", amt, "USD"),
        ("Assets:Liquid:Checking:ValleyCU", -amt, "USD"),
    ], source_account="Assets:Liquid:Savings:HighYield:HYSA")

def gen_hysa_interest(dt):
    amt = round(random.uniform(50, 800), 2)
    return txn(dt, "Interest Earned", "", [
        ("Assets:Liquid:Savings:HighYield:HYSA", amt, "USD"),
        ("Income:Interest:Savings:HighYield:HYSA", -amt, "USD"),
    ], source_account="Assets:Liquid:Savings:HighYield:HYSA")

def gen_savings_interest(dt):
    txns = []
    for bank, acct, inc in [
        ("WestCoastBank", "Assets:Liquid:Savings:WestCoastBank", "Income:Interest:Savings:WestCoastBank"),
        ("ValleyCU", "Assets:Liquid:Savings:ValleyCU", "Income:Interest:Savings:ValleyCU"),
    ]:
        amt = round(random.uniform(0.5, 15), 2)
        txns.append(txn(dt, "Interest Earned", "", [
            (acct, amt, "USD"),
            (inc, -amt, "USD"),
        ], source_account=acct))
    return txns

def gen_checking_interest(dt):
    amt = round(random.uniform(0.05, 0.50), 2)
    return txn(dt, "Interest Earned", "", [
        ("Assets:Liquid:Checking:WestCoastBank", amt, "USD"),
        ("Income:Interest:Checking:WestCoastBank", -amt, "USD"),
    ], source_account="Assets:Liquid:Checking:WestCoastBank")

def gen_bonus(dt):
    amt = round(random.uniform(15000, 35000), 2)
    return txn(dt, "Globex Corporation", "Annual performance bonus", [
        ("Assets:Liquid:Checking:WestCoastBank", amt, "USD"),
        ("Income:Bonus", -amt, "USD"),
    ], source_account="Assets:Liquid:Checking:WestCoastBank")

def gen_brokerage_transfer(dt):
    amt = round(random.uniform(1000, 3000), 2)
    acct = random.choice([
        "Assets:Investments:Brokerage:Vanguard:Individual",
        "Assets:Investments:Brokerage:Wealthsimple",
    ])
    return txn(dt, f"Transfer to {acct.split(':')[-1]}", "Investment contribution", [
        (acct, amt, "USD"),
        ("Assets:Liquid:Checking:WestCoastBank", -amt, "USD"),
    ], source_account=acct)

def gen_savings_to_checking_transfer(dt):
    """Replenish checking from HYSA periodically."""
    amt = round(random.uniform(3000, 8000), 2)
    return txn(dt, "Transfer from High Yield Savings", "Replenish checking", [
        ("Assets:Liquid:Checking:ValleyCU", amt, "USD"),
        ("Assets:Liquid:Savings:HighYield:HYSA", -amt, "USD"),
    ], source_account="Assets:Liquid:Checking:ValleyCU")

def gen_inr_inward_remittance(dt):
    """Wire transfer from US to India NRO account — two-leg cross-currency transfer."""
    inr_amt = round(random.uniform(50000, 200000), 0)
    # Approximate exchange rate (varies by year)
    rate = random.uniform(78, 86)
    usd_amt = round(inr_amt / rate, 2)
    ref = random.randint(10000000, 99999999)
    leg1 = txn(dt, f"Wire transfer to India #{ref}", "Send remittance to NRO", [
        ("Assets:Liquid:Checking:WestCoastBank", -usd_amt, "USD"),
        ("Assets:Transfer", usd_amt, "USD"),
    ], source_account="Assets:Liquid:Checking:WestCoastBank")
    leg2 = txn(dt + timedelta(days=2), f"SWIFT/INWARD/{ref}", "Remittance received in NRO", [
        ("Assets:Transfer", -usd_amt, "USD"),
        ("Assets:Liquid:Savings:PinnacleBank:NRO", inr_amt, "INR", usd_amt, "USD"),
    ], source_account="Assets:Liquid:Savings:PinnacleBank:NRO")
    return [leg1, leg2]

def gen_gadget(dt):
    items = [
        ("Apple Store", "AirPods Pro", 249.00),
        ("Apple Store", "MacBook Pro charger", 79.00),
        ("Best Buy", "Mechanical keyboard", 129.99),
        ("Amazon", "USB-C hub", 45.99),
        ("Apple Store", "iPad Air", 599.00),
        ("Best Buy", "Monitor", 349.99),
        ("Amazon", "Wireless mouse", 29.99),
        ("Apple Store", "iPhone 16 Pro", 1099.00),
        ("Apple Store", "Apple Watch Series 10", 449.00),
    ]
    store, narr, amt = random.choice(items)
    cc = "Liabilities:CreditCards:Citi:DoubleCash"
    return txn(dt, store, narr, [
        (cc, -amt, "USD"),
        ("Expenses:Gadgets", amt, "USD"),
    ], source_account=cc)

def gen_gifts(dt):
    items = [
        ("Amazon", "Birthday gift"),
        ("Target", "Gift"),
        ("Nordstrom", "Holiday gift"),
        ("Etsy", "Handmade gift"),
    ]
    store, narr = random.choice(items)
    amt = round(random.uniform(15, 150), 2)
    cc = random.choice(USD_CC)
    return txn(dt, store, narr, [
        (cc, -amt, "USD"),
        ("Expenses:Gifts", amt, "USD"),
    ], source_account=cc)

def gen_donation(dt):
    orgs = [
        "Wikipedia Foundation",
        "Red Cross",
        "Local Food Bank",
        "EFF",
        "Khan Academy",
    ]
    org = random.choice(orgs)
    amt = round(random.uniform(25, 500), 2)
    cc = random.choice(USD_CC)
    return txn(dt, org, "Donation", [
        (cc, -amt, "USD"),
        ("Expenses:Donations", amt, "USD"),
    ], source_account=cc)

def gen_personal_items(dt):
    stores = [
        ("Target", "Personal items"),
        ("Amazon", "Personal items"),
        ("CVS", "Toiletries"),
        ("Uniqlo", "Clothing"),
        ("REI", "Outdoor gear"),
    ]
    store, narr = random.choice(stores)
    amt = round(random.uniform(10, 120), 2)
    cc = random.choice(USD_CC)
    return txn(dt, store, narr, [
        (cc, -amt, "USD"),
        ("Expenses:PersonalItems", amt, "USD"),
    ], source_account=cc)

def gen_household(dt):
    stores = [
        ("Home Depot", "Home improvement"),
        ("IKEA", "Furniture"),
        ("Amazon", "Household items"),
        ("Bed Bath & Beyond", "Household items"),
        ("Target", "Household items"),
    ]
    store, narr = random.choice(stores)
    amt = round(random.uniform(15, 300), 2)
    cc = random.choice(USD_CC)
    return txn(dt, store, narr, [
        (cc, -amt, "USD"),
        ("Expenses:HouseholdItems", amt, "USD"),
    ], source_account=cc)

def gen_grooming(dt):
    amt = round(random.uniform(25, 60), 2)
    cc = random.choice(USD_CC)
    return txn(dt, "Great Clips", "Haircut", [
        (cc, -amt, "USD"),
        ("Expenses:Grooming", amt, "USD"),
    ], source_account=cc)

def gen_parking(dt):
    amt = round(random.uniform(5, 25), 2)
    cc = random.choice(USD_CC)
    return txn(dt, "ParkMobile", "Parking", [
        (cc, -amt, "USD"),
        ("Expenses:Parking", amt, "USD"),
    ], source_account=cc)

def gen_childcare(dt):
    if dt.year < 2021:
        return None
    amt = 1800.00 if dt.year <= 2023 else 2100.00
    return txn(dt, "Sunshine Montessori", "Monthly tuition", [
        ("Assets:Liquid:Checking:WestCoastBank", -amt, "USD"),
        ("Expenses:Childcare", amt, "USD"),
    ], source_account="Assets:Liquid:Checking:WestCoastBank")

def gen_car_lease(dt):
    amt = 389.00
    return txn(dt, "Honda Financial Services", "Car lease payment", [
        ("Assets:Liquid:Checking:WestCoastBank", -amt, "USD"),
        ("Expenses:Car:Lease", amt, "USD"),
    ], source_account="Assets:Liquid:Checking:WestCoastBank")

def gen_house_cleaning(dt):
    amt = round(random.uniform(140, 175), 2)
    return txn(dt, "Maria's Cleaning Service", "House cleaning", [
        ("Assets:Liquid:Checking:WestCoastBank", -amt, "USD"),
        ("Expenses:HouseCleaning", amt, "USD"),
    ], source_account="Assets:Liquid:Checking:WestCoastBank")

# --- INR transaction generators ---

def gen_inr_grocery(dt):
    store = random.choice(INR_GROCERY)
    amt = round(random.uniform(200, 5000), 0)
    acct = "Assets:Liquid:Savings:PinnacleBank:NRO"
    return txn(dt, f"UPI/P2M/{random.randint(100000000000,999999999999)}/{store}", "Groceries", [
        (acct, -amt, "INR"),
        ("Expenses:Groceries", amt, "INR"),
    ], source_account=acct)

def gen_inr_restaurant(dt):
    rest, narr = random.choice(INR_RESTAURANTS)
    amt = round(random.uniform(100, 3000), 0)
    acct = "Assets:Liquid:Savings:PinnacleBank:NRO"
    return txn(dt, f"UPI/P2M/{random.randint(100000000000,999999999999)}/{rest}", narr, [
        (acct, -amt, "INR"),
        ("Expenses:EatingOut", amt, "INR"),
    ], source_account=acct)

def gen_inr_interest(dt):
    txns = []
    for bank, acct, inc in [
        ("NRE", "Assets:Liquid:Savings:PinnacleBank:NRE", "Income:Interest:Savings:PinnacleBank:NRE"),
        ("NRO", "Assets:Liquid:Savings:PinnacleBank:NRO", "Income:Interest:Savings:PinnacleBank:NRO"),
    ]:
        amt = round(random.uniform(200, 5000), 0)
        txns.append(txn(dt, f"Interest Paid Q{(dt.month-1)//3+1} {dt.year}", "", [
            (acct, amt, "INR"),
            (inc, -amt, "INR"),
        ], source_account=acct))
    return txns

def gen_inr_cash(dt):
    amt = round(random.choice([2000, 5000, 10000]), 0)
    return txn(dt, f"ATM-CASH-PINNACLE/{random.randint(1000,9999)}/{dt.strftime('%d%m%y')}", "", [
        ("Assets:Liquid:Savings:PinnacleBank:NRE", -amt, "INR"),
        ("Expenses:Cash", amt, "INR"),
    ], source_account="Assets:Liquid:Savings:PinnacleBank:NRE")

def gen_inr_household(dt):
    items = [
        ("Wooden Street", "Furniture"),
        ("Pepperfry", "Home decor"),
        ("Urban Ladder", "Bookshelf"),
        ("Amazon India", "Household items"),
        ("Flipkart", "Appliance"),
    ]
    store, narr = random.choice(items)
    amt = round(random.uniform(500, 50000), 0)
    acct = "Assets:Liquid:Savings:PinnacleBank:NRO"
    return txn(dt, f"UPI/P2M/{random.randint(100000000000,999999999999)}/{store}", narr, [
        (acct, -amt, "INR"),
        ("Expenses:HouseholdItems", amt, "INR"),
    ], source_account=acct)

def gen_inr_donation(dt):
    amt = round(random.uniform(1000, 10000), 0)
    return txn(dt, f"NEFT/MB/{random.randint(10000000000,99999999999)}/Charitable Trust", "Donation", [
        ("Assets:Liquid:Savings:PinnacleBank:NRE", -amt, "INR"),
        ("Expenses:Donations", amt, "INR"),
    ], source_account="Assets:Liquid:Savings:PinnacleBank:NRE")

def gen_inr_entertainment(dt):
    items = [
        ("Netflix India", 649),
        ("BookMyShow", None),
        ("Hotstar", 299),
        ("Amazon Prime", 179),
    ]
    svc, amt = random.choice(items)
    if amt is None:
        amt = round(random.uniform(200, 1500), 0)
    acct = "Assets:Liquid:Savings:PinnacleBank:NRO"
    return txn(dt, f"UPI/P2M/{random.randint(100000000000,999999999999)}/{svc}", "", [
        (acct, -amt, "INR"),
        ("Expenses:Entertainment", amt, "INR"),
    ], source_account=acct)

def gen_inr_transfer(dt):
    """Two-leg transfer: NRO → Transfer, then Transfer → NRE (next day)."""
    amt = round(random.uniform(10000, 100000), 0)
    neft_ref = random.randint(10000000000, 99999999999)
    leg1 = txn(dt, f"NEFT/MB/{neft_ref}/Self", "Transfer to NRE", [
        ("Assets:Liquid:Savings:PinnacleBank:NRO", -amt, "INR"),
        ("Assets:Transfer", amt, "INR"),
    ], source_account="Assets:Liquid:Savings:PinnacleBank:NRO")
    leg2 = txn(dt + timedelta(days=1), f"NEFT/MB/{neft_ref}/Self", "Transfer from NRO", [
        ("Assets:Transfer", -amt, "INR"),
        ("Assets:Liquid:Savings:PinnacleBank:NRE", amt, "INR"),
    ], source_account="Assets:Liquid:Savings:PinnacleBank:NRE")
    return [leg1, leg2]

def gen_inr_fees(dt):
    amt = round(random.uniform(100, 500), 0)
    return txn(dt, "Annual Debit Card Fee", "", [
        ("Assets:Liquid:Savings:PinnacleBank:NRE", -amt, "INR"),
        ("Expenses:Fees:Banking", amt, "INR"),
    ], source_account="Assets:Liquid:Savings:PinnacleBank:NRE")

def gen_term_deposit_purchase(dt, acct, amt, source):
    return txn(dt, "Term Deposit Purchase", f"CD at {acct.split(':')[3]}", [
        (acct, amt, "USD"),
        (source, -amt, "USD"),
    ], source_account=acct)

def gen_term_deposit_interest(dt):
    amt = round(random.uniform(100, 800), 2)
    acct = random.choice(USD_CHECKING)
    return txn(dt, "Term Deposit Interest", "", [
        (acct, amt, "USD"),
        ("Income:Interest:TermDeposits", -amt, "USD"),
    ], source_account=acct)

def gen_inr_business_income(dt):
    amt = round(random.uniform(50000, 200000), 0)
    return txn(dt, f"NEFT/{random.randint(10000000000,99999999999)}/Client Payment", "Consulting income", [
        ("Assets:Liquid:Checking:NationalBank", amt, "INR"),
        ("Income:Business:Consulting", -amt, "INR"),
    ], source_account="Assets:Liquid:Checking:NationalBank")

def gen_inr_business_expense(dt):
    items = [
        ("Google Workspace", 500),
        ("Professional Tax", 2500),
        ("Office Supplies", None),
        ("Domain Registration", 800),
    ]
    svc, amt = random.choice(items)
    if amt is None:
        amt = round(random.uniform(500, 5000), 0)
    return txn(dt, f"UPI/P2M/{random.randint(100000000000,999999999999)}/{svc}", "", [
        ("Assets:Liquid:Checking:NationalBank", -amt, "INR"),
        ("Expenses:Business", amt, "INR"),
    ], source_account="Assets:Liquid:Checking:NationalBank")

def gen_inr_fd(dt):
    """Fixed deposit in INR."""
    accts = [
        ("Assets:Investments:TermDeposits:PinnacleBank:NRE-FD", "Assets:Liquid:Savings:PinnacleBank:NRE"),
        ("Assets:Investments:TermDeposits:PinnacleBank:NRO-FD", "Assets:Liquid:Savings:PinnacleBank:NRO"),
    ]
    fd_acct, source = random.choice(accts)
    amt = round(random.choice([100000, 200000, 500000]), 0)
    return txn(dt, "Fixed Deposit", f"FD at PinnacleBank", [
        (fd_acct, amt, "INR"),
        (source, -amt, "INR"),
    ], source_account=fd_acct)

def gen_inr_car(dt):
    items = [
        ("Tata Motors", "Car booking advance", 25000),
        ("Shell Petrol Pump", "Petrol", None),
        ("Indian Oil", "Petrol", None),
        ("Car Service Center", "Periodic service", None),
    ]
    store, narr, amt = random.choice(items)
    if amt is None:
        amt = round(random.uniform(500, 8000), 0)
    acct = "Assets:Liquid:Savings:PinnacleBank:NRO"
    return txn(dt, f"UPI/P2M/{random.randint(100000000000,999999999999)}/{store}", narr, [
        (acct, -amt, "INR"),
        ("Expenses:Car", amt, "INR"),
    ], source_account=acct)

# --- Generators for previously-empty accounts ---

def gen_ibond_purchase(dt):
    amt = round(random.choice([5000, 10000]), 2)
    return txn(dt, "TreasuryDirect", "I-Bond purchase", [
        ("Assets:Investments:Bonds:TreasuryDirect:IBonds", amt, "USD"),
        ("Assets:Liquid:Checking:WestCoastBank", -amt, "USD"),
    ], source_account="Assets:Investments:Bonds:TreasuryDirect:IBonds")

def gen_nationalbank_savings_transfer(dt):
    """Transfer from NRO to NationalBank Savings."""
    amt = round(random.uniform(20000, 80000), 0)
    return txn(dt, f"UPI/P2A/{random.randint(100000000000,999999999999)}/Self", "Transfer to NationalBank Savings", [
        ("Assets:Liquid:Savings:PinnacleBank:NRO", -amt, "INR"),
        ("Assets:Liquid:Savings:NationalBank", amt, "INR"),
    ], source_account="Assets:Liquid:Savings:NationalBank")

def gen_nationalbank_savings_interest(dt):
    amt = round(random.uniform(100, 2000), 0)
    return txn(dt, "Interest Paid", "", [
        ("Assets:Liquid:Savings:NationalBank", amt, "INR"),
        ("Income:Interest:Savings:NationalBank", -amt, "INR"),
    ], source_account="Assets:Liquid:Savings:NationalBank")

def gen_receivable_work(dt):
    """Expense reimbursement from employer — pending then received."""
    amt = round(random.uniform(200, 1500), 2)
    return [
        txn(dt, "Globex Corporation", "Expense reimbursement pending", [
            ("Assets:Receivable:Work", amt, "USD"),
            ("Income:Other", -amt, "USD"),
        ], source_account="Assets:Receivable:Work"),
        txn(dt + timedelta(days=random.randint(5, 15)), "Globex Corporation", "Expense reimbursement received", [
            ("Assets:Liquid:Checking:WestCoastBank", amt, "USD"),
            ("Assets:Receivable:Work", -amt, "USD"),
        ], source_account="Assets:Liquid:Checking:WestCoastBank"),
    ]

def gen_receivable_personal(dt):
    """Personal loan to/from a friend."""
    amt = round(random.uniform(50, 500), 2)
    return [
        txn(dt, "Personal loan", "Lent to friend", [
            ("Assets:Receivable:Personal", amt, "USD"),
            ("Assets:Liquid:Checking:WestCoastBank", -amt, "USD"),
        ], source_account="Assets:Receivable:Personal"),
        txn(dt + timedelta(days=random.randint(10, 45)), "Personal loan", "Repaid by friend", [
            ("Assets:Liquid:Checking:WestCoastBank", amt, "USD"),
            ("Assets:Receivable:Personal", -amt, "USD"),
        ], source_account="Assets:Liquid:Checking:WestCoastBank"),
    ]

def gen_hobbies(dt):
    items = [
        ("Michaels", "Art supplies"),
        ("REI", "Camping gear"),
        ("Guitar Center", "Guitar strings"),
        ("Blick Art Materials", "Painting supplies"),
        ("Joann Stores", "Craft supplies"),
    ]
    store, narr = random.choice(items)
    amt = round(random.uniform(15, 120), 2)
    cc = random.choice(USD_CC)
    return txn(dt, store, narr, [
        (cc, -amt, "USD"),
        ("Expenses:Hobbies", amt, "USD"),
    ], source_account=cc)

def gen_miscellaneous(dt):
    items = [
        ("USPS", "Postage"),
        ("UPS Store", "Shipping"),
        ("DMV", "Registration renewal"),
        ("Locksmith", "Key copy"),
        ("Dry Cleaners", "Dry cleaning"),
    ]
    store, narr = random.choice(items)
    amt = round(random.uniform(5, 80), 2)
    cc = random.choice(USD_CC)
    return txn(dt, store, narr, [
        (cc, -amt, "USD"),
        ("Expenses:Miscellaneous", amt, "USD"),
    ], source_account=cc)

def gen_school(dt):
    items = [
        ("Coursera", "Online course"),
        ("Udemy", "Programming course"),
        ("O'Reilly Media", "Book subscription"),
        ("Community College", "Evening class"),
    ]
    store, narr = random.choice(items)
    amt = round(random.uniform(12, 200), 2)
    cc = random.choice(USD_CC)
    return txn(dt, store, narr, [
        (cc, -amt, "USD"),
        ("Expenses:School", amt, "USD"),
    ], source_account=cc)

def gen_cc_fee(dt):
    cc = random.choice(USD_CC)
    amt = round(random.uniform(25, 95), 2)
    return txn(dt, f"{cc.split(':')[-1]} Annual Fee", "Credit card annual fee", [
        (cc, -amt, "USD"),
        ("Expenses:Fees:CreditCard", amt, "USD"),
    ], source_account=cc)

def gen_atm_fee(dt):
    amt = round(random.uniform(2, 5), 2)
    return txn(dt, "ATM Fee", "Out-of-network ATM withdrawal fee", [
        ("Assets:Liquid:Checking:WestCoastBank", -amt, "USD"),
        ("Expenses:Fees:ATM", amt, "USD"),
    ], source_account="Assets:Liquid:Checking:WestCoastBank")

def gen_cc_rewards(dt):
    amt = round(random.uniform(25, 150), 2)
    cc = random.choice(USD_CC)
    return txn(dt, f"{cc.split(':')[-1]} Rewards", "Statement credit rewards", [
        (cc, amt, "USD"),
        ("Income:Rewards", -amt, "USD"),
    ], source_account=cc)

def gen_valleycu_checking_interest(dt):
    amt = round(random.uniform(0.05, 2.00), 2)
    return txn(dt, "Interest Earned", "", [
        ("Assets:Liquid:Checking:ValleyCU", amt, "USD"),
        ("Income:Interest:Checking:ValleyCU", -amt, "USD"),
    ], source_account="Assets:Liquid:Checking:ValleyCU")

def gen_capitalfirst_purchase(dt):
    """Occasional purchase on CapitalFirst card."""
    items = [
        ("Costco", "Bulk shopping"),
        ("Home Depot", "Home improvement"),
        ("Lowe's", "Hardware"),
        ("Sam's Club", "Warehouse shopping"),
    ]
    store, narr = random.choice(items)
    amt = round(random.uniform(50, 300), 2)
    return txn(dt, store, narr, [
        ("Liabilities:CreditCards:CapitalFirst", -amt, "USD"),
        ("Expenses:HouseholdItems", amt, "USD"),
    ], source_account="Liabilities:CreditCards:CapitalFirst")

def gen_westcoastbank_cc_purchase(dt):
    """Occasional purchase on WestCoastBank card."""
    items = [
        ("Amazon", "Online order"),
        ("Walmart", "Shopping"),
        ("Target", "Household items"),
    ]
    store, narr = random.choice(items)
    amt = round(random.uniform(20, 150), 2)
    return txn(dt, store, narr, [
        ("Liabilities:CreditCards:WestCoastBank", -amt, "USD"),
        ("Expenses:HouseholdItems", amt, "USD"),
    ], source_account="Liabilities:CreditCards:WestCoastBank")

def gen_unknown_expense(dt):
    amt = round(random.uniform(5, 50), 2)
    cc = random.choice(USD_CC)
    return txn(dt, "Unrecognized Merchant", "Uncategorized", [
        (cc, -amt, "USD"),
        ("Expenses:Unknown", amt, "USD"),
    ], source_account=cc)

# --- Main generation ---

def generate():
    all_txns = []  # list of (date, txn_string)

    start_date = date(2019, 1, 1)
    end_date = date(2026, 4, 15)

    current = start_date
    while current <= end_date:
        year = current.year
        month = current.month
        day = current.day
        weekday = current.weekday()

        # --- Monthly events (1st-5th of month) ---
        if day == 1:
            all_txns.append((current, gen_rent(current)))
            all_txns.append((current + timedelta(days=1), gen_internet(current + timedelta(days=1))))
            all_txns.append((current + timedelta(days=2), gen_phone(current + timedelta(days=2))))
            all_txns.append((current + timedelta(days=3), gen_car_lease(current + timedelta(days=3))))
            for t in gen_health_insurance(current + timedelta(days=4)):
                all_txns.append((current + timedelta(days=4), t))
            if day == 1 and month % 2 == 0:
                all_txns.append((current + timedelta(days=5), gen_auto_insurance(current + timedelta(days=5))))

        # Biweekly salary (1st and 15th)
        if day in (1, 15):
            for t in gen_salary(current):
                all_txns.append((current, t))

        # Annual bonus in March
        if day == 15 and month == 3:
            all_txns.append((current, gen_bonus(current)))

        # Utilities mid-month
        if day == 15:
            for t in gen_utilities(current):
                all_txns.append((current, t))

        # House cleaning biweekly
        if day in (10, 24):
            all_txns.append((current, gen_house_cleaning(current)))

        # Childcare monthly
        if day == 5:
            t = gen_childcare(current)
            if t:
                all_txns.append((current, t))

        # Credit card payment mid-month
        if day == 20:
            all_txns.append((current, gen_cc_payment(current)))

        # Savings interest quarterly
        if day == 28 and month in (3, 6, 9, 12):
            for t in gen_savings_interest(current):
                all_txns.append((current, t))

        # Checking interest monthly
        if day == 28:
            all_txns.append((current, gen_checking_interest(current)))

        # HYSA interest monthly
        if day == 1 and year >= 2019 and month >= 5:
            all_txns.append((current, gen_hysa_interest(current)))

        # Transfer to HYSA monthly
        if day == 5 and year >= 2019 and month >= 5:
            all_txns.append((current, gen_transfer_to_hysa(current)))

        # Brokerage investment monthly
        if day == 10 and year >= 2019:
            all_txns.append((current, gen_brokerage_transfer(current)))

        # Replenish ValleyCU checking from HYSA quarterly (after HYSA has funds)
        if day == 15 and month in (1, 4, 7, 10) and year >= 2020:
            all_txns.append((current, gen_savings_to_checking_transfer(current)))

        # Inward remittance to NRO (quarterly, to fund INR expenses)
        if day == 1 and month in (1, 4, 7, 10) and year >= 2019:
            for t in gen_inr_inward_remittance(current):
                all_txns.append((current, t))

        # Groceries 2-3 times per week
        if weekday in (0, 3, 6):  # Mon, Thu, Sun
            if random.random() < 0.85:
                all_txns.append((current, gen_usd_grocery(current)))

        # Eating out 2-4 times per week
        if weekday in (2, 4, 5, 6):  # Wed, Fri, Sat, Sun
            if random.random() < 0.7:
                all_txns.append((current, gen_usd_restaurant(current)))

        # Gas weekly
        if weekday == 6 and random.random() < 0.6:
            all_txns.append((current, gen_usd_gas(current)))

        # Cloud services scattered
        if day in (5, 12, 18, 25) and random.random() < 0.4:
            all_txns.append((current, gen_cloud_service(current)))

        # Entertainment
        if day in (1, 8, 15, 22) and random.random() < 0.5:
            all_txns.append((current, gen_entertainment(current)))

        # Healthcare monthly-ish
        if day == 12 and random.random() < 0.4:
            all_txns.append((current, gen_healthcare(current)))

        # Travel occasionally
        if day == 20 and random.random() < 0.15:
            all_txns.append((current, gen_travel(current)))

        # Gadgets occasionally
        if day == 18 and random.random() < 0.08:
            all_txns.append((current, gen_gadget(current)))

        # Gifts quarterly-ish
        if day == 15 and month in (2, 5, 8, 11):
            all_txns.append((current, gen_gifts(current)))

        # Donations quarterly
        if day == 1 and month in (1, 4, 7, 10):
            all_txns.append((current, gen_donation(current)))

        # Personal items
        if weekday == 5 and random.random() < 0.25:
            all_txns.append((current, gen_personal_items(current)))

        # Household items
        if day == 22 and random.random() < 0.35:
            all_txns.append((current, gen_household(current)))

        # Grooming monthly
        if day == 8 and month % 1 == 0 and random.random() < 0.5:
            all_txns.append((current, gen_grooming(current)))

        # Parking
        if weekday in (1, 3) and random.random() < 0.15:
            all_txns.append((current, gen_parking(current)))

        # Hobbies monthly-ish
        if day == 20 and random.random() < 0.25:
            all_txns.append((current, gen_hobbies(current)))

        # Miscellaneous bimonthly-ish
        if day == 14 and random.random() < 0.2:
            all_txns.append((current, gen_miscellaneous(current)))

        # School/education quarterly
        if day == 5 and month in (1, 4, 7, 10) and random.random() < 0.6:
            all_txns.append((current, gen_school(current)))

        # Unknown/uncategorized expenses occasionally
        if day == 17 and random.random() < 0.08:
            all_txns.append((current, gen_unknown_expense(current)))

        # ATM fees occasionally
        if day == 25 and random.random() < 0.1:
            all_txns.append((current, gen_atm_fee(current)))

        # Credit card annual fees yearly
        if day == 1 and month == 2:
            all_txns.append((current, gen_cc_fee(current)))

        # Credit card rewards quarterly
        if day == 15 and month in (3, 6, 9, 12):
            all_txns.append((current, gen_cc_rewards(current)))

        # ValleyCU checking interest monthly
        if day == 28:
            all_txns.append((current, gen_valleycu_checking_interest(current)))

        # CapitalFirst card purchases monthly
        if day == 12 and random.random() < 0.4:
            all_txns.append((current, gen_capitalfirst_purchase(current)))

        # WestCoastBank card purchases monthly
        if day == 18 and random.random() < 0.35:
            all_txns.append((current, gen_westcoastbank_cc_purchase(current)))

        # Work expense reimbursements quarterly
        if day == 10 and month in (2, 5, 8, 11) and random.random() < 0.5:
            for t in gen_receivable_work(current):
                all_txns.append((current, t))

        # Personal receivables twice a year
        if day == 1 and month in (3, 9) and random.random() < 0.6:
            for t in gen_receivable_personal(current):
                all_txns.append((current, t))

        # --- INR transactions ---
        # More sparse before 2026, richer in 2026

        # Quarterly interest on Indian savings
        if day == 31 and month in (3, 6, 9, 12):
            for t in gen_inr_interest(current):
                all_txns.append((current, t))
        elif day == 30 and month in (6, 9) and date(year, month, 1) + timedelta(days=30) > date(year, month+1, 1) if month < 12 else False:
            pass  # handled above

        # INR groceries (weekly in 2026, monthly before)
        if year >= 2026 and month >= 2:
            if weekday == 2 and random.random() < 0.7:
                all_txns.append((current, gen_inr_grocery(current)))
            if weekday == 5 and random.random() < 0.5:
                all_txns.append((current, gen_inr_restaurant(current)))
            if day == 15 and random.random() < 0.4:
                all_txns.append((current, gen_inr_household(current)))
            if day == 10 and random.random() < 0.3:
                all_txns.append((current, gen_inr_entertainment(current)))
            if day == 20:
                all_txns.append((current, gen_inr_car(current)))
        elif random.random() < 0.02:
            # Occasional INR transaction in earlier years
            all_txns.append((current, gen_inr_grocery(current)))

        # ATM cash in India (occasional)
        if year >= 2024 and day == 25 and random.random() < 0.3:
            all_txns.append((current, gen_inr_cash(current)))

        # INR donations yearly
        if day == 15 and month == 1 and year >= 2020:
            all_txns.append((current, gen_inr_donation(current)))

        # INR fees yearly
        if day == 15 and month == 7:
            all_txns.append((current, gen_inr_fees(current)))

        # Transfers INR
        if day == 1 and month in (1, 7) and year >= 2020:
            for t in gen_inr_transfer(current):
                all_txns.append((current, t))

        # Term deposit interest monthly (2020+)
        if day == 1 and year >= 2020:
            all_txns.append((current, gen_term_deposit_interest(current)))

        # NationalBank savings (2026, after open on 2026-03-06)
        if year == 2026 and month >= 3 and not (month == 3 and day <= 6):
            # Transfer from NRO to NationalBank Savings monthly
            if day == 8:
                all_txns.append((current, gen_nationalbank_savings_transfer(current)))
            # Interest monthly
            if day == 28:
                all_txns.append((current, gen_nationalbank_savings_interest(current)))

        # Business income/expenses (2026 only, after NationalBank opens 2026-03-06)
        if year == 2026 and month >= 3 and not (month == 3 and day <= 6):
            if day == 7:
                all_txns.append((current, gen_inr_business_income(current)))
            if day in (10, 20) and random.random() < 0.5:
                all_txns.append((current, gen_inr_business_expense(current)))

        current += timedelta(days=1)

    # One-off term deposit purchases
    all_txns.append((date(2023, 1, 14), gen_term_deposit_purchase(
        date(2023, 1, 14), "Assets:Investments:TermDeposits:ValleyCU:CD1",
        200000.00, "Assets:Liquid:Savings:ValleyCU")))
    all_txns.append((date(2023, 1, 14), gen_term_deposit_purchase(
        date(2023, 1, 14), "Assets:Investments:TermDeposits:ValleyCU:CD2",
        50000.00, "Assets:Liquid:Savings:ValleyCU")))
    all_txns.append((date(2023, 1, 24), gen_term_deposit_purchase(
        date(2023, 1, 24), "Assets:Investments:TermDeposits:WestCoastBank:CD001",
        100000.00, "Assets:Liquid:Savings:WestCoastBank")))
    all_txns.append((date(2023, 10, 14), gen_term_deposit_purchase(
        date(2023, 10, 14), "Assets:Investments:TermDeposits:ValleyCU:CD3",
        75000.00, "Assets:Liquid:Savings:ValleyCU")))
    all_txns.append((date(2024, 8, 14), gen_term_deposit_purchase(
        date(2024, 8, 14), "Assets:Investments:TermDeposits:ValleyCU:CD4",
        100000.00, "Assets:Liquid:Savings:ValleyCU")))

    # I-Bond purchases (annual, 2020-2024)
    for yr in range(2020, 2025):
        all_txns.append((date(yr, 4, 15), gen_ibond_purchase(date(yr, 4, 15))))

    # INR fixed deposits — NRE-FD opens 2026-01-29, NRO-FD opens 2026-02-03
    all_txns.append((date(2026, 2, 1), txn(date(2026, 2, 1), "Fixed Deposit", "FD at PinnacleBank", [
        ("Assets:Investments:TermDeposits:PinnacleBank:NRE-FD", 500000.00, "INR"),
        ("Assets:Liquid:Savings:PinnacleBank:NRE", -500000.00, "INR"),
    ], source_account="Assets:Investments:TermDeposits:PinnacleBank:NRE-FD")))
    all_txns.append((date(2026, 2, 5), txn(date(2026, 2, 5), "Fixed Deposit", "FD at PinnacleBank", [
        ("Assets:Investments:TermDeposits:PinnacleBank:NRO-FD", 300000.00, "INR"),
        ("Assets:Liquid:Savings:PinnacleBank:NRO", -300000.00, "INR"),
    ], source_account="Assets:Investments:TermDeposits:PinnacleBank:NRO-FD")))

    # Big travel expenses (vacations)
    for yr in range(2019, 2027):
        vac_month = random.choice([6, 7, 8, 12])
        vac_day = random.randint(10, 20)
        try:
            vac_date = date(yr, vac_month, vac_day)
        except ValueError:
            vac_date = date(yr, vac_month, 15)
        if vac_date <= end_date:
            amt = round(random.uniform(800, 3000), 2)
            cc = "Liabilities:CreditCards:Citi:DoubleCash"
            all_txns.append((vac_date, txn(vac_date, "United Airlines", "Vacation flights", [
                (cc, -amt, "USD"),
                ("Expenses:Travel", amt, "USD"),
            ], source_account=cc)))
            hotel_date = vac_date + timedelta(days=1)
            hotel_amt = round(random.uniform(500, 2000), 2)
            all_txns.append((hotel_date, txn(hotel_date, "Marriott Hotels", "Vacation hotel", [
                (cc, -hotel_amt, "USD"),
                ("Expenses:Travel", hotel_amt, "USD"),
            ], source_account=cc)))

    # Sort by date
    all_txns.sort(key=lambda x: x[0])

    # Build output
    output = []
    output.append("; -*- mode: beancount; coding: utf-8; -*-")
    output.append("; Fake ledger for demo/screenshot purposes")
    output.append("; This file contains fictional data only")
    output.append("")
    output.append("")

    # Account opens
    output.append(ACCOUNTS)
    output.append("")
    output.append("")

    # Pad and balance
    output.append(PAD_BALANCE)
    output.append("")
    output.append("")

    # Transactions
    for dt, t in all_txns:
        output.append(t)
        output.append("")
        output.append("")

    return "\n".join(output)

if __name__ == "__main__":
    content = generate()
    output_path = Path(__file__).resolve().parent.parent / "data" / "ledgers" / "fake.beancount"
    with open(output_path, "w") as f:
        f.write(content)
    # Count stats
    lines = content.split("\n")
    txn_count = sum(1 for l in lines if l.startswith("20") and ' * "' in l)
    print(f"Generated {len(lines)} lines, {txn_count} transactions")
