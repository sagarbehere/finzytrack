# Beancount 3.2.0 beancount/core/realization.py: the names the engine uses (S0049).
from beancount.core.data import Directive
from beancount.core.inventory import Inventory

class RealAccount(dict[str, RealAccount]):
    account: str
    balance: Inventory

def realize(
    entries: list[Directive], min_accounts: list[str] | None = None, compute_balance: bool = True
) -> RealAccount: ...
def get(real_account: RealAccount, account_name: str, default: RealAccount | None = None) -> RealAccount | None: ...
def compute_balance(real_account: RealAccount, leaf_only: bool = False) -> Inventory: ...
