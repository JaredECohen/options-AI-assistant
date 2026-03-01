from __future__ import annotations

from app.core.safety import SECTION_TITLES
from app.core.strategies import get_strategy


class HeuristicProvider:
    name = "heuristic"

    async def generate(self, system: str, user: str, max_output_tokens: int, temperature: float) -> str:
        lower = user.lower()
        for name in list(get_strategy_names()):
            if name in lower:
                strat = get_strategy(name)
                if strat:
                    return format_strategy(strat)
        return default_response()


def get_strategy_names():
    from app.core.strategies import STRATEGIES

    return STRATEGIES.keys()


def format_strategy(strat: dict) -> str:
    return (
        f"Summary: {strat['summary']}\n"
        f"Setup: {strat['setup']}\n"
        f"Payoff at Expiration: {strat['payoff']}\n"
        f"Max Profit / Max Loss: {strat['max_profit']} / {strat['max_loss']}\n"
        f"Breakeven(s): {strat['breakevens']}\n"
        f"Key Sensitivities: {strat['key_sensitivities']}\n"
        f"Typical Use Case: {strat['typical_use_case']}\n"
        f"Main Risks: {strat['main_risks']}\n"
        "Assumptions / What I need from you: Provide ticker, expiration, strikes, call/put, buy/sell, quantity, and optional premiums if you want calculations."
    )


def default_response() -> str:
    return (
        "Summary: I can explain listed equity options strategies and evaluate positions you specify using live premiums.\n"
        "Setup: Ask about a specific strategy or provide legs to evaluate.\n"
        "Payoff at Expiration: I compute expiration payoff for user-specified legs.\n"
        "Max Profit / Max Loss: Computed from the specified premiums.\n"
        "Breakeven(s): Computed from the specified premiums.\n"
        "Key Sensitivities: I can summarize delta/vega/theta for the structure you choose.\n"
        "Typical Use Case: Evaluating a known strategy or set of legs.\n"
        "Main Risks: Options carry risk and time decay.\n"
        "Assumptions / What I need from you: Provide ticker, expiration, strikes, call/put, buy/sell, quantity, and optional premiums."
    )
