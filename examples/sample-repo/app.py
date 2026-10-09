"""A tiny deliberately buggy application used only for demonstration."""

balance = 100


def withdraw(amount):
    global balance
    if amount >= 0:
        balance -= amount
    return balance
