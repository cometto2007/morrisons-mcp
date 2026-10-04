from pydantic import BaseModel, Field, computed_field
from typing import Literal, Optional


# --- Ingredient Parsing ---

class ParsedIngredient(BaseModel):
    """Result of parsing a raw ingredient string like '500g chicken breast'."""
    original: str = Field(description="The original ingredient string")
    quantity: Optional[float] = Field(None, description="Numeric quantity extracted")
    unit: Optional[str] = Field(None, description="Unit of measurement (g, kg, ml, l, tbsp, tsp, etc.)")
    name: str = Field(description="The ingredient name with quantity/unit stripped")
    search_query: str = Field(description="Cleaned query optimised for Morrisons search")


# --- Product Data ---

class Promotion(BaseModel):
    """A product promotion/offer."""
    description: str
    promo_price: Optional[float] = None
    expiry: Optional[str] = None

class ProductResult(BaseModel):
    """A single product from Morrisons search results."""
    product_id: str = Field(description="UUID product ID")
    retailer_product_id: str = Field(description="Numeric string ID used for BOP endpoint")
    name: str
    brand: Optional[str] = None
    pack_size: Optional[str] = Field(None, description="e.g. '1kg', '6 pack'")
    price: float = Field(description="Current price in GBP")
    unit_price: Optional[str] = Field(None, description="e.g. '£3.50/kg'")
    promotions: list[Promotion] = Field(default_factory=list)
    category_path: Optional[str] = Field(None, description="e.g. 'Meat & Poultry > Chicken > Breast'")
    available: bool = True
    image_url: Optional[str] = None
    rating: Optional[float] = None
    review_count: Optional[int] = None

    @computed_field(description="Product page URL; Morrisons redirects it to the canonical slug URL")
    @property
    def url(self) -> str:
        return f"https://groceries.morrisons.com/products/{self.retailer_product_id}"


# --- Product picker ---

class IngredientChoices(BaseModel):
    """Candidate products for one ingredient, for the picker UI."""
    ingredient: str = Field(description="The ingredient as given")
    query: str = Field(description="The search query actually used")
    results: list[ProductResult] = Field(default_factory=list)


class ProductPicks(BaseModel):
    """pick_products result: one row of candidates per ingredient."""
    ingredients: list[IngredientChoices]


# --- Nutrition ---

class NutritionPer100g(BaseModel):
    """Nutritional values per 100 g or per 100 ml (see `basis`).

    Values come only from the label column headed "per 100g" or "per 100ml";
    per-serving columns are never used.
    """
    basis: Literal["100g", "100ml"] = Field(
        "100g", description="What the figures are per: 100 grams or 100 millilitres"
    )
    basis_note: Optional[str] = Field(
        None,
        description=(
            "Qualifier from the per-100 column header, lowercase: e.g. 'as consumed' "
            "(cooked), 'as sold', 'prepared', 'cooked', 'raw', 'drained', or a cooking "
            "method such as 'grilled'. Null when the header has none."
        ),
    )
    energy_kj: Optional[float] = None
    energy_kcal: Optional[float] = None
    fat_g: Optional[float] = None
    saturates_g: Optional[float] = None
    carbohydrate_g: Optional[float] = None
    sugars_g: Optional[float] = None
    fibre_g: Optional[float] = None
    protein_g: Optional[float] = None
    salt_g: Optional[float] = None


class NetQuantity(BaseModel):
    """Net quantity parsed from the pack size, normalised to g or ml.

    Multipacks are totalled: "6 x 330ml" → 1980 ml.
    """
    value: float
    unit: Literal["g", "ml"]


class ProductDetail(BaseModel):
    """Full product detail from BOP endpoint.

    `found` is False when Morrisons no longer has the product (dead
    retailerProductId); every other field is then empty.
    """
    retailer_product_id: str
    found: bool = Field(True, description="False if Morrisons has no product with this ID")
    name: Optional[str] = None
    brand: Optional[str] = None
    pack_size: Optional[str] = Field(None, description="Raw packSizeDescription, e.g. '6 x 330ml'")
    net_quantity: Optional[NetQuantity] = Field(
        None, description="Parsed pack size in g or ml, when parseable"
    )
    price: Optional[float] = None
    nutrition_per_100g: Optional[NutritionPer100g] = None
    ingredients: Optional[str] = Field(
        None, description="The label's ingredient list as text, e.g. 'Tomato (65%), Concentrated Tomato Juice, ...'; null when the label has none (fresh meat, produce)"
    )
    dietary: list[str] = Field(
        default_factory=list, description="Dietary labels Morrisons shows for the product, e.g. ['Vegetarian', 'Vegan']"
    )
    country_of_origin: Optional[str] = None
    storage: Optional[str] = None
    cooking_guidelines: Optional[str] = None
    features: Optional[str] = None
    servings_info: Optional[str] = None
    promotions: list[Promotion] = Field(default_factory=list)


# --- Recipe Costing ---

class IngredientCost(BaseModel):
    """Cost breakdown for a single ingredient."""
    ingredient: str = Field(description="Original ingredient string from recipe")
    parsed_query: str = Field(description="What was searched on Morrisons")
    matched_product: Optional[ProductResult] = None
    match_confidence: Optional[float] = Field(None, description="0.0 to 1.0 fuzzy match score")
    cost: Optional[float] = Field(None, description="Price of matched product in GBP")
    on_hand: bool = Field(False, description="True if ingredient is a pantry staple the user already has")
    note: Optional[str] = Field(None, description="e.g. 'No match found', 'Chose cheapest per-unit'")

class RecipeCostResult(BaseModel):
    """Complete recipe costing result."""
    recipe_name: Optional[str] = None
    servings: Optional[float] = None
    ingredients: list[IngredientCost]
    total_cost: float = Field(description="Sum of matched ingredient costs in GBP")
    cost_per_serving: Optional[float] = None
    cost_excluding_pantry: float = Field(description="Total cost excluding pantry staples")
    cost_per_serving_excluding_pantry: Optional[float] = None
    unmatched_count: int = Field(description="Number of ingredients with no match")
