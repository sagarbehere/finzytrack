"""Parity corpus plugin (S0094): raises while it runs; Beancount turns that into an error and goes on."""

__plugins__ = ["fail"]


def fail(entries, options_map):
    raise ValueError("parity corpus: this plugin always fails")
