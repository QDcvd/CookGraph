import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
ALIASES_PATH = ROOT / "config" / "recommendation_aliases.json"
REJECTED_PATH = ROOT / "config" / "recommendation_aliases.rejected.json"


def _load_aliases() -> dict:
    assert ALIASES_PATH.is_file()
    return json.loads(ALIASES_PATH.read_text(encoding="utf-8"))


def test_recommendation_alias_files_exist_and_are_json():
    aliases = _load_aliases()
    rejected = json.loads(REJECTED_PATH.read_text(encoding="utf-8"))
    assert isinstance(aliases, dict)
    assert isinstance(rejected, dict)


def test_spicy_pepper_aliases_are_available():
    aliases = _load_aliases()
    pepper_group = aliases["ingredient"]["辣椒"]
    assert {"辣椒", "青椒", "小米辣", "泡椒"}.issubset(set(pepper_group))


def test_beef_aliases_are_available():
    aliases = _load_aliases()
    beef_group = aliases["ingredient"]["牛肉"]
    assert {"牛肉", "黄牛肉", "牛里脊", "肥牛"}.issubset(set(beef_group))


def test_generic_meat_is_not_expanded_to_all_meats():
    aliases = _load_aliases()
    ingredient_aliases = aliases.get("ingredient", {})
    assert "肉" not in ingredient_aliases
