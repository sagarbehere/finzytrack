"""ISO 4217 codes that count as cash in the mirror's `commodities.is_currency` (ADR-0025; BL-0120).

Derived by `engine/scripts/gen_iso4217.py` from the ISO 4217 Maintenance Agency's lists (SIX Financial
Information AG): List One (current codes) and List Three (historic codes). Do not edit by hand: download the
lists again and rerun the script (its docstring has the rule). The owner's rule (2026-10-29): every code that
was ever a country's money, current and historic, is cash; metals, funds and accounting units are not
(`NOT_CASH`, each with its reason). The ledger's operating currencies, `asset-class` and lots held at cost
decide before this list (`rows.is_currency`). Nothing is fetched at run time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class Source:
    """One of the Agency's files: where it was downloaded, its publication date and its SHA-256."""

    url: str
    published: str
    sha256: str


RETRIEVED: Final = "2026-11-08"
LIST_ONE: Final = Source(
    url="https://www.six-group.com/dam/download/financial-information/data-center/iso-currrency/lists/list-one.xml",
    published="2026-09-17",
    sha256="33139b438657d1cee116ba737807ea71d19d6de4b90f799a09c56f0cc6a1b0ff",
)
LIST_THREE: Final = Source(
    url="https://www.six-group.com/dam/download/financial-information/data-center/iso-currrency/lists/list-three.xml",
    published="2026-01-01",
    sha256="98fde2423cdb916dd59dcf5fe96222edad8fa198d865c1c83dbc464b9cc52387",
)

# 277 codes: every code of both lists that was a country's money.
_CASH_CODES = """
ADP AED AFA AFN ALK ALL AMD ANG AOA AOK AON AOR ARA ARP ARS ARY ATS AUD AWG AYM
AZM AZN BAD BAM BBD BDT BEC BEF BEL BGJ BGK BGL BGN BHD BIF BMD BND BOB BOP BRB
BRC BRE BRL BRN BRR BSD BTN BUK BWP BYB BYN BYR BZD CAD CDF CHF CLP CNY COP CRC
CSD CSJ CSK CUC CUP CVE CYP CZK DDM DEM DJF DKK DOP DZD ECS EEK EGP ERN ESA ESB
ESP ETB EUR FIM FJD FKP FRF GBP GEK GEL GHC GHP GHS GIP GMD GNE GNF GNS GQE GRD
GTQ GWE GWP GYD HKD HNL HRD HRK HTG HUF IDR IEP ILP ILR ILS INR IQD IRR ISJ ISK
ITL JMD JOD JPY KES KGS KHR KMF KPW KRW KWD KYD KZT LAJ LAK LBP LKR LRD LSL LSM
LTL LTT LUC LUF LUL LVL LVR LYD MAD MDL MGA MGF MKD MLF MMK MNT MOP MRO MRU MTL
MTP MUR MVQ MVR MWK MXN MXP MYR MZE MZM MZN NAD NGN NIC NIO NLG NOK NPR NZD OMR
PAB PEH PEI PEN PES PGK PHP PKR PLN PLZ PTE PYG QAR RHD ROK ROL RON RSD RUB RUR
RWF SAR SBD SCR SDD SDG SDP SEK SGD SHP SIT SKK SLE SLL SOS SRD SRG SSP STD STN
SUR SVC SYP SZL THB TJR TJS TMM TMT TND TOP TPE TRL TRY TTD TWD TZS UAH UAK UGS
UGW UGX USD UYN UYP UYU UZS VEB VED VEF VES VNC VND VUV WST XAF XCD XCG XOF XPF
YDD YER YUD YUM YUN ZAL ZAR ZMK ZMW ZRN ZRZ ZWC ZWD ZWG ZWL ZWN ZWR
"""
CASH: Final = frozenset(_CASH_CODES.split())

# 30 codes of the lists that are not cash, and why.
NOT_CASH: Final = {
    "BOV": "fund (ISO flags it): Mvdol",
    "CHC": "fund: WIR Franc (for electronic), the WIR Bank's money (as CHE, CHW, which ISO flags funds)",
    "CHE": "fund (ISO flags it): WIR Euro",
    "CHW": "fund (ISO flags it): WIR Franc",
    "CLF": "fund (ISO flags it): Unidad de Fomento",
    "COU": "fund (ISO flags it): Unidad de Valor Real",
    "ECV": "accounting unit: Ecuador's indexed Unidad de Valor Constante (as CLF, which ISO flags a fund)",
    "MXV": "fund (ISO flags it): Mexican Unidad de Inversion (UDI)",
    "USN": "fund (ISO flags it): US Dollar (Next day)",
    "USS": "fund: US Dollar (Same day), a settlement variant (as USN, which ISO flags a fund)",
    "UYI": "fund (ISO flags it): Uruguay Peso en Unidades Indexadas (UI)",
    "UYW": "fund (ISO flags it): Unidad Previsional",
    "XAD": "fund (ISO flags it): Arab Accounting Dinar",
    "XAG": "ISO non-country entry: Silver",
    "XAU": "ISO non-country entry: Gold",
    "XBA": "ISO non-country entry: Bond Markets Unit European Composite Unit (EURCO)",
    "XBB": "ISO non-country entry: Bond Markets Unit European Monetary Unit (E.M.U.-6)",
    "XBC": "ISO non-country entry: Bond Markets Unit European Unit of Account 9 (E.U.A.-9)",
    "XBD": "ISO non-country entry: Bond Markets Unit European Unit of Account 17 (E.U.A.-17)",
    "XDR": "accounting unit: the IMF's Special Drawing Right",
    "XEU": "accounting unit: the European Currency Unit (ECU), a basket, never notes or coins",
    "XFO": "ISO non-country entry: Gold-Franc",
    "XFU": "fund (ISO flags it): UIC-Franc",
    "XPD": "ISO non-country entry: Palladium",
    "XPT": "ISO non-country entry: Platinum",
    "XRE": "fund (ISO flags it): RINET Funds Code",
    "XSU": "accounting unit: the SUCRE regional payment system's unit",
    "XTS": "ISO non-country entry: Codes specifically reserved for testing purposes",
    "XUA": "accounting unit: the African Development Bank's unit of account",
    "XXX": "ISO non-country entry: The codes assigned for transactions where no currency is involved",
}
