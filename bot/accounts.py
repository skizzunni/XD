"""Local account labels select paper risk profiles, never brokerage credentials."""

from dataclasses import replace
import json
from pathlib import Path

from .report import write_json


def load_accounts(path):
    path = Path(path)
    if not path.exists():
        return {
            "version": 1,
            "active": "paper",
            "accounts": {"paper": {"stage": "funded"}},
        }
    data = json.loads(path.read_text())
    if data.get("version") != 1 or data.get("active") not in data.get("accounts", {}):
        raise ValueError("Invalid account registry")
    for value in data["accounts"].values():
        if value.get("stage") not in {"evaluation", "funded"}:
            raise ValueError("Account stage must be evaluation or funded")
        if "loss_limit_usd" in value and value["loss_limit_usd"] <= 0:
            raise ValueError("Account loss limit must be positive")
    return data


def update_account(path, name, stage=None, select=False, loss_limit=None):
    data = load_accounts(path)
    if stage:
        data["accounts"].setdefault(name, {})["stage"] = stage
    if name not in data["accounts"]:
        raise ValueError("Unknown account; set its stage first")
    if loss_limit is not None:
        if loss_limit <= 0:
            raise ValueError("Loss limit must be positive")
        data["accounts"][name]["loss_limit_usd"] = loss_limit
    if select:
        data["active"] = name
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    write_json(temporary, data)
    temporary.replace(path)
    return data


def apply_account(config, path, name=None):
    data = load_accounts(path)
    name = name or data["active"]
    if name not in data["accounts"]:
        raise ValueError(f"Unknown account label {name!r}")
    account = data["accounts"][name]
    return (
        replace(
            config,
            daily_profit_target_usd=750 if account["stage"] == "evaluation" else 0,
            session_loss_budget_usd=account.get(
                "loss_limit_usd", config.session_loss_budget_usd
            ),
        ),
        name,
        account,
    )
