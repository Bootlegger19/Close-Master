# Settings shared by more than one part of the app.
from datetime import timedelta

# (label shown on screen, value in cents). Edit this list to change currency.
DENOMINATIONS = [
    ("Bills", [("$100", 10000), ("$50", 5000), ("$20", 2000), ("$10", 1000), ("$5", 500)]),
    ("Coins", [("$2", 200), ("$1", 100), ("25¢", 25), ("10¢", 10), ("5¢", 5)]),
]

# A bar's "business day" runs from this hour to the same hour the next morning,
# so a closing at 1 a.m. still belongs to the previous evening's date.
BUSINESS_DAY_STARTS_HOUR = 6


def business_date(moment):
    """The business day a datetime belongs to."""
    return (moment - timedelta(hours=BUSINESS_DAY_STARTS_HOUR)).date()