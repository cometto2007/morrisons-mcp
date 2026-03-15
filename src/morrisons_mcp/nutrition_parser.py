import logging
import re

from bs4 import BeautifulSoup

from .models import NutritionPer100g

logger = logging.getLogger(__name__)


def _extract_float(text: str) -> float | None:
    """Extract a float from a string like '10.5g', '1234kJ', 'less than 0.1g'."""
    text = text.strip()

    # Handle "less than X" or "< X" → use half the value as an approximation
    less_than = re.match(r"(?:less\s+than|<)\s*([\d.]+)", text, re.IGNORECASE)
    if less_than:
        try:
            return float(less_than.group(1)) / 2
        except ValueError:
            return None

    m = re.search(r"([\d.]+)", text)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def parse_nutrition_html(html: str | None) -> NutritionPer100g | None:
    """Parse Morrisons BOP nutrition HTML table into structured data."""
    if not html:
        return None

    try:
        soup = BeautifulSoup(html, "html.parser")
        rows = soup.find_all("tr")

        result: dict[str, float | None] = {}

        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 2:
                continue

            label = cells[0].get_text(strip=True).lower()
            value_text = cells[1].get_text(strip=True)

            if "energy" in label:
                # May be "1234kJ / 295kcal" or just one value
                kj_match = re.search(r"([\d.]+)\s*kj", value_text, re.IGNORECASE)
                kcal_match = re.search(r"([\d.]+)\s*kcal", value_text, re.IGNORECASE)
                if kj_match:
                    result["energy_kj"] = float(kj_match.group(1))
                if kcal_match:
                    result["energy_kcal"] = float(kcal_match.group(1))

            elif label == "fat" or label.startswith("fat "):
                result["fat_g"] = _extract_float(value_text)

            elif "saturate" in label:
                result["saturates_g"] = _extract_float(value_text)

            elif label.startswith("carbohydrate"):
                result["carbohydrate_g"] = _extract_float(value_text)

            elif "sugar" in label:
                result["sugars_g"] = _extract_float(value_text)

            elif "fibre" in label or "fiber" in label:
                result["fibre_g"] = _extract_float(value_text)

            elif label == "protein" or label.startswith("protein "):
                result["protein_g"] = _extract_float(value_text)

            elif label == "salt" or label.startswith("salt "):
                result["salt_g"] = _extract_float(value_text)

        if not result:
            logger.debug("Nutrition table parsed but no recognised nutrient rows found")
            return None

        # Fallback: derive kcal from kJ if only kJ was found
        if result.get("energy_kcal") is None and result.get("energy_kj") is not None:
            result["energy_kcal"] = round(result["energy_kj"] / 4.184, 1)

        return NutritionPer100g(**result)

    except Exception as e:
        logger.error(f"Failed to parse nutrition HTML: {e}")
        return None
