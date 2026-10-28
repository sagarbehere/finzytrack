# Beancount 3.2.0 beancount/utils/defdict.py: the names the engine uses (S0049). Beancount fills it with Decimals
# (the tolerances of `interpolate.infer_tolerances`).
from decimal import Decimal

class ImmutableDictWithDefault(dict[str, Decimal]):
    default: Decimal
