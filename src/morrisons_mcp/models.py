from pydantic import BaseModel, Field
from typing import Optional


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


# --- Nutrition ---

class NutritionPer100g(BaseModel):
    """Nutritional values per 100g parsed from BOP HTML table."""
    energy_kj: Optional[float] = None
    energy_kcal: Optional[float] = None
    fat_g: Optional[float] = None
    saturates_g: Optional[float] = None
    carbohydrate_g: Optional[float] = None
    sugars_g: Optional[float] = None
    fibre_g: Optional[float] = None
    protein_g: Optional[float] = None
    salt_g: Optional[float] = None


class ProductDetail(BaseModel):
    """Full product detail from BOP endpoint."""
    retailer_product_id: str
    name: str
    brand: Optional[str] = None
    pack_size: Optional[str] = None
    price: Optional[float] = None
    nutrition_per_100g: Optional[NutritionPer100g] = None
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
    note: Optional[str] = Field(None, description="e.g. 'No match found', 'Chose cheapest per-unit'")

class RecipeCostResult(BaseModel):
    """Complete recipe costing result."""
    recipe_name: Optional[str] = None
    servings: Optional[float] = None
    ingredients: list[IngredientCost]
    total_cost: float = Field(description="Sum of matched ingredient costs in GBP")
    cost_per_serving: Optional[float] = None
    unmatched_count: int = Field(description="Number of ingredients with no match")


# --- Recipe Nutrition ---

class IngredientNutrition(BaseModel):
    """Nutrition data for a single matched ingredient."""
    ingredient: str
    matched_product: Optional[str] = None
    pack_size: Optional[str] = None
    nutrition_per_100g: Optional[NutritionPer100g] = None
    estimated_weight_g: Optional[float] = Field(None, description="Estimated weight used from recipe")
    estimated_kcal: Optional[float] = None
    estimated_protein_g: Optional[float] = None
    estimated_fat_g: Optional[float] = None
    estimated_carbs_g: Optional[float] = None

class RecipeNutritionResult(BaseModel):
    """Complete nutrition analysis for a recipe."""
    recipe_name: Optional[str] = None
    servings: Optional[float] = None
    ingredients: list[IngredientNutrition]
    total_kcal: Optional[float] = None
    total_protein_g: Optional[float] = None
    total_fat_g: Optional[float] = None
    total_carbs_g: Optional[float] = None
    per_serving_kcal: Optional[float] = None
    per_serving_protein_g: Optional[float] = None
    per_serving_fat_g: Optional[float] = None
    per_serving_carbs_g: Optional[float] = None
