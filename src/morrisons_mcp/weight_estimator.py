from .models import ParsedIngredient

# Approximate weight in grams for common cooking units
UNIT_TO_GRAMS: dict[str, float] = {
    "tbsp": 15,
    "tablespoon": 15,
    "tsp": 5,
    "teaspoon": 5,
    "cup": 240,
    "ml": 1,
    "l": 1000,
    "litre": 1000,
    "liter": 1000,
    "clove": 5,
    "medium": 150,
    "large": 200,
    "small": 100,
    "tin": 400,
    "can": 400,
    "bunch": 30,
    "pinch": 0.5,
    "dash": 0.5,
    "spray": 0.5,
    "slice": 30,
    "piece": 50,
    "sheet": 25,      # 1 lasagne sheet ≈ 25g
    "pot": 150,
    "jar": 400,
    "bottle": 500,
}

# Ingredient-specific grams-per-tablespoon overrides
_INGREDIENT_TBSP_GRAMS: dict[str, float] = {
    "oil": 13,
    "olive oil": 13,
    "sunflower oil": 13,
    "vegetable oil": 13,
    "coconut oil": 13,
    "sesame oil": 13,
    "honey": 21,
    "sugar": 12,
    "flour": 8,
    "butter": 15,
    "garlic": 15,
    "soy sauce": 16,
}

# Ingredient-specific grams-per-unit for countable items (no unit or "whole")
_INGREDIENT_EACH_GRAMS: dict[str, float] = {
    "egg": 44,
    "eggs": 44,
    "avocado": 150,
    "onion": 150,
    "potato": 200,
    "lemon": 60,
    "lime": 45,
    "orange": 180,
    "apple": 180,
    "banana": 120,
    "tomato": 120,
    "pepper": 160,
    "carrot": 80,
    "aubergine": 300,   # 1 medium aubergine ≈ 300g
    "eggplant": 300,
    "courgette": 200,   # 1 medium courgette ≈ 200g
    "zucchini": 200,
    "bell pepper": 160, # 1 bell pepper ≈ 160g (also caught by "pepper" above)
    "stock cube": 10,   # 1 stock cube ≈ 10g
    "stock pot": 26,    # 1 Knorr stock pot ≈ 26g
}


def estimate_weight_grams(parsed: ParsedIngredient) -> float | None:
    """Estimate the weight in grams from the parsed ingredient."""
    quantity = parsed.quantity
    if quantity is None:
        return None

    unit = (parsed.unit or "").lower()
    name_lower = parsed.name.lower()

    # Direct gram/kg units
    if unit == "g":
        return quantity
    if unit == "kg":
        return quantity * 1000
    if unit in ("ml",):
        return quantity

    # Tablespoon/teaspoon with ingredient-specific overrides
    if unit in ("tbsp", "tablespoon", "tsp", "teaspoon"):
        for ingredient_key, grams_per_tbsp in _INGREDIENT_TBSP_GRAMS.items():
            if ingredient_key in name_lower:
                if unit in ("tsp", "teaspoon"):
                    return quantity * (grams_per_tbsp / 3)
                return quantity * grams_per_tbsp
        # Generic fallback
        return quantity * UNIT_TO_GRAMS.get(unit, 15)

    # Countable items (no unit, or descriptive units like medium/large/small)
    if unit in ("", "whole") or unit is None:
        for ingredient_key, grams_each in _INGREDIENT_EACH_GRAMS.items():
            if ingredient_key in name_lower:
                return quantity * grams_each
        return None

    # All other units from the generic table
    if unit in UNIT_TO_GRAMS:
        return quantity * UNIT_TO_GRAMS[unit]

    return None
