from __future__ import annotations


TRADES = ("杂工", "泥瓦工", "水电工", "木工", "油漆工", "安装工")

# Initial defaults for new and migrated projects. Projects can edit this list.
TRADE_COLORS = {
    "杂工": "#78909C",
    "泥瓦工": "#C97838",
    "水电工": "#1976D2",
    "木工": "#8D6E63",
    "油漆工": "#8E5AA9",
    "安装工": "#2E8B57",
}

MIXED_TRADE_COLOR = "#315B76"


def normalized_trade(value: str | None) -> str:
    cleaned = str(value or "").strip()
    return cleaned or "杂工"
