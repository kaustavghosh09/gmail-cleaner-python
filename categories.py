# categories.py
from dataclasses import dataclass
from typing import Dict, List, Optional

SYSTEM_LABEL_PROCESSED = "PROCESSED_BY_CLEANER"


@dataclass(frozen=True)
class CategorySpec:
    key: str
    name: str
    description: str
    add_labels: List[str]
    archive: bool = False
    trash: bool = False
    spam: bool = False


CATEGORIES: Dict[str, CategorySpec] = {
    "1": CategorySpec(
        key="1",
        name="STAYS",
        description="Trusted sender. Keep as-is.",
        add_labels=["STAYS"],
    ),
    "2": CategorySpec(
        key="2",
        name="NEED_REVIEW",
        description="Needs human review later.",
        add_labels=["NEED_REVIEW"],
    ),
    "3": CategorySpec(
        key="3",
        name="BANKING",
        description="Finance / Banking emails.",
        add_labels=["BANKING"],
    ),
    "4": CategorySpec(
        key="4",
        name="NEWSLETTERS",
        description="Archive newsletters.",
        add_labels=["NEWSLETTERS"],
        archive=True,
    ),
    "5": CategorySpec(
        key="5",
        name="UNSUBSCRIBE",
        description="Mark for unsubscribe later.",
        add_labels=["UNSUBSCRIBE"],
        archive=True,
    ),
    "6": CategorySpec(
        key="6",
        name="DELETE",
        description="Move to Trash.",
        add_labels=["DELETE"],
        trash=True,
    ),
    "7": CategorySpec(
        key="7",
        name="DELETE_AND_SPAM",
        description="Spam + remove from inbox.",
        add_labels=["DELETE_AND_SPAM"],
        spam=True,
        archive=True,
    ),
    "8": CategorySpec(
        key="8",
        name="BLOCK",
        description="Mark spam + BLOCKED label.",
        add_labels=["BLOCKED"],
        spam=True,
        archive=True,
    ),
}


def print_menu():
    print("\n=== CATEGORY MENU ===")
    for k in sorted(CATEGORIES.keys(), key=int):
        c = CATEGORIES[k]
        print(f"{c.key}. {c.name:<18} - {c.description}")


def get_category(key: str) -> Optional[CategorySpec]:
    return CATEGORIES.get(key)
